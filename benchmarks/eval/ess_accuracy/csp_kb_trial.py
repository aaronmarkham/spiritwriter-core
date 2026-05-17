"""Phase 2 — real-corpus ESS trial against a csp KnowledgeGraph.

Reads a csp ``knowledge_graph.json`` (produced by ``cs kb add ...``),
extracts entities from ``entity_index`` along with every distinct
surface form they appear as in atom content, and runs ESS resolution
on each surface-variation cluster.

Two things measured:

1. **Intra-document surface drift** — given an entity's most-frequent
   surface form as canonical, does ESS resolve the other forms found
   in the same paper(s) to the same canonical entity?

2. **Cross-source resolution** — for entities mentioned in 2+ sources,
   are they resolved as the same canonical entity even when the
   surface form differs between sources?

Usage::

    python -m benchmarks.eval.ess_accuracy.csp_kb_trial \\
        --kg path/to/knowledge_graph.json \\
        --out benchmarks/eval/ess_accuracy/results/csp-<name>-<ts>
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry,
    CanonicalSchema,
    ResolutionTier,
)


# ── Surface-form extraction ─────────────────────────────────────────


def _is_short_or_common(entity: str) -> bool:
    """Filter entities that produce too much noise in precise matching.

    Two-character or shorter entities (e.g. ``Z``, ``OR``) and common
    English words tend to match substrings of unrelated text. Skip them
    rather than fight the regex; the eval signal is cleaner that way.
    """
    if len(entity) <= 2:
        return True
    if entity.lower() in {
        "or", "and", "the", "for", "with", "from", "this", "that",
        "log", "cos", "arc", "method", "methods", "form", "set",
    }:
        return True
    return False


def collect_surface_forms(
    kg: dict[str, Any]
) -> dict[str, dict[str, Counter]]:
    """For each entity, return ``{source_id: Counter(surface_form -> count)}``.

    Whole-word case-insensitive match. No suffix permissiveness — we
    want clean case-and-exact-form variation, not regex over-matching.
    Pluralization gets measured separately via universal mutations.
    """
    entities = list(kg["entity_index"].keys())
    atoms = kg["atoms"]
    atom_sources = kg.get("atom_sources", {})

    out: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))

    for entity in entities:
        if _is_short_or_common(entity):
            continue
        pattern = re.compile(rf"\b{re.escape(entity)}\b", re.IGNORECASE)
        for atom_id, atom in atoms.items():
            content = atom.get("content", "") or ""
            if not content:
                continue
            source_id = atom_sources.get(atom_id, "unknown")
            for m in pattern.finditer(content):
                out[entity][source_id][m.group(0)] += 1

    return out


# ── Trial result types ──────────────────────────────────────────────


@dataclass
class ResolutionRow:
    entity_canonical: str
    canonical_form: str            # most-frequent surface form treated as canonical
    variant_form: str
    same_source: bool              # is the variant from the same source as canonical?
    ess_tier: str
    ess_confidence: float


@dataclass
class CspKbTrialReport:
    kg_path: str
    n_entities_in_index: int
    n_entities_scanned: int
    n_entities_with_variation: int
    n_pairs_intra: int
    n_pairs_cross_source: int

    intra_recall_t1: float
    intra_recall_t1_t2: float
    intra_recall_any_tier: float

    cross_source_recall_t1: float
    cross_source_recall_t1_t2: float
    cross_source_recall_any_tier: float

    per_tier_intra: dict[str, int] = field(default_factory=dict)
    per_tier_cross: dict[str, int] = field(default_factory=dict)
    rows: list[ResolutionRow] = field(default_factory=list)
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    )


# ── Trial runner ────────────────────────────────────────────────────


_AUTO_MERGE = {ResolutionTier.T1_EXACT.value, ResolutionTier.T2_STRONG.value}
_RESOLVED = _AUTO_MERGE | {
    ResolutionTier.T3_FUZZY.value,
    ResolutionTier.T4_WEAK.value,
}


def run_trial(kg_path: Path) -> CspKbTrialReport:
    with open(kg_path, encoding="utf-8") as f:
        kg = json.load(f)

    forms_by_entity = collect_surface_forms(kg)

    schema = CanonicalSchema(
        name="paper_term",
        ess_fields=["term"],
        fuzzy_fields={"term": 0.75},
        context_fields=[],
    )

    rows: list[ResolutionRow] = []

    with tempfile.TemporaryDirectory(
        prefix="csp_kb_eval_", ignore_cleanup_errors=True
    ) as tmp:
        with CanonicalRegistry(Path(tmp) / "reg.db", schema) as registry:
            # For each entity, pick its most-frequent surface form as canonical
            # and seed the registry with it.
            seeded_canonicals: dict[str, str] = {}
            for entity, source_map in forms_by_entity.items():
                all_forms: Counter = Counter()
                for source_id, forms in source_map.items():
                    all_forms.update(forms)
                if not all_forms:
                    continue
                canonical_form = all_forms.most_common(1)[0][0]
                cand = {"term": canonical_form}
                result = registry.resolve(cand)
                registry.upsert(cand, result, "csp_kb_eval", f"seed:{entity}")
                seeded_canonicals[entity] = canonical_form

            # For each entity, test resolution of every other distinct
            # surface form against the registry. Classify as same-source
            # or cross-source.
            for entity, canonical_form in seeded_canonicals.items():
                source_map = forms_by_entity[entity]
                # Which source(s) does the canonical form appear in?
                canonical_sources = {
                    sid for sid, forms in source_map.items()
                    if canonical_form in forms
                }
                # All distinct variant forms across all sources
                seen_variants: set[str] = set()
                for source_id, forms in source_map.items():
                    for form in forms:
                        if form == canonical_form:
                            continue
                        if form in seen_variants:
                            continue
                        seen_variants.add(form)
                        result = registry.resolve({"term": form})
                        # Is this variant from a different source than canonical?
                        same_src = source_id in canonical_sources
                        rows.append(ResolutionRow(
                            entity_canonical=entity,
                            canonical_form=canonical_form,
                            variant_form=form,
                            same_source=same_src,
                            ess_tier=result.tier.value,
                            ess_confidence=result.confidence,
                        ))

    return _summarize(kg_path, kg, forms_by_entity, rows)


def _summarize(
    kg_path: Path,
    kg: dict,
    forms_by_entity: dict,
    rows: list[ResolutionRow],
) -> CspKbTrialReport:
    intra = [r for r in rows if r.same_source]
    cross = [r for r in rows if not r.same_source]

    def _frac(xs: list[ResolutionRow], pred) -> float:
        return sum(1 for x in xs if pred(x)) / len(xs) if xs else 0.0

    intra_t1 = _frac(intra, lambda r: r.ess_tier == ResolutionTier.T1_EXACT.value)
    intra_auto = _frac(intra, lambda r: r.ess_tier in _AUTO_MERGE)
    intra_any = _frac(intra, lambda r: r.ess_tier in _RESOLVED)

    cross_t1 = _frac(cross, lambda r: r.ess_tier == ResolutionTier.T1_EXACT.value)
    cross_auto = _frac(cross, lambda r: r.ess_tier in _AUTO_MERGE)
    cross_any = _frac(cross, lambda r: r.ess_tier in _RESOLVED)

    per_tier_intra: dict[str, int] = Counter(r.ess_tier for r in intra)
    per_tier_cross: dict[str, int] = Counter(r.ess_tier for r in cross)

    n_with_var = sum(
        1 for entity in forms_by_entity
        if sum(sum(c.values()) for c in forms_by_entity[entity].values()) > 0
        and len({f for src in forms_by_entity[entity].values() for f in src}) > 1
    )

    return CspKbTrialReport(
        kg_path=str(kg_path),
        n_entities_in_index=len(kg["entity_index"]),
        n_entities_scanned=len(forms_by_entity),
        n_entities_with_variation=n_with_var,
        n_pairs_intra=len(intra),
        n_pairs_cross_source=len(cross),
        intra_recall_t1=intra_t1,
        intra_recall_t1_t2=intra_auto,
        intra_recall_any_tier=intra_any,
        cross_source_recall_t1=cross_t1,
        cross_source_recall_t1_t2=cross_auto,
        cross_source_recall_any_tier=cross_any,
        per_tier_intra=dict(per_tier_intra),
        per_tier_cross=dict(per_tier_cross),
        rows=rows,
    )


# ── Report rendering ────────────────────────────────────────────────


def render_markdown(report: CspKbTrialReport) -> str:
    lines = [
        f"# CSP KB ESS Trial — {report.generated_at}",
        f"source: `{report.kg_path}`",
        "",
        "## Corpus shape",
        "",
        f"- Entities in `entity_index`: {report.n_entities_in_index}",
        f"- Entities scanned (after short/common filter): {report.n_entities_scanned}",
        f"- Entities with multiple surface forms: {report.n_entities_with_variation}",
        f"- Intra-source variant pairs tested: {report.n_pairs_intra}",
        f"- Cross-source variant pairs tested: {report.n_pairs_cross_source}",
        "",
        "## Resolution accuracy",
        "",
        "| metric | intra-source | cross-source |",
        "|---|---:|---:|",
        f"| Recall@T1 (exact) | {report.intra_recall_t1:.3f} | {report.cross_source_recall_t1:.3f} |",
        f"| Recall@T1+T2 (auto-merge) | {report.intra_recall_t1_t2:.3f} | {report.cross_source_recall_t1_t2:.3f} |",
        f"| Recall@any tier (surfaced) | {report.intra_recall_any_tier:.3f} | {report.cross_source_recall_any_tier:.3f} |",
        "",
        "## Tier distribution",
        "",
        "**Intra-source:**",
    ]
    for tier, n in sorted(report.per_tier_intra.items()):
        lines.append(f"  - `{tier}`: {n}")
    lines.append("")
    lines.append("**Cross-source:**")
    for tier, n in sorted(report.per_tier_cross.items()):
        lines.append(f"  - `{tier}`: {n}")
    lines.append("")
    lines.append("## Sample resolutions")
    lines.append("")
    lines.append("| canonical | variant | tier | conf | scope |")
    lines.append("|---|---|---|---:|---|")
    # Show a mix: a few T1, a few non-T1
    t1_rows = [r for r in report.rows if r.ess_tier == ResolutionTier.T1_EXACT.value][:5]
    non_t1 = [r for r in report.rows if r.ess_tier != ResolutionTier.T1_EXACT.value][:10]
    for r in t1_rows + non_t1:
        scope = "intra" if r.same_source else "cross"
        lines.append(
            f"| `{r.canonical_form}` | `{r.variant_form}` | `{r.ess_tier}` | "
            f"{r.ess_confidence:.2f} | {scope} |"
        )
    lines.append("")
    lines.append("## What this trial validates")
    lines.append("")
    lines.append("- **Real-corpus mode** for the ESS accuracy harness: consumes "
                 "a csp `knowledge_graph.json` produced by `cs kb add`. No "
                 "synthetic mutations — every pair is a surface form that "
                 "actually appears in atom content from a real paper.")
    lines.append("- **Cross-source resolution** specifically: when the same "
                 "entity surfaces in different documents with different surface "
                 "forms, does the registry merge them?")
    lines.append("- **Honest limits**: pluralization (DNN/DNNs) typically lands "
                 "at NO_MATCH on surface strings alone. See `docs/benchmarks/"
                 "ess-accuracy-spec.md` for why — this is by design at the "
                 "CMC-Lite layer, and is what the full CMC pipeline's LLM "
                 "clustering stage is for.")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────────


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    parser = argparse.ArgumentParser(description="csp KB → ESS resolution trial")
    parser.add_argument(
        "--kg", required=True, type=Path,
        help="Path to a csp knowledge_graph.json",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Output directory (default: benchmarks/eval/ess_accuracy/results/csp-<ts>)",
    )
    args = parser.parse_args()

    report = run_trial(args.kg)

    if args.out is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        args.out = Path(__file__).parent / "results" / f"csp-{timestamp}"
    args.out.mkdir(parents=True, exist_ok=True)

    md = render_markdown(report)
    (args.out / "report.md").write_text(md, encoding="utf-8")
    (args.out / "results.json").write_text(
        json.dumps({k: v for k, v in asdict(report).items() if k != "rows"}, indent=2),
        encoding="utf-8",
    )
    # Save per-row TSV
    rows_path = args.out / "rows.tsv"
    rows_path.write_text(
        "\n".join([
            "canonical\tvariant\ttier\tconfidence\tsame_source",
            *(
                f"{r.canonical_form}\t{r.variant_form}\t{r.ess_tier}\t"
                f"{r.ess_confidence:.3f}\t{r.same_source}"
                for r in report.rows
            ),
        ]),
        encoding="utf-8",
    )

    print(md)
    print(f"\nArtifacts written to: {args.out}")


if __name__ == "__main__":
    main()
