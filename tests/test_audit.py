"""Tests for spiritwriter.audit — provenance, registry, verification."""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from pathlib import Path

import pytest

from spiritwriter.audit import (
    generate_witness,
    load_registry,
    trace_existing_audit,
    validate_report,
    verify,
    verify_l1,
    verify_l2,
)
from spiritwriter.audit.seed import seed


# ── Fixtures ────────────────────────────────────────────────────────

@pytest.fixture
def seeded_registry(tmp_path):
    db = tmp_path / "findings.db"
    return seed(db_path=db)


@pytest.fixture
def fake_apk(tmp_path):
    """Build a minimal APK-shaped zip + extraction dir with one DEX file.

    The DEX is just bytes containing ASCII strings — enough for the
    string-extraction stage to find evidence.
    """
    extraction = tmp_path / "com.example.app_extracted"
    extraction.mkdir()

    dex_strings = [
        b"com.google.firebase.analytics.FirebaseAnalytics",
        b"https://firebase-settings.crashlytics.com/spi/v2",
        b"com.google.android.gms.ads.identifier.AdvertisingIdClient",
        b"ca-app-pub-1234567890123456~1234567890",
    ]
    dex_content = b"DEX\x00" + b"\x00" * 64 + b"\x00".join(dex_strings) + b"\x00"
    (extraction / "classes.dex").write_bytes(dex_content)

    manifest = (
        b"\x03\x00\x08\x00"  # pretend binary-XML magic
        + b"android.permission.ACCESS_FINE_LOCATION"
        + b"\x00" * 8
        + b"android.permission.INTERNET"
    )
    (extraction / "AndroidManifest.xml").write_bytes(manifest)

    apk = tmp_path / "com.example.app.apk"
    with zipfile.ZipFile(apk, "w") as zf:
        zf.write(extraction / "classes.dex", "classes.dex")
        zf.write(extraction / "AndroidManifest.xml", "AndroidManifest.xml")

    return apk, extraction


@pytest.fixture
def fake_report(tmp_path, fake_apk):
    _, extraction = fake_apk
    report_path = tmp_path / "audits" / "MyCity" / "report.json"
    report_path.parent.mkdir(parents=True)
    report = {
        "target": "com.example.app",
        "platform": "custom",
        "audit_date": "2026-04-22",
        "findings": [
            {
                "name": "Firebase Analytics (Google Analytics for Firebase)",
                "category": "analytics",
                "risk": "medium",
                "evidence": [
                    {
                        "file": "classes.dex",
                        "match": "com.google.firebase.analytics.FirebaseAnalytics",
                    }
                ],
                "endpoints": ["https://firebase-settings.crashlytics.com/spi/v2"],
                "data_collected": ["device_id", "user_behavior"],
            },
            {
                "name": "Google AdMob / Play Services Ads Identifier",
                "category": "ads",
                "risk": "high",
                "evidence": [
                    {
                        "file": "classes.dex",
                        "match": "com.google.android.gms.ads.identifier.AdvertisingIdClient",
                    }
                ],
                "endpoints": [],
                "data_collected": ["advertising_id"],
            },
        ],
        "permissions": [
            {"name": "android.permission.ACCESS_FINE_LOCATION", "risk": "high"},
            {"name": "android.permission.INTERNET", "risk": "low"},
        ],
        "hardcoded_secrets": [],
        "summary": {
            "overall_risk_rating": "MEDIUM",
            "total_trackers_found": 2,
            "narrative": "Test fixture app with two commodity SDKs.",
        },
    }
    report_path.write_text(json.dumps(report, indent=2))
    return report_path


# ── Registry & validation ───────────────────────────────────────────

def test_seed_populates_registry(tmp_path):
    db = tmp_path / "findings.db"
    reg = seed(db_path=db)
    entities = list(reg.entities())
    assert len(entities) >= 20  # bundled list has 23 findings


def test_seed_is_idempotent(tmp_path):
    from spiritwriter.audit.registry import _parse_fields

    db = tmp_path / "findings.db"
    seed(db_path=db)
    reg = seed(db_path=db)  # re-seed same DB
    names = [_parse_fields(e)["finding_name"] for e in reg.entities()]
    assert len(names) == len(set(names))


def test_validate_clean_report(seeded_registry, fake_report):
    report = json.loads(fake_report.read_text())
    issues = validate_report(report, seeded_registry)
    # All findings in fake_report use canonical names with correct category/risk
    assert issues == []


def test_validate_unknown_finding(seeded_registry):
    report = {
        "findings": [
            {"name": "Totally Made Up SDK v9", "category": "analytics", "risk": "low"}
        ]
    }
    issues = validate_report(report, seeded_registry)
    assert len(issues) == 1
    assert issues[0]["issue"] == "unknown_finding"


def test_validate_category_mismatch(seeded_registry):
    report = {
        "findings": [
            {
                "name": "Firebase Analytics (Google Analytics for Firebase)",
                "category": "ads",  # canonical is "analytics"
                "risk": "medium",
            }
        ]
    }
    issues = validate_report(report, seeded_registry)
    cats = [i for i in issues if i["issue"] == "category_mismatch"]
    assert len(cats) == 1
    assert cats[0]["expected"] == "analytics"


# ── Provenance: end-to-end trace + witness ─────────────────────────

def test_trace_existing_audit_produces_verifiable_bundle(fake_apk, fake_report):
    apk, extraction = fake_apk
    trace_path, witness_path = trace_existing_audit(
        package_name="com.example.app",
        apk_path=apk,
        extraction_dir=extraction,
        report_path=fake_report,
    )
    assert trace_path.exists()
    assert witness_path.exists()

    # L1 + L2 pass on the freshly-generated bundle
    audit_dir = fake_report.parent
    assert verify_l1(audit_dir) == []
    assert verify_l2(audit_dir) == []


def test_trace_contains_expected_event_types(fake_apk, fake_report):
    apk, extraction = fake_apk
    trace_path, _ = trace_existing_audit(
        package_name="com.example.app",
        apk_path=apk,
        extraction_dir=extraction,
        report_path=fake_report,
    )
    events = [json.loads(line) for line in trace_path.read_text().splitlines() if line.strip()]
    types = [e["type"] for e in events]
    assert "audit_input_registered" in types
    assert "audit_evidence_extracted" in types
    assert "audit_strings_extracted" in types
    assert "audit_report_generated" in types
    # one finding_derived per finding in the fake report
    assert types.count("audit_finding_derived") == 2


def test_witness_self_hash_survives_roundtrip(fake_apk, fake_report):
    apk, extraction = fake_apk
    _, witness_path = trace_existing_audit(
        package_name="com.example.app",
        apk_path=apk,
        extraction_dir=extraction,
        report_path=fake_report,
    )
    witness = json.loads(witness_path.read_text())
    claimed = witness["witness_sha256"]
    witness["witness_sha256"] = None
    canon = json.dumps(witness, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    assert claimed == hashlib.sha256(canon.encode("utf-8")).hexdigest()


def test_trace_clears_stale_jsonl_before_emitting(fake_apk, fake_report):
    """Re-running must not append to a stale trace.jsonl (breaks chain)."""
    apk, extraction = fake_apk
    trace_existing_audit(
        package_name="com.example.app",
        apk_path=apk,
        extraction_dir=extraction,
        report_path=fake_report,
    )
    first_line_count = len(
        (fake_report.parent / "trace.jsonl").read_text().splitlines()
    )
    trace_existing_audit(
        package_name="com.example.app",
        apk_path=apk,
        extraction_dir=extraction,
        report_path=fake_report,
    )
    second_line_count = len(
        (fake_report.parent / "trace.jsonl").read_text().splitlines()
    )
    assert first_line_count == second_line_count


def test_tampered_report_breaks_l1(fake_apk, fake_report):
    apk, extraction = fake_apk
    trace_existing_audit(
        package_name="com.example.app",
        apk_path=apk,
        extraction_dir=extraction,
        report_path=fake_report,
    )
    # Tamper with the report after witness is generated
    report = json.loads(fake_report.read_text())
    report["findings"].append({"name": "Injected Finding", "category": "analytics"})
    fake_report.write_text(json.dumps(report, indent=2))

    issues = verify_l1(fake_report.parent)
    assert any("report.json hash mismatch" in i for i in issues)


# ── Bundled reference witnesses ─────────────────────────────────────

REFERENCE_WITNESSES_DIR = (
    Path(__file__).resolve().parent.parent / "examples" / "app_audit" / "witnesses"
)


@pytest.mark.parametrize(
    "relpath",
    [
        "NV/Lyon County",
        "NV/Douglas County",
        "FL/Monroe County",
        "FL/Franklin County",
        "GA/Coffee County",
        "GA/Floyd County",
        "GA/Liberty County",
        "TX/Chambers County",
    ],
)
def test_bundled_witness_l1_l2_pass(relpath):
    """Each shipped reference witness must verify L1+L2 cleanly."""
    audit_dir = REFERENCE_WITNESSES_DIR / relpath
    if not audit_dir.exists():
        pytest.skip(f"reference witness not present: {relpath}")
    assert verify_l1(audit_dir) == [], f"L1 failed for {relpath}"
    assert verify_l2(audit_dir) == [], f"L2 failed for {relpath}"
