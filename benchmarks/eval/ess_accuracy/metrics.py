"""Metrics + report generation for the ESS accuracy harness.

Headline metrics, each tied to a claim in the spec doc:

  recall_t1_t2        — fraction of same-entity pairs auto-merged
                        (T1_EXACT or T2_STRONG). Target ≥ 0.85
                        per cmc-spec-v0.1.md.
  recall_any_tier     — fraction resolved at ANY tier above NO_MATCH.
  false_merge_rate    — fraction of different-entity pairs incorrectly
                        auto-merged at T1/T2. Target ≤ 0.05.
  jaccard_match_rate  — fraction of same-entity pairs that a Jaccard
                        baseline considers matches. The arc this
                        defends: "~33% → 80–100% consistency".
  per_tier_calibration— actual same-entity rate at each tier; should
                        approximate stated confidence values.
  per_family_recall   — recall split by mutation family. Lets a reviewer
                        attribute results to specific drift modes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry,
    CanonicalSchema,
    ResolutionTier,
)

from benchmarks.eval.ess_accuracy.baselines import jaccard_matches, jaccard_score
from benchmarks.eval.ess_accuracy.corpus import Corpus
from benchmarks.eval.ess_accuracy.mutations import Mutation


# Auto-merge tiers (the registry's auto-action set)
_AUTO_MERGE = {ResolutionTier.T1_EXACT.value, ResolutionTier.T2_STRONG.value}
_RESOLVED_ABOVE_NO_MATCH = {
    ResolutionTier.T1_EXACT.value,
    ResolutionTier.T2_STRONG.value,
    ResolutionTier.T3_FUZZY.value,
    ResolutionTier.T4_WEAK.value,
}


@dataclass
class PairResult:
    """One mutation pair after evaluation."""
    family: str
    canonical: dict
    mutated: dict
    same_entity: bool                  # ground truth
    ess_tier: str                      # registry's resolution tier value
    ess_confidence: float
    jaccard_score: float
    jaccard_matches: bool
    expected_tier_min: str | None
    notes: str = ""


@dataclass
class AccuracyReport:
    """Summary numbers + raw pairs for the run."""
    corpus_name: str
    schema_name: str
    n_entities: int
    n_pairs: int
    n_same: int
    n_different: int

    recall_t1: float
    recall_t1_t2: float
    recall_any_tier: float
    false_merge_rate: float
    jaccard_match_rate: float
    jaccard_false_merge_rate: float
    ess_minus_jaccard: float
    jaccard_fields: list[str] = field(default_factory=list)

    per_tier_calibration: dict[str, dict[str, Any]] = field(default_factory=dict)
    per_family: dict[str, dict[str, Any]] = field(default_factory=dict)

    pairs: list[PairResult] = field(default_factory=list)

    # Provenance
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    spiritwriter_version: str = ""

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        # Pairs already serialized via asdict; nothing further needed.
        return d


_JACCARD_THRESHOLD = 0.80


def score(corpus: Corpus, registry: CanonicalRegistry) -> AccuracyReport:
    """Generate all mutations, resolve each against the registry, score."""
    schema = corpus.schema
    ess_fields = list(schema.ess_fields)
    # Baseline tokenization: per-corpus override, else all ess_fields.
    # Override exists to exclude strong-anchor fields (DOB, SSN) that
    # would make Jaccard trivially permissive on structured records.
    jaccard_fields = corpus.jaccard_fields or ess_fields

    # Seed the registry with the canonical entities.
    for entity in corpus.entities:
        result = registry.resolve(entity)
        registry.upsert(entity, result, "eval_seed", _entity_key(entity, ess_fields))

    # Generate mutations from every family across every entity.
    mutations: list[Mutation] = []
    for entity in corpus.entities:
        for family in corpus.families:
            mutations.extend(family.generate(entity, ess_fields))

    # Silent-degradation guard: a registered *per-corpus* family that
    # produces ZERO mutations across all entities is almost always a
    # bug — typically a hand-curated collision-pair dict whose keys no
    # longer match entities.json after an edit. Universal families
    # (case, whitespace, unicode_normalization, etc.) can legitimately
    # no-op on a corpus that doesn't exercise their drift mode
    # (e.g. unicode_normalization on a corpus with no diacritics), so
    # we only warn for non-universal families.
    from benchmarks.eval.ess_accuracy.mutations import UNIVERSAL_FAMILIES
    _UNIVERSAL_NAMES = {f.name for f in UNIVERSAL_FAMILIES}
    family_counts: dict[str, int] = {f.name: 0 for f in corpus.families}
    for m in mutations:
        family_counts[m.family] = family_counts.get(m.family, 0) + 1
    empty_per_corpus_families = [
        name for name, n in family_counts.items()
        if n == 0 and name not in _UNIVERSAL_NAMES
    ]
    if empty_per_corpus_families:
        import warnings
        warnings.warn(
            f"Corpus {corpus.name!r}: per-corpus families produced ZERO "
            f"mutations across all entities — likely a stale "
            f"hand-curated dict (collision pairs, diminutives, etc.) "
            f"whose keys no longer match entities.json. "
            f"Affected families: {empty_per_corpus_families}",
            stacklevel=2,
        )

    pair_results: list[PairResult] = []
    for m in mutations:
        ess_result = registry.resolve(m.mutated)
        j_score = jaccard_score(m.mutated, m.canonical, jaccard_fields)
        j_match = j_score >= _JACCARD_THRESHOLD
        pair_results.append(PairResult(
            family=m.family,
            canonical=m.canonical,
            mutated=m.mutated,
            same_entity=m.same_entity,
            ess_tier=ess_result.tier.value,
            ess_confidence=ess_result.confidence,
            jaccard_score=j_score,
            jaccard_matches=j_match,
            expected_tier_min=m.expected_tier_min,
            notes=m.notes,
        ))

    return _summarize(corpus, pair_results, jaccard_fields)


def _summarize(
    corpus: Corpus,
    pairs: list[PairResult],
    jaccard_fields: list[str],
) -> AccuracyReport:
    same_pairs = [p for p in pairs if p.same_entity]
    diff_pairs = [p for p in pairs if not p.same_entity]

    n_same = len(same_pairs)
    n_diff = len(diff_pairs)

    recall_t1 = _frac(same_pairs, lambda p: p.ess_tier == ResolutionTier.T1_EXACT.value)
    recall_t1_t2 = _frac(same_pairs, lambda p: p.ess_tier in _AUTO_MERGE)
    recall_any = _frac(same_pairs, lambda p: p.ess_tier in _RESOLVED_ABOVE_NO_MATCH)
    false_merge = _frac(diff_pairs, lambda p: p.ess_tier in _AUTO_MERGE)
    jaccard_rate = _frac(same_pairs, lambda p: p.jaccard_matches)
    jaccard_fm = _frac(diff_pairs, lambda p: p.jaccard_matches)

    per_tier = _per_tier_calibration(pairs)
    per_family = _per_family_breakdown(pairs)

    return AccuracyReport(
        corpus_name=corpus.name,
        schema_name=corpus.schema.name,
        n_entities=len(corpus.entities),
        n_pairs=len(pairs),
        n_same=n_same,
        n_different=n_diff,
        recall_t1=recall_t1,
        recall_t1_t2=recall_t1_t2,
        recall_any_tier=recall_any,
        false_merge_rate=false_merge,
        jaccard_match_rate=jaccard_rate,
        jaccard_false_merge_rate=jaccard_fm,
        ess_minus_jaccard=recall_t1_t2 - jaccard_rate,
        jaccard_fields=jaccard_fields,
        per_tier_calibration=per_tier,
        per_family=per_family,
        pairs=pairs,
    )


# ── Helpers ─────────────────────────────────────────────────────────


def _frac(rows: list[PairResult], pred) -> float:
    if not rows:
        return 0.0
    return sum(1 for r in rows if pred(r)) / len(rows)


def _per_tier_calibration(pairs: list[PairResult]) -> dict[str, dict[str, Any]]:
    """Stated vs actual correctness for each tier the registry produced."""
    stated = {
        "t1_exact": 0.95,
        "t2_strong": 0.85,
        "t3_fuzzy": 0.70,
        "t4_weak": 0.50,
        "no_match": 0.0,
    }
    out: dict[str, dict[str, Any]] = {}
    for tier_value, stated_conf in stated.items():
        bucket = [p for p in pairs if p.ess_tier == tier_value]
        if not bucket:
            continue
        # Actual precision = fraction of pairs in this tier that ARE same-entity.
        if tier_value == "no_match":
            # For NO_MATCH, "correct" means same_entity is False (it's a
            # rejection). Report the negative-predictive-value instead.
            correct = sum(1 for p in bucket if not p.same_entity)
        else:
            correct = sum(1 for p in bucket if p.same_entity)
        out[tier_value] = {
            "n": len(bucket),
            "stated_confidence": stated_conf,
            "actual_precision": correct / len(bucket),
        }
    return out


def _per_family_breakdown(pairs: list[PairResult]) -> dict[str, dict[str, Any]]:
    """Recall + tier distribution per mutation family."""
    families = sorted({p.family for p in pairs})
    out: dict[str, dict[str, Any]] = {}
    for fam in families:
        bucket = [p for p in pairs if p.family == fam]
        same = [p for p in bucket if p.same_entity]
        diff = [p for p in bucket if not p.same_entity]
        out[fam] = {
            "n": len(bucket),
            "n_same": len(same),
            "n_different": len(diff),
            "recall_t1_t2": _frac(same, lambda p: p.ess_tier in _AUTO_MERGE),
            "recall_any_tier": _frac(
                same, lambda p: p.ess_tier in _RESOLVED_ABOVE_NO_MATCH
            ),
            "false_merge_rate": _frac(diff, lambda p: p.ess_tier in _AUTO_MERGE),
            "tier_distribution": _tier_distribution(bucket),
        }
    return out


def _tier_distribution(pairs: list[PairResult]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in pairs:
        out[p.ess_tier] = out.get(p.ess_tier, 0) + 1
    return out


def _entity_key(entity: dict, ess_fields: list[str]) -> str:
    return "|".join(str(entity.get(f, "")) for f in ess_fields)


# ── Report rendering ────────────────────────────────────────────────


def render_markdown(report: AccuracyReport) -> str:
    """Human-readable summary suitable for the citable artifact.

    Pass/fail targets in the headline are deliberately narrow — only the
    invariants CMC-Lite is supposed to guarantee. Bulk recall numbers are
    informational, not pass/fail, because choosing the recall metric to
    declare "PASS" against the spec's 85% number would be exactly the
    metric-shopping this harness exists to prevent.
    """
    # CMC-Lite's actual invariants (what we can defend as pass/fail):
    #   1. No false auto-merges (≤5% — directly from cmc-spec false-merge target)
    #   2. ESS auto-merge precision = 1.0 (no incorrect T1/T2 verdicts)
    #
    # The cmc-spec's "≥85% recall on semantic duplicates" target describes
    # the *full CMC pipeline* (LLM-clustering stage included). CMC-Lite is
    # the deterministic subset; it does not claim 85% recall on its own.
    # Recall numbers below are reported as informational; do not interpret
    # them as defending the cmc-spec recall target.
    target_false_merge = 0.05
    target_auto_merge_precision = 1.00

    # ESS auto-merge precision: among pairs we DID auto-merge, what
    # fraction were actually same-entity?
    n_ess_tp = sum(1 for p in report.pairs if p.same_entity and p.ess_tier in _AUTO_MERGE)
    n_ess_fp = sum(1 for p in report.pairs if not p.same_entity and p.ess_tier in _AUTO_MERGE)
    ess_auto_precision = n_ess_tp / (n_ess_tp + n_ess_fp) if (n_ess_tp + n_ess_fp) else 1.0

    fm_pass = "PASS" if report.false_merge_rate <= target_false_merge else "FAIL"
    prec_pass = "PASS" if ess_auto_precision >= target_auto_merge_precision else "FAIL"

    lines: list[str] = []
    lines.append(f"# ESS Accuracy Report — {report.generated_at}")
    lines.append(f"corpus: **{report.corpus_name}** · "
                 f"schema: `{report.schema_name}` · "
                 f"entities: {report.n_entities} · "
                 f"pairs: {report.n_pairs} ({report.n_same} same, {report.n_different} different)")
    if report.spiritwriter_version:
        lines.append(f"spiritwriter {report.spiritwriter_version}")
    if report.jaccard_fields:
        lines.append(f"baseline tokenization fields: `{report.jaccard_fields}`")
    lines.append("")
    lines.append("## Pass/fail invariants — CMC-Lite engine guarantees")
    lines.append("")
    lines.append("These are the narrow correctness guarantees CMC-Lite makes. "
                 "The cmc-spec's `≥85% recall` target is for the *full* CMC "
                 "pipeline (including LLM clustering); CMC-Lite is the "
                 "deterministic subset and does not claim that number. "
                 "Recall metrics below are reported as informational only.")
    lines.append("")
    lines.append(f"| invariant | value | target | result |")
    lines.append(f"|---|---:|---:|:---:|")
    lines.append(f"| False-merge rate (auto-merge of different entities) | {report.false_merge_rate:.3f} | ≤{target_false_merge:.2f} | {fm_pass} |")
    lines.append(f"| ESS auto-merge precision (TP / (TP + FP)) | {ess_auto_precision:.3f} | ={target_auto_merge_precision:.2f} | {prec_pass} |")
    lines.append("")
    lines.append("## Recall — informational")
    lines.append("")
    lines.append(f"| metric | value | meaning |")
    lines.append(f"|---|---:|---|")
    lines.append(f"| Recall@T1 (exact only) | {report.recall_t1:.3f} | Pure normalization handles this fraction |")
    lines.append(f"| Recall@T1+T2 (auto-merge) | {report.recall_t1_t2:.3f} | Auto-mergeable without human review |")
    lines.append(f"| Recall@any-tier (surfaced) | {report.recall_any_tier:.3f} | Reaches at least T3 for human or higher-confidence review |")
    lines.append(f"| Jaccard same-entity match rate | {report.jaccard_match_rate:.3f} | Baseline at threshold 0.80 |")
    lines.append(f"| Jaccard false-merge rate | {report.jaccard_false_merge_rate:.3f} | Cost of baseline's recall |")
    lines.append("")
    lines.append("**Honest reading of these numbers:**")
    lines.append("")
    lines.append("- Recall@T1+T2 is the auto-merge fraction. Lower numbers here "
                 "mean the engine is being conservative, not wrong. The "
                 "operational target depends on how much human review you tolerate.")
    lines.append("- Recall@any-tier is what reaches a merge queue. CMC-Lite "
                 "surfaces drift modes it doesn't auto-merge — they're not "
                 "missed, they're flagged. But \"surfaced for review\" is not "
                 "the same thing as the cmc-spec's full-pipeline recall claim.")
    lines.append("- Jaccard tokenization deliberately excludes strong-anchor "
                 "fields (e.g. DOB tokenized as numeric tokens) when configured "
                 "per-corpus. With anchors preserved, Jaccard trivially matches "
                 "almost any name drift; the comparison is honest only when "
                 "both sides compete on the same surface forms.")
    lines.append("")
    lines.append("## ESS vs Jaccard at equivalent precision")
    lines.append("")
    lines.append("| comparator | same-entity recall | false-merge rate | precision-of-merges |")
    lines.append("|---|---:|---:|---:|")
    # ESS auto-merge "precision of merges" = TP_merged / (TP_merged + FP_merged)
    n_ess_tp = sum(1 for p in report.pairs if p.same_entity and p.ess_tier in _AUTO_MERGE)
    n_ess_fp = sum(1 for p in report.pairs if not p.same_entity and p.ess_tier in _AUTO_MERGE)
    ess_prec = n_ess_tp / (n_ess_tp + n_ess_fp) if (n_ess_tp + n_ess_fp) else 1.0
    n_jac_tp = sum(1 for p in report.pairs if p.same_entity and p.jaccard_matches)
    n_jac_fp = sum(1 for p in report.pairs if not p.same_entity and p.jaccard_matches)
    jac_prec = n_jac_tp / (n_jac_tp + n_jac_fp) if (n_jac_tp + n_jac_fp) else 1.0
    lines.append(f"| ESS auto-merge (T1+T2) | {report.recall_t1_t2:.3f} | "
                 f"{report.false_merge_rate:.3f} | {ess_prec:.3f} |")
    lines.append(f"| Jaccard @ 0.80 threshold | {report.jaccard_match_rate:.3f} | "
                 f"{report.jaccard_false_merge_rate:.3f} | {jac_prec:.3f} |")
    lines.append("")
    lines.append("The honest comparison: ESS chooses high precision (no incorrect auto-merges) "
                 "and surfaces the rest at T3/T4. Jaccard at this threshold accepts a "
                 f"{report.jaccard_false_merge_rate:.0%} false-merge rate to claim higher raw "
                 "recall — which would cascade into real data corruption in any production "
                 "merge pipeline.")
    lines.append("")

    lines.append("## Per-tier calibration")
    lines.append("")
    lines.append("| tier | n | stated confidence | actual precision |")
    lines.append("|---|---:|---:|---:|")
    for tier, data in report.per_tier_calibration.items():
        lines.append(
            f"| `{tier}` | {data['n']} | {data['stated_confidence']:.2f} | "
            f"{data['actual_precision']:.3f} |"
        )
    lines.append("")
    lines.append(
        "Reading this: for each tier the registry assigned, what fraction of "
        "pairs were actually same-entity? Stated confidences should approximate "
        "actual precision. (`no_match` reports negative predictive value — what "
        "fraction were correctly identified as different.)"
    )
    lines.append("")

    lines.append("## Per-family breakdown")
    lines.append("")
    lines.append("| family | n | recall@T1+T2 | recall any | false-merge | tier distribution |")
    lines.append("|---|---:|---:|---:|---:|---|")
    for fam, data in sorted(report.per_family.items()):
        dist = ", ".join(
            f"{t}={n}" for t, n in sorted(data["tier_distribution"].items())
        )
        lines.append(
            f"| `{fam}` | {data['n']} | "
            f"{data['recall_t1_t2']:.3f} | {data['recall_any_tier']:.3f} | "
            f"{data['false_merge_rate']:.3f} | {dist} |"
        )
    lines.append("")
    lines.append("`negative_control` is the false-merge canary — recall columns")
    lines.append("aren't meaningful (no same-entity pairs); false-merge MUST be 0.")
    lines.append("These mutations garble one ESS field at a time and leave the")
    lines.append("others intact, so each negative pair shares N-1 of N anchors with")
    lines.append("its canonical — a harder false-merge test than fully-disjoint records.")
    lines.append("")

    lines.append("## Honest limitations")
    lines.append("")
    lines.append("- Programmatic mutations are easier than real-world drift; "
                 "numbers are an upper bound. Phase 2 real-corpus run will be lower.")
    lines.append("- Hand-curated entity list reflects whatever it contains; "
                 "biases are documented in the corpus README.")
    lines.append("- The original CMC-Lite \"80–100% vs Jaccard's 9–36%\" claim was measured "
                 "on free-text memory atoms — a different domain than structured records "
                 "with anchor fields like DOB. Reproducing that specific number requires a "
                 "free-text atom corpus (Phase 2 target via csp's `artifacts/kb/` ingested KBs).")
    lines.append("- Per-tier calibration may diverge from stated confidence values on a "
                 "given corpus; that's a finding worth surfacing per-run rather than a bug. "
                 "Look at the calibration table above.")
    lines.append("- See `docs/benchmarks/ess-accuracy-spec.md` for the full "
                 "list of what this harness does and does not validate.")
    lines.append("")
    return "\n".join(lines)


def write_outputs(report: AccuracyReport, out_dir: Path) -> None:
    """Write report.md + results.json + pairs.tsv to out_dir."""
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "report.md").write_text(render_markdown(report), encoding="utf-8")
    (out_dir / "results.json").write_text(
        json.dumps(_strip_pairs(report.to_json()), indent=2),
        encoding="utf-8",
    )
    _write_pairs_tsv(report, out_dir / "pairs.tsv")


def _strip_pairs(report_dict: dict[str, Any]) -> dict[str, Any]:
    """For results.json keep summary; pairs.tsv carries the per-pair detail."""
    out = {k: v for k, v in report_dict.items() if k != "pairs"}
    return out


def _write_pairs_tsv(report: AccuracyReport, path: Path) -> None:
    """Tab-separated to avoid quoting headaches with names containing commas."""
    lines = [
        "\t".join([
            "family", "same_entity", "ess_tier", "ess_confidence",
            "jaccard_score", "jaccard_matches", "canonical", "mutated", "notes",
        ])
    ]
    for p in report.pairs:
        lines.append("\t".join([
            p.family,
            str(p.same_entity),
            p.ess_tier,
            f"{p.ess_confidence:.3f}",
            f"{p.jaccard_score:.3f}",
            str(p.jaccard_matches),
            json.dumps(p.canonical, ensure_ascii=False),
            json.dumps(p.mutated, ensure_ascii=False),
            p.notes,
        ]))
    path.write_text("\n".join(lines), encoding="utf-8")
