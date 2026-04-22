"""End-to-end app audit demo.

Takes an extracted APK + an existing report.json and produces a
trace.jsonl + witness.json, then verifies the result.

Usage::

    python examples/app_audit/run.py \\
        --package com.example.app \\
        --apk /tmp/audit_workspace/com.example.app.apk \\
        --extraction /tmp/audit_workspace/com.example.app_extracted \\
        --report ./audits/MyCity/report.json

See skills/audit/SKILL.md for the full workflow — this script runs
only Steps 4-5 (witness generation + verification) assuming a report
already exists. For the full agent-driven scan workflow, follow the
SKILL doc with your own subagent harness.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from spiritwriter.audit import trace_existing_audit, verify


def main() -> int:
    ap = argparse.ArgumentParser(description="Traced audit demo")
    ap.add_argument("--package", required=True, help="Android package name (e.g. com.example.app)")
    ap.add_argument("--apk", required=True, type=Path, help="Path to the APK file")
    ap.add_argument("--extraction", required=True, type=Path, help="Extracted APK directory")
    ap.add_argument("--report", required=True, type=Path, help="Existing report.json")
    ap.add_argument("--download-source", default="unknown", help="Where the APK was sourced from")
    args = ap.parse_args()

    print(f"== Generating trace + witness for {args.package} ==")
    trace_path, witness_path = trace_existing_audit(
        package_name=args.package,
        apk_path=args.apk,
        extraction_dir=args.extraction,
        report_path=args.report,
        download_source=args.download_source,
    )
    print(f"  trace:   {trace_path}")
    print(f"  witness: {witness_path}")

    print("\n== Verifying ==")
    results = verify(args.report.parent, args.apk, args.extraction)
    all_ok = True
    for level, issues in results.items():
        if issues:
            all_ok = False
            print(f"  {level}: FAIL ({len(issues)} issues)")
            for issue in issues:
                print(f"    - {issue}")
        else:
            print(f"  {level}: PASS")

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
