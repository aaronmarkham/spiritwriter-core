# ESS Accuracy Report — 2026-05-27T01:46:43Z
corpus: **inmate_clean** · schema: `person_inmate_clean` · entities: 26 · pairs: 636 (529 same, 107 different)
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
| Recall@T1 (exact only) | 0.639 | Pure normalization handles this fraction |
| Recall@T1+T2 (auto-merge) | 0.639 | Auto-mergeable without human review |
| Recall@any-tier (surfaced) | 1.000 | Reaches at least T3 for human or higher-confidence review |
| Jaccard same-entity match rate | 0.758 | Baseline at threshold 0.80 |
| Jaccard false-merge rate | 0.271 | Cost of baseline's recall |

**Honest reading of these numbers:**

- Recall@T1+T2 is the auto-merge fraction. Lower numbers here mean the engine is being conservative, not wrong. The operational target depends on how much human review you tolerate.
- Recall@any-tier is what reaches a merge queue. CMC-Lite surfaces drift modes it doesn't auto-merge — they're not missed, they're flagged. But "surfaced for review" is not the same thing as the cmc-spec's full-pipeline recall claim.
- Jaccard tokenization deliberately excludes strong-anchor fields (e.g. DOB tokenized as numeric tokens) when configured per-corpus. With anchors preserved, Jaccard trivially matches almost any name drift; the comparison is honest only when both sides compete on the same surface forms.

## ESS vs Jaccard at equivalent precision

| comparator | same-entity recall | false-merge rate | precision-of-merges |
|---|---:|---:|---:|
| ESS auto-merge (T1+T2) | 0.639 | 0.000 | 1.000 |
| Jaccard @ 0.80 threshold | 0.758 | 0.271 | 0.933 |

The honest comparison: ESS chooses high precision (no incorrect auto-merges) and surfaces the rest at T3/T4. Jaccard at this threshold accepts a 27% false-merge rate to claim higher raw recall — which would cascade into real data corruption in any production merge pipeline.

## Per-tier calibration

| tier | n | stated confidence | actual precision |
|---|---:|---:|---:|
| `t1_exact` | 338 | 0.95 | 1.000 |
| `t3_fuzzy` | 155 | 0.70 | 0.890 |
| `t4_weak` | 117 | 0.50 | 0.453 |
| `no_match` | 26 | 0.00 | 1.000 |

Reading this: for each tier the registry assigned, what fraction of pairs were actually same-entity? Stated confidences should approximate actual precision. (`no_match` reports negative predictive value — what fraction were correctly identified as different.)

## Per-family breakdown

| family | n | recall@T1+T2 | recall any | false-merge | tier distribution |
|---|---:|---:|---:|---:|---|
| `case` | 104 | 1.000 | 1.000 | 0.000 | t1_exact=104 |
| `dob_typo` | 26 | 0.000 | 1.000 | 0.000 | t4_weak=26 |
| `garbled_all_fields` | 26 | 0.000 | 0.000 | 0.000 | no_match=26 |
| `middle_initial_add` | 23 | 0.000 | 1.000 | 0.000 | t3_fuzzy=23 |
| `middle_initial_drop` | 2 | 0.000 | 1.000 | 0.000 | t3_fuzzy=2 |
| `negative_control` | 78 | 0.000 | 0.000 | 0.000 | t3_fuzzy=17, t4_weak=61 |
| `realistic_collision` | 3 | 0.000 | 0.000 | 0.000 | t4_weak=3 |
| `surname_dehyphenate` | 1 | 0.000 | 1.000 | 0.000 | t3_fuzzy=1 |
| `surname_drop_maternal` | 7 | 0.000 | 1.000 | 0.000 | t3_fuzzy=6, t4_weak=1 |
| `surname_hyphenate` | 6 | 0.000 | 1.000 | 0.000 | t3_fuzzy=6 |
| `typo_insertion` | 77 | 0.000 | 1.000 | 0.000 | t3_fuzzy=51, t4_weak=26 |
| `typo_substitution` | 49 | 0.000 | 1.000 | 0.000 | t3_fuzzy=49 |
| `whitespace` | 234 | 1.000 | 1.000 | 0.000 | t1_exact=234 |

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
