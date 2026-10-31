"""Audit provenance — trace chain emission and witness generation.

Emits hash-chained trace events for each audit step, then materializes
the chain into a self-hashing witness.json that binds the APK, extracted
evidence, findings, and report together.

Events:
  audit_input_registered    — APK hash, size, download source
  audit_evidence_extracted  — SHA-256 of every extracted file (DEX, manifest, .so)
  audit_strings_extracted   — DEX string corpus hash + extraction params
  audit_finding_derived     — per-finding evidence binding (one per finding)
  audit_report_generated    — final report hash + counts
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spiritwriter.audit._util import (
    dex_string_pattern,
    extract_dex_strings,
    sha256_bytes,
    sha256_file,
)
from spiritwriter.fabric.emitter import (
    TraceChainError,
    TraceEmitter,
    generate_trace_receipt,
    verify_chain,
)


class AuditWitnessError(ValueError):
    """An audit trace does not strictly bind its terminal report."""


def _canonical_json_str(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def validate_audit_terminal(
    events: list[dict[str, Any]],
    report_bytes: bytes,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the unique final report event against exact report bytes."""
    if not events or not verify_chain(events):
        raise AuditWitnessError("audit trace must be a non-empty valid hash chain")

    terminal_events = [
        event for event in events if event.get("type") == "audit_report_generated"
    ]
    if len(terminal_events) != 1:
        raise AuditWitnessError(
            "audit trace must contain exactly one audit_report_generated event"
        )
    terminal = terminal_events[0]
    if terminal is not events[-1]:
        raise AuditWitnessError("audit_report_generated must be the final trace event")

    try:
        report = json.loads(report_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditWitnessError(f"report is not valid JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise AuditWitnessError("report JSON must be an object")

    actual_sha256 = sha256_bytes(report_bytes)
    if terminal.get("report_sha256") != actual_sha256:
        raise AuditWitnessError("terminal report hash does not match report bytes")

    for field, report_field in (
        ("finding_count", "findings"),
        ("permission_count", "permissions"),
    ):
        value = report.get(report_field, [])
        if not isinstance(value, list):
            raise AuditWitnessError(f"report field {report_field!r} must be a list")
        committed = terminal.get(field)
        if type(committed) is not int or committed != len(value):
            raise AuditWitnessError(
                f"terminal {field} does not match report {report_field} count"
            )

    return terminal, report


# ── Trace emission helpers ──────────────────────────────────────────

def emit_audit_input(
    emitter: TraceEmitter,
    package_name: str,
    apk_path: str | Path,
    download_source: str = "unknown",
) -> dict:
    """Event 1: Register the input APK with its hash."""
    apk_path = Path(apk_path)
    return emitter.emit(
        "audit_input_registered",
        package_name=package_name,
        apk_sha256=sha256_file(apk_path),
        apk_size_bytes=apk_path.stat().st_size,
        download_source=download_source,
    )


def emit_audit_evidence(
    emitter: TraceEmitter,
    extraction_dir: str | Path,
    patterns: list[str] | None = None,
) -> dict:
    """Event 2: Hash all audit-relevant files in the extraction directory."""
    extraction_dir = Path(extraction_dir)
    if patterns is None:
        patterns = [
            "classes*.dex",
            "AndroidManifest.xml",
            "*.properties",
            "resources.arsc",
            "lib/**/*.so",
        ]

    file_hashes: dict[str, str] = {}
    for pattern in patterns:
        for path in extraction_dir.glob(pattern):
            if path.is_file():
                rel = str(path.relative_to(extraction_dir)).replace("\\", "/")
                file_hashes[rel] = sha256_file(path)

    return emitter.emit(
        "audit_evidence_extracted",
        file_hashes=file_hashes,
        total_files=len(file_hashes),
    )


def emit_audit_strings(
    emitter: TraceEmitter,
    extraction_dir: str | Path,
    min_length: int = 8,
) -> tuple[dict, list[str]]:
    """Event 3: Extract and hash the DEX string corpus.

    Returns (trace_event, sorted_strings) so the caller can use the
    strings for finding derivation without re-extracting.
    """
    extraction_dir = Path(extraction_dir)
    dex_files = sorted(extraction_dir.glob("classes*.dex"))

    all_strings: set[str] = set()
    dex_names: list[str] = []
    for dex in dex_files:
        dex_names.append(dex.name)
        all_strings.update(extract_dex_strings(dex, min_length))

    sorted_strings = sorted(all_strings)
    corpus = "\n".join(sorted_strings).encode("utf-8")
    corpus_sha256 = sha256_bytes(corpus)

    event = emitter.emit(
        "audit_strings_extracted",
        dex_files=dex_names,
        corpus_sha256=corpus_sha256,
        total_strings=len(sorted_strings),
        extraction_params={
            "min_length": min_length,
            "pattern": dex_string_pattern(min_length).decode("ascii"),
        },
    )
    return event, sorted_strings


def emit_audit_finding(
    emitter: TraceEmitter,
    finding: dict[str, Any],
    evidence_file_hashes: dict[str, str],
) -> dict:
    """Event 4: Record a single finding with its evidence binding."""
    return emitter.emit(
        "audit_finding_derived",
        finding_name=finding["name"],
        category=finding.get("category", "unknown"),
        risk=finding.get("risk", "unknown"),
        evidence_strings=[e.get("match", "") for e in finding.get("evidence", [])],
        evidence_files=[e.get("file", "") for e in finding.get("evidence", [])],
        evidence_file_hashes=evidence_file_hashes,
    )


def emit_audit_report(
    emitter: TraceEmitter,
    report_path: str | Path,
    report: dict[str, Any],
) -> dict:
    """Event 5: Commit the exact report bytes and their derived counts."""
    report_path = Path(report_path)
    report_bytes = report_path.read_bytes()
    try:
        report_on_disk = json.loads(report_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditWitnessError(f"report is not valid JSON: {exc}") from exc
    if not isinstance(report_on_disk, dict):
        raise AuditWitnessError("report JSON must be an object")
    if report_on_disk != report:
        raise AuditWitnessError("report argument does not match report_path contents")

    findings = report_on_disk.get("findings", [])
    permissions = report_on_disk.get("permissions", [])
    if not isinstance(findings, list) or not isinstance(permissions, list):
        raise AuditWitnessError("report findings and permissions must be lists")

    return emitter.emit(
        "audit_report_generated",
        report_path=str(report_path),
        report_sha256=sha256_bytes(report_bytes),
        finding_count=len(findings),
        permission_count=len(permissions),
    )


# ── Witness generation ──────────────────────────────────────────────

def generate_witness(
    trace_path: str | Path,
    report_path: str | Path,
) -> dict[str, Any]:
    """Generate a witness.json from a completed trace chain and report.

    Reads trace events, extracts structured proof data, and assembles
    a self-hashing witness document.
    """
    trace_path = Path(trace_path)
    report_path = Path(report_path)

    trace_bytes = trace_path.read_bytes()
    try:
        events = [
            json.loads(line)
            for line in trace_bytes.splitlines()
            if line.strip()
        ]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AuditWitnessError(f"trace is not valid JSONL: {exc}") from exc
    if any(not isinstance(event, dict) for event in events):
        raise AuditWitnessError("every trace line must be a JSON object")

    report_bytes = report_path.read_bytes()
    terminal_report_evt, report = validate_audit_terminal(events, report_bytes)
    try:
        trace_receipt = generate_trace_receipt(
            trace_path,
            {"report": report_path},
            stream_id=terminal_report_evt.get("run_id"),
        )
    except TraceChainError as exc:  # includes TraceReceiptError
        raise AuditWitnessError(f"failed to generate trace receipt: {exc}") from exc
    # Both files are read once above for audit fields and once by the generic
    # receipt. Refuse a mixed snapshot if either changed between those reads.
    if trace_receipt["trace_sha256"] != sha256_bytes(trace_bytes):
        raise AuditWitnessError("trace changed while witness was generated")
    if trace_receipt["terminal_artifact_hashes"]["report"] != sha256_bytes(report_bytes):
        raise AuditWitnessError("report changed while witness was generated")

    input_evt = next((e for e in events if e["type"] == "audit_input_registered"), None)
    evidence_evt = next((e for e in events if e["type"] == "audit_evidence_extracted"), None)
    strings_evt = next((e for e in events if e["type"] == "audit_strings_extracted"), None)
    finding_evts = [e for e in events if e["type"] == "audit_finding_derived"]

    witness: dict[str, Any] = {
        "witness_version": "1.1",
        "witness_sha256": None,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trace_receipt": trace_receipt,
    }

    if input_evt:
        witness["input"] = {
            "package_name": input_evt.get("package_name"),
            "apk_sha256": input_evt.get("apk_sha256"),
            "apk_size_bytes": input_evt.get("apk_size_bytes"),
            "download_source": input_evt.get("download_source"),
        }

    if evidence_evt:
        witness["evidence"] = {
            "extraction_file_count": evidence_evt.get("total_files", 0),
            "file_hashes": evidence_evt.get("file_hashes", {}),
        }
    if strings_evt:
        witness.setdefault("evidence", {}).update(
            {
                "dex_corpus_sha256": strings_evt.get("corpus_sha256"),
                "dex_string_count": strings_evt.get("total_strings", 0),
                "dex_extraction_params": strings_evt.get("extraction_params", {}),
            }
        )

    witness["findings"] = [
        {
            "name": fe.get("finding_name"),
            "category": fe.get("category"),
            "risk": fe.get("risk"),
            "evidence_strings": fe.get("evidence_strings", []),
            "evidence_files": fe.get("evidence_files", []),
            "derivation_event_hash": fe.get("hash"),
        }
        for fe in finding_evts
    ]

    witness["permissions"] = report.get("permissions", [])
    witness["hardcoded_secrets"] = report.get("hardcoded_secrets", [])

    witness["report"] = {
        "path": str(report_path),
        "sha256": terminal_report_evt["report_sha256"],
        "finding_count": terminal_report_evt["finding_count"],
        "permission_count": terminal_report_evt["permission_count"],
        "hardcoded_secret_count": len(report.get("hardcoded_secrets", [])),
        "overall_risk_rating": report.get("summary", {}).get("overall_risk_rating"),
        "narrative": report.get("summary", {}).get("narrative"),
    }

    witness["trace_chain"] = {
        "trace_path": str(trace_path),
        "trace_sha256": trace_receipt["trace_sha256"],
        "trace_bytes": trace_receipt["trace_bytes"],
        "stream_id": trace_receipt["stream_id"],
        "event_count": trace_receipt["event_count"],
        "first_event_hash": trace_receipt["first_event_hash"],
        "last_event_hash": trace_receipt["last_event_hash"],
        "chain_intact": True,
    }

    witness["agent"] = {
        "agent_id": events[0].get("agent_id"),
        "run_id": trace_receipt["stream_id"],
    }

    canonical = _canonical_json_str(witness)
    witness["witness_sha256"] = sha256_bytes(canonical.encode("utf-8"))

    return witness


# ── Full traced audit of an existing report ─────────────────────────

def trace_existing_audit(
    package_name: str,
    apk_path: str | Path,
    extraction_dir: str | Path,
    report_path: str | Path,
    output_dir: str | Path | None = None,
    agent_id: str = "audit-provenance",
    download_source: str = "unknown",
) -> tuple[Path, Path]:
    """Run a full trace chain on an already-completed audit.

    Reads the existing report.json, hashes the APK and extracted files,
    emits trace events for each finding, and generates a witness.

    Returns (trace_path, witness_path).
    """
    apk_path = Path(apk_path)
    extraction_dir = Path(extraction_dir)
    report_path = Path(report_path)

    if output_dir is None:
        output_dir = report_path.parent
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    trace_path = output_dir / "trace.jsonl"
    witness_path = output_dir / "witness.json"

    # The emitter appends, so clear any stale trace first
    if trace_path.exists():
        trace_path.unlink()

    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    emitter = TraceEmitter(
        run_id=f"audit-{package_name}-{today}",
        agent_id=agent_id,
        out_path=str(trace_path),
    )

    emit_audit_input(emitter, package_name, apk_path, download_source=download_source)
    emit_audit_evidence(emitter, extraction_dir)

    evts = emitter.get_events()
    evidence_data = next((e for e in evts if e["type"] == "audit_evidence_extracted"), {})
    file_hashes = evidence_data.get("file_hashes", {})

    emit_audit_strings(emitter, extraction_dir)

    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
    for finding in report.get("findings", []):
        evidence_fh = {}
        for ev in finding.get("evidence", []):
            fname = ev.get("file", "")
            if fname in file_hashes:
                evidence_fh[fname] = file_hashes[fname]
        emit_audit_finding(emitter, finding, evidence_fh)

    emit_audit_report(emitter, report_path, report)

    witness = generate_witness(trace_path, report_path)
    with open(witness_path, "w", encoding="utf-8") as f:
        json.dump(witness, f, indent=2)

    return trace_path, witness_path
