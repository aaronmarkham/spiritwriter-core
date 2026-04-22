"""Verify an audit's provenance chain at four levels.

L1: Document integrity   — witness, report, trace unmodified
L2: Chain integrity      — prev_event_hash linkage valid
L3: Evidence provenance  — file hashes match the actual APK extraction
L4: Finding derivability — evidence strings appear in the claimed files

L1-L2 work from committed files alone. L3-L4 require the APK and
extraction directory on disk.

Usage::

    python -m spiritwriter.audit.verify ./audits/MyCity
    python -m spiritwriter.audit.verify ./audits/MyCity --apk app.apk --extraction app_extracted
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from spiritwriter.audit._util import extract_dex_strings, sha256_file
from spiritwriter.fabric.emitter import verify_chain


def verify_l1(audit_dir: Path) -> list[str]:
    """L1: Document integrity — all hashes match."""
    issues: list[str] = []
    witness_path = audit_dir / "witness.json"
    report_path = audit_dir / "report.json"
    trace_path = audit_dir / "trace.jsonl"

    if not witness_path.exists():
        return ["witness.json not found"]
    if not report_path.exists():
        return ["report.json not found"]
    if not trace_path.exists():
        return ["trace.jsonl not found"]

    witness = json.loads(witness_path.read_text(encoding="utf-8"))

    claimed = witness["witness_sha256"]
    witness["witness_sha256"] = None
    canon = json.dumps(witness, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    actual = hashlib.sha256(canon.encode("utf-8")).hexdigest()
    if claimed != actual:
        issues.append(
            f"witness self-hash mismatch: claimed={claimed[:16]}... actual={actual[:16]}..."
        )
    witness["witness_sha256"] = claimed

    report_sha256 = sha256_file(report_path)
    if witness.get("report", {}).get("sha256") != report_sha256:
        issues.append("report.json hash mismatch")

    trace_sha256 = sha256_file(trace_path)
    if witness.get("trace_chain", {}).get("trace_sha256") != trace_sha256:
        issues.append("trace.jsonl hash mismatch")

    return issues


def verify_l2(audit_dir: Path) -> list[str]:
    """L2: Chain integrity — hash chain is valid."""
    issues: list[str] = []
    trace_path = audit_dir / "trace.jsonl"

    if not trace_path.exists():
        return ["trace.jsonl not found"]

    with open(trace_path, "r", encoding="utf-8") as f:
        events = [json.loads(line) for line in f if line.strip()]

    if not verify_chain(events):
        issues.append("hash chain verification failed — events may be tampered or reordered")

    witness_path = audit_dir / "witness.json"
    if witness_path.exists():
        witness = json.loads(witness_path.read_text(encoding="utf-8"))
        expected = witness.get("trace_chain", {}).get("event_count", 0)
        if len(events) != expected:
            issues.append(
                f"event count mismatch: witness says {expected}, trace has {len(events)}"
            )

    return issues


def verify_l3(audit_dir: Path, apk_path: Path, extraction_dir: Path) -> list[str]:
    """L3: Evidence provenance — APK and file hashes match."""
    issues: list[str] = []
    witness_path = audit_dir / "witness.json"

    if not witness_path.exists():
        return ["witness.json not found"]

    witness = json.loads(witness_path.read_text(encoding="utf-8"))

    if apk_path.exists():
        apk_sha256 = sha256_file(apk_path)
        expected = witness.get("input", {}).get("apk_sha256")
        if expected and apk_sha256 != expected:
            issues.append(
                f"APK hash mismatch: expected={expected[:16]}... actual={apk_sha256[:16]}..."
            )
    else:
        issues.append(f"APK not found: {apk_path}")

    file_hashes = witness.get("evidence", {}).get("file_hashes", {})
    for rel_path, expected_hash in file_hashes.items():
        full_path = extraction_dir / rel_path
        if not full_path.exists():
            issues.append(f"evidence file missing: {rel_path}")
            continue
        actual_hash = sha256_file(full_path)
        if actual_hash != expected_hash:
            issues.append(f"evidence file hash mismatch: {rel_path}")

    return issues


def verify_l4(audit_dir: Path, extraction_dir: Path) -> list[str]:
    """L4: Finding derivability — evidence strings found in claimed files."""
    issues: list[str] = []
    witness_path = audit_dir / "witness.json"

    if not witness_path.exists():
        return ["witness.json not found"]

    witness = json.loads(witness_path.read_text(encoding="utf-8"))

    params = witness.get("evidence", {}).get("dex_extraction_params", {})
    min_length = params.get("min_length", 8)

    all_strings: set[str] = set()
    for dex in sorted(extraction_dir.glob("classes*.dex")):
        all_strings.update(extract_dex_strings(dex, min_length))

    prop_strings: dict[str, set[str]] = {}
    for prop in extraction_dir.glob("*.properties"):
        content = prop.read_text(encoding="utf-8", errors="replace")
        prop_strings[prop.name] = set(content.splitlines())

    for finding in witness.get("findings", []):
        name = finding.get("name", "?")
        for ev_str in finding.get("evidence_strings", []):
            if not ev_str:
                continue
            found = ev_str in all_strings
            if not found:
                for _, plines in prop_strings.items():
                    if any(ev_str in line for line in plines):
                        found = True
                        break
            if not found and any(ev_str in s for s in all_strings):
                found = True
            if not found:
                issues.append(f"[{name}] evidence string not derivable: {ev_str[:80]}")

    return issues


def verify(
    audit_dir: str | Path,
    apk_path: str | Path | None = None,
    extraction_dir: str | Path | None = None,
) -> dict[str, list[str]]:
    """Run all applicable verification levels."""
    audit_dir = Path(audit_dir)
    results: dict[str, list[str]] = {}

    results["L1"] = verify_l1(audit_dir)
    results["L2"] = verify_l2(audit_dir)

    if apk_path and extraction_dir:
        results["L3"] = verify_l3(audit_dir, Path(apk_path), Path(extraction_dir))
        results["L4"] = verify_l4(audit_dir, Path(extraction_dir))

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify audit provenance")
    parser.add_argument(
        "audit_dir",
        help="Path to audit directory (contains report.json, trace.jsonl, witness.json)",
    )
    parser.add_argument("--apk", help="Path to the APK file (for L3 verification)")
    parser.add_argument(
        "--extraction", help="Path to the extracted APK directory (for L3/L4 verification)"
    )
    args = parser.parse_args()

    results = verify(args.audit_dir, args.apk, args.extraction)

    all_ok = True
    for level, issues in results.items():
        if issues:
            all_ok = False
            print(f"{level}: FAIL ({len(issues)} issues)")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print(f"{level}: PASS")

    if "L3" not in results:
        print("\nL3/L4: SKIPPED (provide --apk and --extraction for full verification)")

    if "L4" in results and results["L4"]:
        print("\nNote: L4 failures on retroactively witnessed reports are expected —")
        print("existing reports contain summarized evidence descriptions, not verbatim")
        print("extracted strings. Future audits with live trace instrumentation will")
        print("have exact string matches for full L4 verification.")

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
