# ESS Accuracy Report — 2026-05-21T15:17:29Z
corpus: **people** · schema: `person` · entities: 48 · pairs: 1288 (1091 same, 197 different)
spiritwriter-core 0.5.1
baseline tokenization fields: `['last_name', 'first_name']`

## Pass/fail invariants — CMC-Lite engine guarantees

These are the narrow correctness guarantees CMC-Lite makes. The cmc-spec's `≥85% recall` target is for the *full* CMC pipeline (including LLM clustering); CMC-Lite is the deterministic subset and does not claim that number. Recall metrics below are reported as informational only.

| invariant | value | target | result |
|---|---:|---:|:---:|
| False-merge rate (auto-merge of different entities) | 0.000 | ≤0.05 | PASS |
| ESS auto-merge precision (TP / (TP + FP)) | 1.000 | =1.00 | PASS |

## Recall — informational

| metric | value | meaning |
|---|---:|---|
| Recall@T1 (exact only) | 0.573 | Pure normalization handles this fraction |
| Recall@T1+T2 (auto-merge) | 0.573 | Auto-mergeable without human review |
| Recall@any-tier (surfaced) | 1.000 | Reaches at least T3 for human or higher-confidence review |
| Jaccard same-entity match rate | 0.740 | Baseline at threshold 0.80 |
| Jaccard false-merge rate | 0.269 | Cost of baseline's recall |

**Honest reading of these numbers:**

- Recall@T1+T2 is the auto-merge fraction. Lower numbers here mean the engine is being conservative, not wrong. The operational target depends on how much human review you tolerate.
- Recall@any-tier is what reaches a merge queue. CMC-Lite surfaces drift modes it doesn't auto-merge — they're not missed, they're flagged. But "surfaced for review" is not the same thing as the cmc-spec's full-pipeline recall claim.
- Jaccard tokenization deliberately excludes strong-anchor fields (e.g. DOB tokenized as numeric tokens) when configured per-corpus. With anchors preserved, Jaccard trivially matches almost any name drift; the comparison is honest only when both sides compete on the same surface forms.

## ESS vs Jaccard at equivalent precision

| comparator | same-entity recall | false-merge rate | precision-of-merges |
|---|---:|---:|---:|
| ESS auto-merge (T1+T2) | 0.573 | 0.000 | 1.000 |
| Jaccard @ 0.80 threshold | 0.740 | 0.269 | 0.938 |

The honest comparison: ESS chooses high precision (no incorrect auto-merges) and surfaces the rest at T3/T4. Jaccard at this threshold accepts a 27% false-merge rate to claim higher raw recall — which would cascade into real data corruption in any production merge pipeline.

## Per-tier calibration

| tier | n | stated confidence | actual precision |
|---|---:|---:|---:|
| `t1_exact` | 625 | 0.95 | 1.000 |
| `t3_fuzzy` | 391 | 0.70 | 0.931 |
| `t4_weak` | 224 | 0.50 | 0.455 |
| `no_match` | 48 | 0.00 | 1.000 |

Reading this: for each tier the registry assigned, what fraction of pairs were actually same-entity? Stated confidences should approximate actual precision. (`no_match` reports negative predictive value — what fraction were correctly identified as different.)

## Per-family breakdown

| family | n | recall@T1+T2 | recall any | false-merge | tier distribution |
|---|---:|---:|---:|---:|---|
| `case` | 193 | 1.000 | 1.000 | 0.000 | t1_exact=193 |
| `diminutive` | 40 | 0.000 | 1.000 | 0.000 | t3_fuzzy=37, t4_weak=3 |
| `dob_typo` | 48 | 0.000 | 1.000 | 0.000 | t4_weak=48 |
| `four_name_compress` | 1 | 0.000 | 1.000 | 0.000 | t4_weak=1 |
| `garbled_all_fields` | 48 | 0.000 | 0.000 | 0.000 | no_match=48 |
| `middle_initial_add` | 47 | 0.000 | 1.000 | 0.000 | t3_fuzzy=47 |
| `negative_control` | 144 | 0.000 | 0.000 | 0.000 | t3_fuzzy=27, t4_weak=117 |
| `realistic_collision` | 5 | 0.000 | 0.000 | 0.000 | t4_weak=5 |
| `surname_dehyphenate` | 2 | 0.000 | 1.000 | 0.000 | t3_fuzzy=2 |
| `surname_drop_maternal` | 14 | 0.000 | 1.000 | 0.000 | t3_fuzzy=12, t4_weak=2 |
| `surname_duplication` | 34 | 0.000 | 1.000 | 0.000 | t3_fuzzy=34 |
| `surname_hyphenate` | 12 | 0.000 | 1.000 | 0.000 | t3_fuzzy=12 |
| `surname_hyphenate_duplicate` | 34 | 0.000 | 1.000 | 0.000 | t3_fuzzy=34 |
| `typo_insertion` | 142 | 0.000 | 1.000 | 0.000 | t3_fuzzy=94, t4_weak=48 |
| `typo_substitution` | 92 | 0.000 | 1.000 | 0.000 | t3_fuzzy=92 |
| `whitespace` | 432 | 1.000 | 1.000 | 0.000 | t1_exact=432 |

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
