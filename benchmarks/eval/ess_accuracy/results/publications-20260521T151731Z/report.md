# ESS Accuracy Report — 2026-05-21T15:17:31Z
corpus: **publications** · schema: `publication` · entities: 30 · pairs: 783 (659 same, 124 different)
spiritwriter-core 0.5.1
baseline tokenization fields: `['title', 'first_author_last']`

## Pass/fail invariants — CMC-Lite engine guarantees

These are the narrow correctness guarantees CMC-Lite makes. The cmc-spec's `≥85% recall` target is for the *full* CMC pipeline (including LLM clustering); CMC-Lite is the deterministic subset and does not claim that number. Recall metrics below are reported as informational only.

| invariant | value | target | result |
|---|---:|---:|:---:|
| False-merge rate (auto-merge of different entities) | 0.000 | ≤0.05 | PASS |
| ESS auto-merge precision (TP / (TP + FP)) | 1.000 | =1.00 | PASS |

## Recall — informational

| metric | value | meaning |
|---|---:|---|
| Recall@T1 (exact only) | 0.636 | Pure normalization handles this fraction |
| Recall@T1+T2 (auto-merge) | 0.681 | Auto-mergeable without human review |
| Recall@any-tier (surfaced) | 1.000 | Reaches at least T3 for human or higher-confidence review |
| Jaccard same-entity match rate | 0.907 | Baseline at threshold 0.80 |
| Jaccard false-merge rate | 0.379 | Cost of baseline's recall |

**Honest reading of these numbers:**

- Recall@T1+T2 is the auto-merge fraction. Lower numbers here mean the engine is being conservative, not wrong. The operational target depends on how much human review you tolerate.
- Recall@any-tier is what reaches a merge queue. CMC-Lite surfaces drift modes it doesn't auto-merge — they're not missed, they're flagged. But "surfaced for review" is not the same thing as the cmc-spec's full-pipeline recall claim.
- Jaccard tokenization deliberately excludes strong-anchor fields (e.g. DOB tokenized as numeric tokens) when configured per-corpus. With anchors preserved, Jaccard trivially matches almost any name drift; the comparison is honest only when both sides compete on the same surface forms.

## ESS vs Jaccard at equivalent precision

| comparator | same-entity recall | false-merge rate | precision-of-merges |
|---|---:|---:|---:|
| ESS auto-merge (T1+T2) | 0.681 | 0.000 | 1.000 |
| Jaccard @ 0.80 threshold | 0.907 | 0.379 | 0.927 |

The honest comparison: ESS chooses high precision (no incorrect auto-merges) and surfaces the rest at T3/T4. Jaccard at this threshold accepts a 38% false-merge rate to claim higher raw recall — which would cascade into real data corruption in any production merge pipeline.

## Per-tier calibration

| tier | n | stated confidence | actual precision |
|---|---:|---:|---:|
| `t1_exact` | 419 | 0.95 | 1.000 |
| `t2_strong` | 30 | 0.85 | 1.000 |
| `t3_fuzzy` | 162 | 0.70 | 0.926 |
| `t4_weak` | 140 | 0.50 | 0.429 |
| `no_match` | 32 | 0.00 | 1.000 |

Reading this: for each tier the registry assigned, what fraction of pairs were actually same-entity? Stated confidences should approximate actual precision. (`no_match` reports negative predictive value — what fraction were correctly identified as different.)

## Per-family breakdown

| family | n | recall@T1+T2 | recall any | false-merge | tier distribution |
|---|---:|---:|---:|---:|---|
| `case` | 149 | 1.000 | 1.000 | 0.000 | t1_exact=149 |
| `first_author_initial` | 30 | 0.000 | 1.000 | 0.000 | t3_fuzzy=30 |
| `garbled_all_fields` | 30 | 0.000 | 0.000 | 0.000 | no_match=30 |
| `negative_control` | 90 | 0.000 | 0.000 | 0.000 | t3_fuzzy=10, t4_weak=80 |
| `realistic_collision` | 4 | 0.000 | 0.000 | 0.000 | no_match=2, t3_fuzzy=2 |
| `title_subtitle_add` | 5 | 0.000 | 1.000 | 0.000 | t3_fuzzy=5 |
| `title_subtitle_drop` | 16 | 0.000 | 1.000 | 0.000 | t3_fuzzy=16 |
| `typo_insertion` | 82 | 0.000 | 1.000 | 0.000 | t3_fuzzy=52, t4_weak=30 |
| `typo_substitution` | 47 | 0.000 | 1.000 | 0.000 | t3_fuzzy=47 |
| `whitespace` | 270 | 1.000 | 1.000 | 0.000 | t1_exact=270 |
| `year_missing` | 30 | 1.000 | 1.000 | 0.000 | t2_strong=30 |
| `year_typo` | 30 | 0.000 | 1.000 | 0.000 | t4_weak=30 |

`negative_control` is the false-merge canary — recall columns
aren't meaningful (no same-entity pairs); false-merge MUST be 0.
These mutations garble one ESS field at a time and leave the
others intact, so each negative pair shares N-1 of N anchors with
its canonical — a harder false-merge test than fully-disjoint records.

## Honest limitations

- Programmatic mutations are easier than real-world drift; numbers are an upper bound. Phase 2 real-corpus run will be lower.
- Hand-curated entity list reflects whatever it contains; biases are documented in the corpus README.
- The original CMC-Lite "80–100% vs Jaccard's 9–36%" claim was measured on free-text memory atoms — a different domain than structured records with anchor fields like DOB. Reproducing that specific number requires a free-text atom corpus (Phase 2 target via csp's `artifacts/kb/` ingested KBs).
- Per-tier calibration may diverge from stated confidence values on a given corpus; that's a finding worth surfacing per-run rather than a bug. Look at the calibration table above.
- See `docs/benchmarks/ess-accuracy-spec.md` for the full list of what this harness does and does not validate.
