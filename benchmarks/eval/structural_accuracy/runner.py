"""Structural accuracy harness — the rule vs. similarity scoring.

Answers one question with numbers: on records whose variation is
*structural* rather than textual, what can a similarity threshold
actually achieve, and what does canonicalization achieve?

The design is a paired comparison. Every pair is labelled by ground
truth — same structure or different structure — and then decided twice:

* **by rule** — compare canonical digests; equal means the same.
* **by score** — ``fuzzy_score`` over the recordings as written, merged
  when it clears a threshold. This is the baseline the rule is meant to
  replace, and it is given every chance: the reported best threshold is
  chosen *after* seeing the answers, which no deployed system could do.

Run::

    python -m benchmarks.eval.structural_accuracy.runner --corpus subject_rings
    python -m benchmarks.eval.structural_accuracy.runner --corpus all

Writes a timestamped directory under ``results/`` holding report.md
(citable summary), results.json (machine-readable), and pairs.tsv
(per-pair detail).
"""

from __future__ import annotations

import argparse
import json
import random
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from spiritwriter.fabric.canonicalize import fuzzy_score

from .structures import CORPORA, Structure, distinct_siblings, recordings

THRESHOLDS = [round(0.50 + 0.05 * i, 2) for i in range(11)]  # 0.50 … 1.00


@dataclass
class Pair:
    corpus: str
    kind: str
    relation: str  # "rotation" | "reflection" | "permutation" | "sibling"
    same: bool  # ground truth
    left: tuple[str, ...]
    right: tuple[str, ...]
    rule_same: bool
    score: float


def build_pairs(name: str, structures: list[Structure], seed: int) -> list[Pair]:
    rng = random.Random(seed)
    pairs: list[Pair] = []

    for s in structures:
        authored = tuple(s.members)
        base_digest = s.digest()

        # Positives: the same structure, written down differently.
        for rec in recordings(s, rng):
            if rec == authored:
                continue
            variant = Structure(s.id, s.kind, rec)
            if s.kind == "grouping":
                relation = "permutation"
            else:
                rotations = {
                    tuple(list(authored)[k:] + list(authored)[:k])
                    for k in range(len(authored))
                }
                relation = "rotation" if rec in rotations else "reflection"
            pairs.append(Pair(
                name, s.kind, relation, True, authored, rec,
                variant.digest() == base_digest,
                fuzzy_score(" ".join(authored), " ".join(rec)),
            ))

        # Hard negatives: same members, genuinely different structure.
        for sib in distinct_siblings(s, rng):
            pairs.append(Pair(
                name, s.kind, "sibling", False, authored, sib.members,
                sib.digest() == base_digest,
                fuzzy_score(" ".join(authored), " ".join(sib.members)),
            ))

    return pairs


def score_at(pairs: list[Pair], threshold: float) -> dict:
    pos = [p for p in pairs if p.same]
    neg = [p for p in pairs if not p.same]
    caught = sum(1 for p in pos if p.score >= threshold)
    merged = sum(1 for p in neg if p.score >= threshold)
    return {
        "threshold": threshold,
        "recall": caught / len(pos) if pos else 0.0,
        "caught": caught,
        "false_merges": merged,
        "false_merge_rate": merged / len(neg) if neg else 0.0,
    }


def summarize(pairs: list[Pair]) -> dict:
    pos = [p for p in pairs if p.same]
    neg = [p for p in pairs if not p.same]

    sweep = [score_at(pairs, t) for t in THRESHOLDS]
    # The best a threshold could do if you already knew the answers.
    best = max(sweep, key=lambda r: r["recall"] - r["false_merge_rate"])
    # The best that makes no wrong merges at all — the honest comparison,
    # since a system that mis-merges records is not a working system.
    clean = [r for r in sweep if r["false_merges"] == 0]
    best_clean = max(clean, key=lambda r: r["recall"]) if clean else None

    pos_scores = [p.score for p in pos]
    neg_scores = [p.score for p in neg]
    overlap = (
        sum(1 for n in neg_scores if n >= min(pos_scores))
        if pos_scores and neg_scores else 0
    )

    return {
        "pairs": len(pairs),
        "positives": len(pos),
        "negatives": len(neg),
        "rule": {
            "recall": sum(1 for p in pos if p.rule_same) / len(pos) if pos else 0.0,
            "settled": sum(1 for p in pos if p.rule_same),
            "false_merges": sum(1 for p in neg if p.rule_same),
        },
        "score_sweep": sweep,
        "best_threshold": best,
        "best_clean_threshold": best_clean,
        "separation": {
            "min_positive": min(pos_scores) if pos_scores else None,
            "max_negative": max(neg_scores) if neg_scores else None,
            "negatives_above_weakest_positive": overlap,
            "separable": bool(pos_scores and neg_scores
                              and min(pos_scores) > max(neg_scores)),
        },
    }


def write_report(out: Path, name: str, pairs: list[Pair], s: dict) -> None:
    sep, rule = s["separation"], s["rule"]
    lines = [
        f"# Structural accuracy — {name}",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        "",
        "Pairs of records whose variation is structural, not textual. Ground",
        "truth is known: a rotated ring *is* the same ring; a ring with two",
        "members swapped is *not*. Each pair is decided twice — by canonical",
        "digest, and by a similarity threshold over the text as written.",
        "",
        "## Headline",
        "",
        "| | Caught (of same-structure pairs) | Wrong merges |",
        "|---|---|---|",
        f"| **Rule** | {rule['settled']}/{s['positives']} "
        f"({rule['recall']:.0%}) | {rule['false_merges']} |",
    ]
    if s["best_clean_threshold"]:
        b = s["best_clean_threshold"]
        lines.append(
            f"| **Best threshold making no wrong merges** (t={b['threshold']}) | "
            f"{b['caught']}/{s['positives']} ({b['recall']:.0%}) | 0 |"
        )
    b = s["best_threshold"]
    lines += [
        f"| **Best threshold overall** (t={b['threshold']}, chosen knowing the "
        f"answers) | {b['caught']}/{s['positives']} ({b['recall']:.0%}) | "
        f"{b['false_merges']} |",
        "",
        "## Why no threshold works",
        "",
        f"- Weakest same-structure pair scores **{sep['min_positive']:.3f}**",
        f"- Strongest different-structure pair scores **{sep['max_negative']:.3f}**",
        f"- Different-structure pairs scoring at or above the weakest "
        f"same-structure pair: **{sep['negatives_above_weakest_positive']} "
        f"of {s['negatives']}**",
        "",
        "The classes are "
        + ("**separable**" if sep["separable"] else "**not separable**")
        + " by any threshold on this signal."
        + ("" if sep["separable"] else
           " Similarity is not merely a weak signal here — it is inverted."
           " Rewriting a structure changes the text a great deal, while"
           " altering the structure changes it hardly at all, so the score"
           " ranks the wrong pairs highest."),
        "",
        "## Threshold sweep",
        "",
        "| Threshold | Caught | Recall | Wrong merges |",
        "|---|---|---|---|",
    ]
    for r in s["score_sweep"]:
        lines.append(
            f"| {r['threshold']:.2f} | {r['caught']}/{s['positives']} | "
            f"{r['recall']:.0%} | {r['false_merges']}/{s['negatives']} |"
        )

    lines += ["", "## By relation", "", "| Relation | Pairs | Settled by rule | Mean score |", "|---|---|---|---|"]
    for rel in ("rotation", "reflection", "permutation", "sibling"):
        sub = [p for p in pairs if p.relation == rel]
        if not sub:
            continue
        settled = sum(1 for p in sub if p.rule_same)
        mean = sum(p.score for p in sub) / len(sub)
        expect = "" if sub[0].same else " *(must stay apart)*"
        lines.append(
            f"| {rel}{expect} | {len(sub)} | {settled} | {mean:.3f} |"
        )

    lines += [
        "",
        "## Reading this",
        "",
        "The rule's recall is 100% by construction — canonicalization is exact,",
        "and a result below 100% would be a bug, not a tuning problem. The",
        "number that carries information is the wrong-merge column: a rule that",
        "collapsed distinct structures would score perfect recall and be",
        "useless. Both must hold at once.",
        "",
        "This measures only structural variation. Textual drift — case,",
        "whitespace, typos, unicode — is the ESS harness's subject, and",
        "similarity scoring handles much of it well. The two are complementary;",
        "neither number belongs in the other's sentence.",
        "",
    ]
    (out / "report.md").write_text("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description="Structural accuracy harness")
    parser.add_argument("--corpus", default="subject_rings",
                        help=f"one of {', '.join(CORPORA)}, or 'all'")
    parser.add_argument("--seed", type=int, default=20260818)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    names = list(CORPORA) if args.corpus == "all" else [args.corpus]
    if args.corpus != "all" and args.corpus not in CORPORA:
        parser.error(f"unknown corpus {args.corpus!r}; have {', '.join(CORPORA)}")

    pairs: list[Pair] = []
    for n in names:
        pairs += build_pairs(n, CORPORA[n], args.seed)

    s = summarize(pairs)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    label = args.corpus
    out = Path(args.out) if args.out else (
        Path(__file__).parent / "results" / f"{label}-{stamp}"
    )
    out.mkdir(parents=True, exist_ok=True)

    (out / "results.json").write_text(json.dumps(
        {"corpus": label, "seed": args.seed, **s}, indent=2))
    with (out / "pairs.tsv").open("w") as fh:
        fh.write("corpus\tkind\trelation\tsame\trule_same\tscore\tleft\tright\n")
        for p in pairs:
            fh.write(f"{p.corpus}\t{p.kind}\t{p.relation}\t{p.same}\t"
                     f"{p.rule_same}\t{p.score:.4f}\t{' · '.join(p.left)}\t"
                     f"{' · '.join(p.right)}\n")
    write_report(out, label, pairs, s)

    rule, sep = s["rule"], s["separation"]
    print(f"corpus: {label}   pairs: {s['pairs']} "
          f"({s['positives']} same, {s['negatives']} different)")
    print(f"rule:  caught {rule['settled']}/{s['positives']}, "
          f"wrong merges {rule['false_merges']}")
    if s["best_clean_threshold"]:
        b = s["best_clean_threshold"]
        print(f"score: best threshold with no wrong merges (t={b['threshold']}) "
              f"caught {b['caught']}/{s['positives']}")
    print(f"separable by threshold: {sep['separable']}")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
