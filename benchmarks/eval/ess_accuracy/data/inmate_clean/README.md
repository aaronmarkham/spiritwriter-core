# `inmate_clean` — Realistic Frio Production Drift

24 hand-curated person entities exercising the drift modes that
actually appear in real jail roster data — without the stress-test
modes that exist in the `people` corpus to test engine resilience.

## What this corpus measures

The engine's per-family auto-merge behavior on the **realistic
operating regime** for frio-style cross-jurisdiction inmate roster
deduplication. Use this to characterize "what does the engine deliver
on data shaped like what we'll actually encounter in production."

Pair with `people` (kitchen-sink stress test) to see the
realistic-vs-upper-bound spread per family.

## What's included (per-corpus mutation families)

| Family | Realistic because |
|---|---|
| `middle_initial_add` / `middle_initial_drop` | Rosters inconsistently record "Carlos" vs "Carlos A" |
| `surname_drop_maternal` | Cross-jurisdictional drift: some rosters keep paternal+maternal, others just paternal |
| `surname_hyphenate` / `surname_dehyphenate` | Space vs hyphen formatting drift across data sources |

Plus the universal families (`case`, `whitespace`, `typo_substitution`,
`typo_insertion`, `unicode_normalization`, `negative_control`).

## What's deliberately excluded (vs `people`)

| Family | Why excluded here |
|---|---|
| `surname_duplication` | Real but rare frio artifact (Maria Paten → Maria Paten Paten). Stress-test territory. |
| `surname_hyphenate_duplicate` | Same — rare downstream of `surname_duplication`. |
| `four_name_compress` | Real but rare (Jose Luis Garcia Lopez → Jose Garcia). Stress-test territory. |
| `diminutive` | Real but uncommon in inmate rosters specifically — different drift mode (informal vs transcription). |

If you want those drift modes exercised, run against the `people`
corpus, which has all of them.

## Composition

24 entities, distribution:

- 17 Hispanic names (mix of single-surname like "Rodriguez" and two-surname like "Garcia Lopez")
- 5 Anglo names (Smith, Johnson, etc.)
- 1 compound European name (O'Connor)
- 1 East Asian name (Tanaka)
- 1 South Asian name (Patel)
- 1 hyphenated (Smith-Jones)
- 1 four-part name (Jose Luis Hernandez Martinez)

**Deliberate non-features:**
- No diacritics (universal `unicode_normalization` will be a no-op)
- All have DOB (no DOB-missing test here)

## Expected per-family outcome

Marketing-relevant claims come from per-family rows. Predictions for
this corpus:

- `case`, `whitespace` → 100% recall@T1+T2 (universal normalization)
- `middle_initial_*` → 0% recall@T1+T2, 100% any-tier (T3 by design)
- `surname_drop_maternal` → 0% recall@T1+T2, 100% any-tier (T3 by design)
- `surname_hyphenate` / `dehyphenate` → mixed at T3 depending on similarity
- `typo_*` → 0% recall@T1+T2, 100% any-tier (T3 by design)
- `negative_control` → 0% false-merge

Whole-corpus Recall@T1+T2 expected to be modest (most non-universal
drift modes don't auto-merge by design). Per-family is the marketing
story.

## Sampling biases (known)

- Hispanic-name overrepresentation (~70%) reflects frio's primary
  drift modes; not a population-representative sample
- 24-entity sample is intentionally smaller than `people` (48) to
  separate the realistic-regime signal from the stress-test signal

## Provenance

Hand-curated by the harness author (2026-05-19). Subset of the entity
patterns in the `people` corpus, narrowed to those that exercise
realistic-only drift modes. Not derived from any external dataset; not PII.
