"""Command-line entry point for spiritwriter.sw_vocab.

Usage::

    python -m spiritwriter.sw_vocab seed [--force] [--db-path PATH]
    python -m spiritwriter.sw_vocab validate PATH [PATH ...] [--fail-on=invented,deferred]

``seed`` populates ~/.spiritwriter/sw-vocab/terms.db from the bundled
canonical_terms.json. ``--force`` wipes and rebuilds (required to pick
up in-place edits to existing terms).

``validate`` runs the validator against one or more markdown files (or
directories — they're walked recursively for *.md). Exits 1 if any
issue of a type listed in ``--fail-on`` is found. Other issues are
reported but don't fail. Default is ``--fail-on=invented_term,deferred_term``
— the categories the substring scan exists to catch.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def _seed_cmd(args: argparse.Namespace) -> int:
    """Dispatch to spiritwriter.sw_vocab.seed."""
    from spiritwriter.sw_vocab.seed import seed, StaleMetadataError

    try:
        seed(args.db_path, verbose=True, force=args.force)
        return 0
    except StaleMetadataError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2
    except ValueError as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        return 2


def _expand_paths(paths: list[Path]) -> list[Path]:
    """Walk directories for *.md; pass through file paths unchanged."""
    out: list[Path] = []
    for p in paths:
        if p.is_dir():
            out.extend(sorted(p.rglob("*.md")))
        elif p.is_file():
            out.append(p)
        else:
            print(f"WARNING: path does not exist: {p}", file=sys.stderr)
    return out


def _validate_cmd(args: argparse.Namespace) -> int:
    """Validate one or more docs against the vocabulary registry."""
    from spiritwriter.sw_vocab import load_registry, validate_doc

    fail_on = {s.strip() for s in args.fail_on.split(",") if s.strip()}
    docs = _expand_paths(args.paths)
    if not docs:
        print("No documents to validate.", file=sys.stderr)
        return 1

    failing_files = 0
    informational_files = 0

    with load_registry(args.db_path) as registry:
        for doc in docs:
            try:
                issues = validate_doc(doc, registry)
            except Exception as e:
                print(f"{doc}: ERROR reading - {e}", file=sys.stderr)
                failing_files += 1
                continue

            failing = [i for i in issues if i["issue"] in fail_on]
            other = [i for i in issues if i["issue"] not in fail_on]

            if failing:
                print(f"{doc}: {len(failing)} blocking issue(s)")
                for i in failing:
                    canonical = i.get("canonical", "?")
                    print(f"  [{i['issue']:15s}] {i['term']!r} -> {canonical!r}")
                failing_files += 1
            elif other and args.verbose:
                print(f"{doc}: {len(other)} informational issue(s)")
                for i in other:
                    canonical = i.get("canonical", "?")
                    print(f"  [{i['issue']:15s}] {i['term']!r} -> {canonical!r}")
                informational_files += 1

    print()
    print(f"Scanned {len(docs)} document(s); "
          f"{failing_files} failed, "
          f"{informational_files} had informational issues only.")
    return 1 if failing_files else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(prog="python -m spiritwriter.sw_vocab")
    sub = parser.add_subparsers(dest="cmd", required=True)

    seed_p = sub.add_parser("seed", help="Seed the vocabulary registry.")
    seed_p.add_argument("--db-path", type=str, default=None, help="Path to terms.db")
    seed_p.add_argument(
        "--force", action="store_true",
        help="Wipe and rebuild the DB (required to pick up in-place edits).",
    )
    seed_p.set_defaults(func=_seed_cmd)

    val_p = sub.add_parser(
        "validate",
        help="Validate markdown docs against the vocabulary.",
    )
    val_p.add_argument("paths", nargs="+", type=Path,
                       help="Files or directories (directories walked for *.md).")
    val_p.add_argument(
        "--fail-on", default="invented_term,deferred_term",
        help="Comma-separated issue types that cause non-zero exit "
             "(default: invented_term,deferred_term).",
    )
    val_p.add_argument("--db-path", type=str, default=None, help="Path to terms.db")
    val_p.add_argument(
        "--verbose", "-v", action="store_true",
        help="Also report informational (non-failing) issues per doc.",
    )
    val_p.set_defaults(func=_validate_cmd)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
