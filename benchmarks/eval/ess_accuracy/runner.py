"""CLI runner for the ESS accuracy harness.

Usage::

    python -m benchmarks.eval.ess_accuracy.runner --corpus people
    python -m benchmarks.eval.ess_accuracy.runner --corpus publications
    python -m benchmarks.eval.ess_accuracy.runner --corpus /path/to/your-domain

Produces an output directory under ``benchmarks/eval/ess_accuracy/results/``
containing report.md (citable summary), results.json (machine-readable
summary), pairs.tsv (per-pair detail for spreadsheet inspection).
"""

from __future__ import annotations

import argparse
import importlib.metadata
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from spiritwriter.fabric.canonicalize import CanonicalRegistry

from benchmarks.eval.ess_accuracy.corpus import load_corpus
from benchmarks.eval.ess_accuracy.metrics import (
    render_markdown,
    score,
    write_outputs,
)


_DEFAULT_RESULTS_ROOT = Path(__file__).parent / "results"


def _spiritwriter_version() -> str:
    try:
        return importlib.metadata.version("spiritwriter")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def run(
    corpus_arg: str,
    out_dir: Path | None = None,
    *,
    print_report: bool = True,
    allow_untrusted_mutations: bool = False,
) -> Path:
    """Run the harness against one corpus, write artifacts, return out_dir."""
    corpus = load_corpus(corpus_arg, allow_untrusted_mutations=allow_untrusted_mutations)

    # Always use a fresh temp registry — we don't want state from prior runs
    # bleeding into accuracy numbers. ignore_cleanup_errors is set because
    # on Windows SQLite's WAL/SHM sidecars sometimes linger past close().
    with tempfile.TemporaryDirectory(prefix="ess_eval_", ignore_cleanup_errors=True) as tmp:
        with CanonicalRegistry(Path(tmp) / "registry.db", corpus.schema) as registry:
            report = score(corpus, registry)

    report.spiritwriter_version = _spiritwriter_version()

    if out_dir is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = _DEFAULT_RESULTS_ROOT / f"{corpus.name}-{timestamp}"
    out_dir = Path(out_dir)
    write_outputs(report, out_dir)

    if print_report:
        print(render_markdown(report))
        print(f"\nArtifacts written to: {out_dir}")

    return out_dir


def main() -> None:
    # Reports contain Unicode (>=, <=, en/em dashes). Windows default cp1252
    # stdout chokes on these; force UTF-8 where possible.
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="ESS accuracy harness runner")
    parser.add_argument(
        "--corpus", required=True,
        help="Corpus short name (people, publications) or absolute path.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output directory (default: benchmarks/eval/ess_accuracy/results/<corpus>-<timestamp>)",
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress markdown report on stdout; only write artifacts.",
    )
    parser.add_argument(
        "--allow-untrusted-mutations", action="store_true",
        help="Opt in to loading mutations.py from corpora outside the "
             "shipped data/ tree. mutations.py executes arbitrary Python; "
             "only enable for corpora you trust.",
    )
    args = parser.parse_args()
    run(
        args.corpus, args.out, print_report=not args.quiet,
        allow_untrusted_mutations=args.allow_untrusted_mutations,
    )


if __name__ == "__main__":
    main()
