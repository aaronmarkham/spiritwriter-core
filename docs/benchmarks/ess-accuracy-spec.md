# ESS Accuracy Validation Harness — Design

**Status:** Draft for review. Not yet implemented.
**Scope:** Defends the *correctness* claims about Entity Sense Signature (ESS) resolution. Distinct from `benchmarks/bench_entity_resolution.py`, which measures *speed*.

## Claims this harness defends

Every metric below points back to a concrete claim in the existing docs. If a claim isn't here, the harness can't speak to it.

| Claim | Source | Metric |
|---|---|---|
| "Definition-based matching beats string similarity (~33% → 80–100% consistency)" | [docs/specs/spiritwriter-canonicalize.md:13](../specs/spiritwriter-canonicalize.md) | Recall@T1+T2 for ESS vs. baseline Jaccard-on-tokens |
| "Consensus matching uses Jaccard on key tokens (9–36% match rate)" | [docs/specs/cmc-lite-v0.1.md:33](../specs/cmc-lite-v0.1.md) | Baseline measurement under same mutations |
| "Targets ≥85% recall on semantic duplicates with ≤5% false-merge rate" | [docs/specs/cmc-spec-v0.1.md](../specs/cmc-spec-v0.1.md) | Recall@T1+T2 ≥ 0.85; false-merge rate ≤ 0.05 |
| Tier confidence values (T1=0.95, T2=0.85, T3=0.70, T4=0.50) | [docs/entity-resolution.md:14](../entity-resolution.md) | Calibration: actual-correct rate at each tier matches the stated confidence |

What this does NOT defend (kept honest):
- LLM-driven entity *extraction* quality (separate problem; needs a separate harness).
- Semantic similarity for entity pairs with zero overlapping defining fields (e.g. acronym vs. expansion — ESS cannot help without an alias table; that's by design).
- Real-world drift in any specific domain (this harness measures the *engine*, not the *data*).

## Corpus design — domain-pluggable

The CanonicalRegistry is domain-agnostic by design ([docs/entity-resolution.md:339](../entity-resolution.md) — "Custom Schemas"). The eval harness must reflect that: it shouldn't ship a single fixed corpus and call that authoritative for every domain ESS might be used in. Instead, it ships **example corpora** — small, well-documented, domain-shaped — and a documented *process* for building your own.

This rhymes with how the existing canonical registries handle seed data:
- [spiritwriter/audit/data/canonical_findings.json](../../spiritwriter/audit/data/canonical_findings.json) — OCV/audit-domain example
- [spiritwriter/sw_vocab/data/canonical_terms.json](../../spiritwriter/sw_vocab/data/canonical_terms.json) — spiritwriter-terminology-domain example

Neither claims to be *the* canonical seed; each demonstrates how to bootstrap one for a domain.

### Universal mutation families (apply to every corpus)

Each rule is independently scored so we can attribute results:

| Family | Example | Expected ESS behavior |
|---|---|---|
| `case` | "Martinez" → "MARTINEZ" | T1 exact (normalization handles) |
| `whitespace` | "Carlos" → "  Carlos  " | T1 exact |
| `typo_substitution` | "Smith" → "Smyth" | T2/T3 fuzzy |
| `typo_insertion` | "Smith" → "Smiith" | T2/T3 fuzzy |
| `unicode_normalization` | "María" → "Maria" | T2/T3 fuzzy |
| `negative_control` | Different ESS-field values | NO_MATCH or T4 |

These run against *any* corpus. They're universal because they exercise the engine's normalization, fuzzy scorer, and tier system — not anything domain-specific.

### Domain-specific mutations (per-corpus)

Each corpus directory may declare its own mutation rules in `mutations.py`. Examples:

- **People** corpus: `middle_initial_add`, `middle_initial_drop`, `diminutive` ("Carlos" → "Carlitos"), `hyphenation` ("De La Cruz" → "Delacruz"), plus a family of **Hispanic two-surname drift modes** observed in frio rosters:
  - `surname_duplication` — "Maria Paten" → "Maria Paten Paten" (real frio artifact: roster systems sometimes duplicate when a person has only one surname recorded)
  - `surname_hyphenate_duplicate` — "Maria Paten" → "Maria Paten-Paten"
  - `surname_drop_maternal` — "Garcia Lopez" → "Garcia"
  - `surname_hyphenate` / `surname_dehyphenate` — "Garcia Lopez" ↔ "Garcia-Lopez"
  - `four_name_compress` — "Jose Luis Garcia Lopez" → "Jose Garcia" (drops middle given + maternal surname)
- **Publications** corpus: `venue_abbreviation` ("International Conference on Machine Learning" → "ICML"), `year_optional` ("BERT (2018)" → "BERT"). Phase 2 acquisition path: the SciencePodcastGenerator project has a PDF search/download tool we can repurpose to populate the publications corpus from arXiv-style queries.

The runner discovers and applies them automatically; missing `mutations.py` means universal rules only.

### Example corpora shipped

Two domains, deliberately structurally different (different `ess_fields`, different fuzzy thresholds), to demonstrate the engine isn't tuned for one shape:

1. **`benchmarks/eval/ess_accuracy/data/people/`** — name + DOB + optional middle initial. Mirrors the inmate schema that already exists in [bench_entity_resolution.py:25](../../benchmarks/bench_entity_resolution.py). ~50 entities hand-curated; not authoritative, just illustrative.
2. **`benchmarks/eval/ess_accuracy/data/publications/`** — paper title + first-author surname + year. Matches the academic-article use case driving this work. ~50 entries.

Each `data/<domain>/` contains:

```
schema.json       — CanonicalSchema as JSON (name, ess_fields, fuzzy_fields, ...)
entities.json     — list of canonical entity records
mutations.py      — optional; domain-specific mutation rules
README.md         — what this corpus is, where the entities came from, known biases
```

### Bring-your-own-corpus process (documented, not magic)

`benchmarks/eval/ess_accuracy/README.md` will document the four steps to add a new domain:

1. Define a `CanonicalSchema` in `data/<domain>/schema.json`
2. Curate or import a canonical entity list in `data/<domain>/entities.json`
3. (Optional) Add domain-specific mutation rules in `data/<domain>/mutations.py`
4. Run: `python -m benchmarks.eval.ess_accuracy.runner --corpus <domain>`

Each step links to a "how do you actually do this" section. The point: the harness is reusable infrastructure, not a one-off measurement of one corpus.

### Phase 2 — real corpora (deferred)

Once Phase 1 is running on the two example corpora, the natural Phase 2 is real-data validation against csp's existing `artifacts/kb/kb_*/` ingested academic KBs. Three projects already exist there. The flow: re-extract entities → measure cross-source resolution → spot-check labels by hand. No new infrastructure needed; same harness, real corpus.

arXiv abstracts as a hand-curated alternative is on the table if the csp KBs don't give us enough cross-source variation; deciding after Phase 1 lands.

## Metrics computed

For each run, against each corpus:

1. **Recall@T1**: fraction of `same=True` pairs resolved at T1_EXACT.
2. **Recall@T1+T2**: fraction resolved at T1_EXACT or T2_STRONG (the auto-merge tiers — this is the headline "≥85%" number).
3. **Recall@T1+T2+T3**: fraction resolved at any tier above NO_MATCH.
4. **False-merge rate**: among `same=False` pairs, fraction resolved at T1 or T2 (= incorrect auto-merge).
5. **Per-tier precision**: among pairs at each tier, fraction that are actually `same=True`. Should approximate the stated confidence values (T1≈0.95, T2≈0.85, T3≈0.70).
6. **Per-mutation-family breakdown**: recall split by mutation rule. Lets a reviewer see "case variation handled at T1; typos at T2/T3."
7. **Baseline comparison**: same metrics for Jaccard-on-tokens (the thing CMC-Lite claims to beat). Headline: ESS Recall@T1+T2 minus Jaccard match rate.

## Output

```
eval/results/<timestamp>/
├── report.md           — human-readable summary (the citable artifact)
├── results.json        — raw metrics, machine-readable
├── pairs.csv           — every pair + predicted tier + ground truth + correct? (for spreadsheet inspection)
└── corpus.json         — the exact corpus + mutations used (reproducibility)
```

`report.md` shape:

```markdown
# ESS Accuracy Report — 2026-05-17T22:00:00Z
spiritwriter-core 0.7.2 · commit abc1234

## Headline
Recall@T1+T2:  0.91   (target ≥0.85)   ✓
False-merge:   0.02   (target ≤0.05)   ✓
ESS - Jaccard: +0.58  (ESS auto-merge minus Jaccard match rate)

## Per-tier calibration
T1_EXACT  predicted-precision 0.95  actual 0.99  (n=412)
T2_STRONG predicted-precision 0.85  actual 0.88  (n=187)
T3_FUZZY  predicted-precision 0.70  actual 0.72  (n=143)

## Per-mutation breakdown
case               recall 1.00 (T1)
whitespace         recall 1.00 (T1)
middle_initial_add recall 0.84 (T3)
typo_substitution  recall 0.71 (T2)
...
```

## Module layout

```
benchmarks/
  eval/                                    — accuracy/correctness measurements (this work)
    __init__.py
    README.md                              — what each metric means + bring-your-own-corpus process
    ess_accuracy/
      __init__.py
      corpus.py                            — load schema + entities + per-corpus mutations
      mutations.py                         — universal mutation rule families
      baselines.py                         — Jaccard-on-tokens (and any other comparator)
      metrics.py                           — recall, false-merge, calibration
      runner.py                            — orchestrate: load → mutate → resolve → score → report
      data/
        people/
          schema.json
          entities.json
          mutations.py                     — domain-specific (middle initial, diminutive, ...)
          README.md
        publications/
          schema.json
          entities.json
          mutations.py                     — domain-specific (venue abbreviation, ...)
          README.md
    results/                               — generated; gitignored except .gitkeep
  bench_entity_resolution.py               — existing perf bench (untouched)
  ... other existing perf benches
tests/test_ess_accuracy.py                 — unit tests on mutations + metrics
```

CLI:
```
python -m benchmarks.eval.ess_accuracy.runner --corpus people
python -m benchmarks.eval.ess_accuracy.runner --corpus publications
python -m benchmarks.eval.ess_accuracy.runner --corpus /path/to/your-domain
```

The existing `benchmarks/README.md` gets a new section pointing at `eval/` — perf and accuracy living in the same directory, with the subdivision making the audience split clear.

## Tonight-startable scope

Minimum viable: Phase 1 corpus + the four headline metrics (Recall@T1+T2, false-merge, ESS-vs-Jaccard, per-tier calibration), with `report.md` output. Per-mutation breakdown is the next layer; Phase 2 corpus is the next phase.

**Estimated implementation effort (Phase 1 only):**
- Corpus + mutations: ~150 lines, 1–2 hours
- Baseline Jaccard scorer: ~50 lines, 30 minutes
- Metrics + report: ~200 lines, 2 hours
- Runner + CLI: ~100 lines, 1 hour
- Tests: ~150 lines, 1 hour
- **Total: ~650 lines, ~5–6 hours of focused work**

Deliverable: one commit, one PR, one runnable command that produces a citable `report.md` defending the four headline claims.

## Honest limitations

- Programmatic mutations are *easier* than real-world drift. Phase 1 numbers will be upper-bound; Phase 2 will be lower and closer to real.
- The hand-curated seed entity list reflects whatever we put in it. We should publish it; reviewers can argue with the choice.
- Single-language (English) bias.
- Mutation families don't cover every real drift mode (e.g. transliteration, semantic synonyms like "USA" / "United States" — ESS can't resolve those without an alias table; we should say so explicitly).

## Open questions for review

1. ~~`eval/` or `benchmarks/eval_*`?~~ → **Resolved**: `benchmarks/eval/`.
2. ~~Seed entity list — hand-curate or pull from a public dataset?~~ → **Resolved**: ship two example corpora (people, publications) as illustrative not authoritative; document the bring-your-own-corpus process. Phase 2 uses real csp KBs.
3. **CI cadence** — every build, or nightly? Recall thresholds will drift on legitimate refactors; suggest **nightly with trend tracking** rather than per-commit hard gate. Hard gates only on regression-style invariants (e.g., recall doesn't drop more than 10 points on the people corpus).
4. **First example corpus to build** — people or publications first? People has the prior art (matches the inmate schema already in `bench_entity_resolution.py`); publications is closer to the eventual academic-articles goal.
