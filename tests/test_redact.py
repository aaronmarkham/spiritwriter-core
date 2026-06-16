"""Tests for spiritwriter.audit.redact."""
from __future__ import annotations

import json
from pathlib import Path

from spiritwriter.audit.redact import (
    DETECT_ONLY_PATTERNS,
    redact_dir,
    redact_tree,
    residual_scan,
    scrub_text,
)
from spiritwriter.audit.verify import verify_l1, verify_l2

GOOGLE_KEY = "AIzaSyASZbllDc_ielyHaBZw120XkaOoeQ11n2Y"
COGNITO = "us-east-1:2793e471-e8b3-4dba-8270-d1a4192c0d17"
# A secret whose shape NO REDACT_PATTERN matches (Cognito user-pool client
# secret). Its structured value is fingerprinted by the catch-all, but it also
# appears in finding evidence, narrative, and trace free-text — which only the
# literal-value sweep removes.
CLIENT_SECRET = "1qofasclp7m472vf9pk3h35k60k0a6svd72v1hfegen098gu05vr"


def _make_audit(d: Path, pkg: str) -> None:
    """Write a minimal report.json + trace.jsonl carrying live secrets."""
    d.mkdir(parents=True, exist_ok=True)
    report = {
        "package_name": pkg,
        "permissions": ["android.permission.INTERNET"],
        "hardcoded_secrets": [
            {"type": "Google API Key (YouTube)", "value": GOOGLE_KEY, "risk": "medium",
             "notes": "Hardcoded in classes.dex. Extractable by any user; could be abused for quota theft."},
            {"type": "AWS Cognito Identity Pool ID", "value": COGNITO, "risk": "high",
             "notes": "Probe for misconfigured unauthenticated role to obtain temporary AWS credentials."},
            {"type": "AWS Cognito User Pool Client Secret", "value": CLIENT_SECRET, "risk": "high",
             "notes": "Client secret embedded in resources; AWS documents these must never ship in apps."},
        ],
        "findings": [
            {"name": "YouTube Data API v3 (Embedded Key)", "category": "analytics", "risk": "medium",
             "evidence": [{"file": "classes.dex",
                           "match": f"https://www.googleapis.com/youtube/v3/playlistItems?key={GOOGLE_KEY}"}]},
            {"name": "AWS Cognito", "category": "analytics", "risk": "high",
             "evidence": [{"file": "resources.arsc",
                           "match": f"User Pool Client Secret: {CLIENT_SECRET}"}]},
        ],
        "summary": {"overall_risk_rating": "high",
                    "narrative": f"Embeds {GOOGLE_KEY}, {COGNITO}, and client secret {CLIENT_SECRET}."},
    }
    (d / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    events = [
        {"type": "audit_input_registered", "run_id": f"audit-{pkg}", "event_id": "e1",
         "ts": "2026-01-01T00:00:00Z", "agent_id": "test", "prev_event_hash": None,
         "package_name": pkg, "apk_sha256": "a" * 64, "apk_size_bytes": 1, "download_source": "test"},
        {"type": "audit_finding_derived", "run_id": f"audit-{pkg}", "event_id": "e2",
         "ts": "2026-01-01T00:00:01Z", "agent_id": "test", "prev_event_hash": None,
         "finding_name": "YouTube Data API v3 (Embedded Key)", "category": "analytics", "risk": "medium",
         "evidence_strings": [f"https://www.googleapis.com/youtube/v3/playlistItems?key={GOOGLE_KEY}",
                              f"User Pool Client Secret: {CLIENT_SECRET}"],
         "evidence_files": ["classes.dex"], "evidence_file_hashes": {}},
        {"type": "audit_report_generated", "run_id": f"audit-{pkg}", "event_id": "e3",
         "ts": "2026-01-01T00:00:02Z", "agent_id": "test", "prev_event_hash": None,
         "report_path": "report.json", "report_sha256": "b" * 64, "finding_count": 1, "permission_count": 1},
    ]
    with open(d / "trace.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_scrub_text_fingerprints_and_is_deterministic():
    out = scrub_text(f"key={GOOGLE_KEY}")
    assert GOOGLE_KEY not in out
    assert "<REDACTED:google_api_key" in out
    assert scrub_text(GOOGLE_KEY) == scrub_text(GOOGLE_KEY)  # deterministic


def test_bare_host_endpoint_redacted_without_scheme():
    # Host-based identifiers redact with OR without an https:// scheme, so a
    # bare mention in prose is caught too.
    bare = scrub_text("intermediated via abc123.execute-api.us-east-1.amazonaws.com /dev stage")
    assert "abc123.execute-api" not in bare
    assert "<REDACTED:aws_api_gateway" in bare
    full = scrub_text("https://abc123.execute-api.us-east-1.amazonaws.com/dev")
    assert "<REDACTED:aws_api_gateway" in full


def test_redact_dir_clean_and_verifiable(tmp_path: Path):
    src = tmp_path / "src" / "App"
    _make_audit(src, "com.test.app")
    dst = tmp_path / "out" / "App"
    res = redact_dir(src, dst)

    assert res["chain_ok"] is True
    # No live secrets anywhere in the output.
    for name in ("report.json", "trace.jsonl", "witness.json"):
        body = (dst / name).read_text(encoding="utf-8")
        assert GOOGLE_KEY not in body
        assert COGNITO not in body
        # Non-patterned secret must be gone from evidence/narrative/trace too.
        assert CLIENT_SECRET not in body
    # Project verifier accepts the redacted bundle.
    assert verify_l1(dst) == []
    assert verify_l2(dst) == []
    # Exploit framing neutralized.
    report = json.loads((dst / "report.json").read_text())
    assert "quota theft" not in json.dumps(report)
    assert "temporary AWS credentials" not in json.dumps(report)


def test_same_value_same_fingerprint_across_dirs(tmp_path: Path):
    _make_audit(tmp_path / "src" / "A", "com.test.a")
    _make_audit(tmp_path / "src" / "B", "com.test.b")
    summary = redact_tree(tmp_path / "src", tmp_path / "out")
    assert summary["safe"] is True

    def yt_token(app: str) -> str:
        r = json.loads((tmp_path / "out" / app / "report.json").read_text())
        return r["hardcoded_secrets"][0]["value"]

    assert yt_token("A") == yt_token("B")  # shared-key signal preserved


def test_no_structured_secret_value_survives_verbatim(tmp_path: Path):
    # Every value the audit flagged as a hardcoded secret must be absent,
    # verbatim, from the entire redacted bundle — regardless of whether a
    # REDACT_PATTERN knows its shape.
    src_root = tmp_path / "src"
    _make_audit(src_root / "App", "com.test.app")
    out = tmp_path / "out"
    summary = redact_tree(src_root, out)
    assert summary["safe"] is True

    report = json.loads((src_root / "App" / "report.json").read_text(encoding="utf-8"))
    values = [s["value"] for s in report["hardcoded_secrets"]]
    blob = "".join(
        (out / "App" / name).read_text(encoding="utf-8")
        for name in ("report.json", "trace.jsonl", "witness.json")
    )
    for v in values:
        assert v not in blob, f"secret survived verbatim: {v!r}"


def test_preserved_evidence_hash_not_corrupted(tmp_path: Path):
    # A short secret value must never be redacted out of the middle of a
    # longer preserved hash (lookaround boundaries protect L3 evidence hashes).
    src_root = tmp_path / "src"
    _make_audit(src_root / "App", "com.test.app")
    out = tmp_path / "out"
    redact_tree(src_root, out)
    trace = (out / "App" / "trace.jsonl").read_text(encoding="utf-8")
    assert "a" * 64 in trace  # apk_sha256 preserved intact


def test_residual_gate_flags_unhandled_class(tmp_path: Path):
    # A secret class the redactor does NOT auto-handle must trip the gate.
    leaky = tmp_path / "leak.txt"
    leaky.write_text("aws_key=AKIAIOSFODNN7EXAMPLE\n", encoding="utf-8")
    findings = residual_scan(tmp_path)
    assert any(cls.startswith("unhandled:aws_access_key_id") for _, cls, _ in findings)
