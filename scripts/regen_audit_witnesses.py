"""Regenerate the bundled reference audit witnesses.

The witnesses under ``examples/app_audit/witnesses/<STATE>/<County>/`` ship
as reference artifacts for the L1/L2 verifiers in :mod:`spiritwriter.audit`.
Each witness claims sha256 hashes for its sibling ``report.json`` and
``trace.jsonl`` files; the L1 verifier checks those claims.

This script re-runs :func:`spiritwriter.audit.provenance.generate_witness`
against each (trace, report) pair and overwrites ``witness.json`` with a
fresh document whose claims match the actual file bytes. Useful when:

  - The bundled report/trace files were edited (linter, line endings, JSON
    re-formatter) without re-witnessing.
  - The witness format itself changed and old witnesses need to catch up.

Idempotent: running it twice produces witnesses that L1-verify either way.
Note that ``generated_at`` is wall-clock and ``witness_sha256`` depends on
all field values, so byte-level output differs run-to-run; only the
*verifier-relevant* fields are stable.

Run from the repo root::

    python scripts/regen_audit_witnesses.py
"""

from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WITNESS_ROOT = REPO_ROOT / "examples" / "app_audit" / "witnesses"


@contextmanager
def chdir(path: Path):
    """contextlib.chdir backport for cross-version compatibility.

    Calling generate_witness with bare relative paths keeps the
    ``report.path`` and ``trace_chain.trace_path`` strings in the
    written witness short and portable (just the filename), rather
    than encoding cwd-relative noise.
    """
    prev = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(prev)


def regen(witness_dir: Path) -> None:
    """Regenerate witness.json in `witness_dir` from its report + trace."""
    from spiritwriter.audit.provenance import generate_witness

    if not (witness_dir / "report.json").exists():
        raise FileNotFoundError(f"report.json missing in {witness_dir}")
    if not (witness_dir / "trace.jsonl").exists():
        raise FileNotFoundError(f"trace.jsonl missing in {witness_dir}")

    with chdir(witness_dir):
        witness = generate_witness(
            trace_path=Path("trace.jsonl"),
            report_path=Path("report.json"),
        )
        Path("witness.json").write_text(
            json.dumps(witness, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


def main() -> int:
    if not WITNESS_ROOT.exists():
        print(f"witness root not found: {WITNESS_ROOT}", file=sys.stderr)
        return 1

    witnesses = sorted(WITNESS_ROOT.glob("*/*/witness.json"))
    if not witnesses:
        print(f"no witnesses found under {WITNESS_ROOT}", file=sys.stderr)
        return 1

    for w in witnesses:
        rel = w.parent.relative_to(WITNESS_ROOT).as_posix()
        regen(w.parent)
        print(f"  regenerated {rel}")

    print(f"\nregenerated {len(witnesses)} witness(es).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
