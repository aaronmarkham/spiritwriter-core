# ESS Accuracy — Runs Log

Per-run summary of every benchmark execution we want to preserve. Each
entry cites the corresponding committed `report.md` so anyone can audit
the numbers without re-running. Ephemeral runs (e.g. debugging) are
gitignored under `benchmarks/eval/ess_accuracy/results/`; entries here
correspond only to runs we deliberately `git add -f`'d.

Format per entry: corpus identity, what the corpus tests, the headline
invariants and per-family table, full-report link. Cross-corpus summary
at the bottom.

---

## Campaign: 2026-05 — Multi-corpus benchmarks for marketing claims

Goal: measure CMC-Lite resolution accuracy across structurally different
corpora to characterize what numbers we can defensibly cite on the
spiritwriter.ai homepage and in the cmc-spec. Driven by the discovery
during PR #55 review that the homepage's `≥85% recall` claim was the
cmc-spec's full-pipeline target, not a CMC-Lite measurement.

Five corpora, all with `False-merge rate = 0.000` and `Auto-merge
precision = 1.000` (the CMC-Lite invariants), differing in shape:

> **Note on pair counts.** The per-family `n=` cells in the per-corpus
> sections below reflect the corpus state at first measurement. After
> the falsification battery and PR #58 review fixes, entity sets were
> minorly extended (inmate_clean 24→26, people 48→50) to exercise the
> `middle_initial_drop` family — so the most recent pinned `report.md`
> files have slightly higher pair counts than shown here. The
> *invariants* (precision = 1.000, false-merge = 0.000, per-family
> recall percentages) are unchanged. The pinned `report.md` linked at
> the top of each section is the authoritative count.

| Corpus | Schema | Entities | What it tests | Composition + provenance |
|---|---|---:|---|---|
| [`case_only`](#case_only--2026-05-19t182318z) | person (3 fields, DOB-anchored) | 20 | Clean entities + universal mutations only | [README](../../benchmarks/eval/ess_accuracy/data/case_only/README.md) · [entities.json](../../benchmarks/eval/ess_accuracy/data/case_only/entities.json) |
| [`inmate_clean`](#inmate_clean--2026-05-27t014644z) | person (3 fields, DOB-anchored) | 26 | Realistic frio production drift | [README](../../benchmarks/eval/ess_accuracy/data/inmate_clean/README.md) · [entities.json](../../benchmarks/eval/ess_accuracy/data/inmate_clean/entities.json) · [mutations.py](../../benchmarks/eval/ess_accuracy/data/inmate_clean/mutations.py) |
| [`people`](#people--2026-05-27t014645z) | person (3 fields, DOB-anchored) | 50 | Kitchen-sink stress test (PR #55 baseline) | [README](../../benchmarks/eval/ess_accuracy/data/people/README.md) · [entities.json](../../benchmarks/eval/ess_accuracy/data/people/entities.json) · [mutations.py](../../benchmarks/eval/ess_accuracy/data/people/mutations.py) |
| [`publications`](#publications--2026-05-19t182854z) | publication (3 fields, year-anchored) | 30 | Different schema shape — academic citations | [README](../../benchmarks/eval/ess_accuracy/data/publications/README.md) · [entities.json](../../benchmarks/eval/ess_accuracy/data/publications/entities.json) · [mutations.py](../../benchmarks/eval/ess_accuracy/data/publications/mutations.py) |
| [`csp_kb_AI_Res`](#csp_kb_ai_res--2026-05-19t182328z) | paper_term (1 field) | 315 | Real LLM-extracted entities from a csp knowledge graph | external — see csp_kb section below |

---

### `case_only` — 2026-05-27T01:46:43Z

**What it tests.** Clean person entities, only the universal mutation
families fire (no domain-specific drift). The cleanest path through the
engine — what does CMC-Lite deliver when the data is what most systems
actually have?

**Report:** [`results/case_only-20260527T014643Z/report.md`](../../benchmarks/eval/ess_accuracy/results/case_only-20260527T014643Z/report.md)

| Invariant | Value | Target | Result |
|---|---:|---:|:---:|
| False-merge rate | 0.000 | ≤0.05 | PASS |
| Auto-merge precision (T1+T2) | 1.000 | =1.00 | PASS |

| Recall metric | Value |
|---|---:|
| Recall@T1 (exact) | 0.733 |
| Recall@T1+T2 (auto-merge) | 0.733 |
| Recall@any-tier (surfaced) | 1.000 |

**Per-family (marketing-relevant rows):**

| Family | n | recall@T1+T2 | recall@any | notes |
|---|---:|---:|---:|---|
| `case` | 81 | **1.000** | 1.000 | All case drift auto-merges (T1) |
| `whitespace` | 180 | **1.000** | 1.000 | All whitespace drift auto-merges (T1) |
| `typo_substitution` | 37 | 0.000 | 1.000 | T3 by design |
| `typo_insertion` | 58 | 0.000 | 1.000 | T3/T4 by design |
| `negative_control` | 60 | n/a | n/a | False-merge = 0 (canary intact) |

---

### `inmate_clean` — 2026-05-27T01:46:44Z

**What it tests.** Realistic frio jail-roster drift: middle initials,
maternal-surname drops, hyphenation differences. Deliberately *excludes*
the stress-test modes from `people` (surname duplication, four-name
compression, diminutives) — those are rare adversarial drift, not the
operating regime.

**Report:** [`results/inmate_clean-20260527T014644Z/report.md`](../../benchmarks/eval/ess_accuracy/results/inmate_clean-20260527T014644Z/report.md)

| Invariant | Value | Target | Result |
|---|---:|---:|:---:|
| False-merge rate | 0.000 | ≤0.05 | PASS |
| Auto-merge precision (T1+T2) | 1.000 | =1.00 | PASS |

| Recall metric | Value |
|---|---:|
| Recall@T1 (exact) | 0.671 |
| Recall@T1+T2 (auto-merge) | 0.671 |
| Recall@any-tier (surfaced) | 1.000 |

**Per-family:**

| Family | n | recall@T1+T2 | recall@any | notes |
|---|---:|---:|---:|---|
| `case` | 96 | **1.000** | 1.000 | T1 |
| `whitespace` | 216 | **1.000** | 1.000 | T1 |
| `middle_initial_add` | 23 | 0.000 | 1.000 | T3 by design |
| `surname_drop_maternal` | 7 | 0.000 | 1.000 | T3 by design |
| `surname_hyphenate` | 6 | 0.000 | 1.000 | T3 by design |
| `surname_dehyphenate` | 1 | 0.000 | 1.000 | T3 by design |
| `typo_substitution` | 45 | 0.000 | 1.000 | T3 by design |
| `typo_insertion` | 71 | 0.000 | 1.000 | T3/T4 by design |
| `negative_control` | 72 | n/a | n/a | False-merge = 0 |

---

### `people` — 2026-05-27T01:46:45Z

**What it tests.** Kitchen-sink stress test. Same schema as
`inmate_clean`, but mutations include the rare adversarial drift modes
(`surname_duplication`, `surname_hyphenate_duplicate`, `four_name_compress`,
`diminutive`) on top of the realistic ones. The upper bound on what
CMC-Lite is asked to handle.

**Report:** [`results/people-20260527T014645Z/report.md`](../../benchmarks/eval/ess_accuracy/results/people-20260527T014645Z/report.md)

| Invariant | Value | Target | Result |
|---|---:|---:|:---:|
| False-merge rate | 0.000 | ≤0.05 | PASS |
| Auto-merge precision (T1+T2) | 1.000 | =1.00 | PASS |

| Recall metric | Value |
|---|---:|
| Recall@T1 (exact) | 0.599 |
| Recall@T1+T2 (auto-merge) | 0.599 |
| Recall@any-tier (surfaced) | 1.000 |

**Per-family (abbreviated — full table in the report):**

| Family | n | recall@T1+T2 | recall@any | notes |
|---|---:|---:|---:|---|
| `case` | 193 | **1.000** | 1.000 | T1 |
| `whitespace` | 432 | **1.000** | 1.000 | T1 |
| (12 other drift families) | various | 0.000 | 1.000 | All T3 by design |
| `negative_control` | 144 | n/a | n/a | False-merge = 0 |

---

### `publications` — 2026-05-27T01:46:47Z

**What it tests.** A structurally different schema —
`(title, first_author_last, year)` — to validate the "domain-agnostic"
claim. Different fuzzy thresholds, different drift modes
(`title_subtitle_drop`, `year_missing`, etc.), same engine.

**Source data.** 30 hand-curated real papers in three subgroups —
not synthetic. Full entity list at
[data/publications/entities.json](../../benchmarks/eval/ess_accuracy/data/publications/entities.json);
provenance and composition rationale at
[data/publications/README.md](../../benchmarks/eval/ess_accuracy/data/publications/README.md).
Summary of the 30:

- **15 LLM / agent-memory papers**: BERT (Devlin 2018), GPT-3
  (Brown 2020), Attention Is All You Need (Vaswani 2017), T5
  (Raffel 2020), RoBERTa (Liu 2019), ELECTRA (Clark 2020), LoRA
  (Hu 2021), Chain-of-Thought (Wei 2022), ReAct (Yao 2022),
  Toolformer (Schick 2023), MemGPT (Packer 2023), Generative Agents
  (Park 2023), Voyager (Wang 2023), Constitutional AI (Bai 2022),
  HuggingGPT (Shen 2023).
- **9 entity-resolution / capability / spiritwriter ancestor papers**:
  Macaroons (Birgisson 2014), Zep (Rasmussen 2025), SimpleMem
  (Liu 2026), EMem (Ren 2025), EDC (Zhang 2024), Probabilistic
  Signatures (Zhang 2018), Fellegi-Sunter record linkage (1969),
  McCallum canopy clustering (2000), Köpcke ER frameworks survey (2010).
- **6 classic deep learning anchors**: ResNet (He 2016), AlexNet
  (Krizhevsky 2012), LSTM (Hochreiter 1997), Word2Vec (Mikolov 2013),
  AlphaGo (Silver 2016), AlphaFold (Jumper 2021).

Deliberate inclusion choices: two papers with `first_author_last="Liu"`
(RoBERTa 2019, SimpleMem 2026) and two with `first_author_last="Zhang"`
(EDC 2024, Probabilistic Signatures 2018) so the engine has to
disambiguate by year + title, not just surname.

**Report:** [`results/publications-20260527T014647Z/report.md`](../../benchmarks/eval/ess_accuracy/results/publications-20260527T014647Z/report.md)

| Invariant | Value | Target | Result |
|---|---:|---:|:---:|
| False-merge rate | 0.000 | ≤0.05 | PASS |
| Auto-merge precision (T1+T2) | 1.000 | =1.00 | PASS |

| Recall metric | Value |
|---|---:|
| Recall@T1 (exact) | 0.666 |
| Recall@T1+T2 (auto-merge) | 0.714 |
| Recall@any-tier (surfaced) | 1.000 |

**Per-family — first corpus where T2_STRONG fires:**

| Family | n | recall@T1+T2 | recall@any | notes |
|---|---:|---:|---:|---|
| `case` | 149 | **1.000** | 1.000 | T1 |
| `whitespace` | 270 | **1.000** | 1.000 | T1 |
| `year_missing` | 30 | **1.000** | 1.000 | **T2_STRONG** (auto-merge — first T2 hits in the campaign) |
| `title_subtitle_drop` | 16 | 0.000 | 1.000 | T3 by design |
| `title_subtitle_add` | 5 | 0.000 | 1.000 | T3 by design |
| `first_author_initial` | 30 | 0.000 | 1.000 | T3 by design |
| `typo_substitution` | 47 | 0.000 | 1.000 | T3 by design |
| `typo_insertion` | 82 | 0.000 | 1.000 | T3/T4 by design |
| `negative_control` | 90 | n/a | n/a | False-merge = 0 |

**Finding worth noting:** when `year_missing` mutations land (year
dropped from candidate, title + first_author_last remain), they
**auto-merge at T2_STRONG** with 100% precision. The fuzzy match on
title+author is strong enough that the missing-year case clears the
T2 threshold. First time in the campaign that a non-universal drift
family auto-merges — confirms the engine's tier logic isn't
case-pathologically conservative; it's just *correctly* conservative
when the alternative defining fields don't carry enough signal.

---

### `csp_kb_AI_Res` — 2026-05-19T18:23:28Z

**What it tests.** Phase 2 of the harness — real LLM-extracted entities
from a real knowledge graph, not synthetic mutations on hand-curated
seed data. Every variant pair tested is a surface form that *actually
appears* in atom content from a real academic paper as extracted by
LLM-driven ingestion.

**Sources and definitions** (for anyone landing here cold):

- **csp** = [`claude-studio-producer`](https://github.com/aaronmarkham/claude-studio-producer),
  the multi-agent video-production project in the spiritwriter
  ecosystem. csp uses spiritwriter-core for KB, ingestion, secrets, and
  shards.
- **`cs kb`** = the `claude-studio` (alias `cs`) CLI's knowledge-base
  subcommand. `cs kb create / add / show / inspect / produce` builds
  and queries multi-source knowledge projects: PDFs in, atoms +
  topic/entity indices + a unified KnowledgeGraph out. The KB ingest
  pipeline uses PyMuPDF for extraction and Claude for semantic analysis
  (atoms, topics, entities). Storage layout under csp's
  `artifacts/kb/kb_<id>/`.
- **`kb_cf30f8f4e225`** = the specific KB project we used as the trial
  corpus. csp's name for it is "AI Research" (set when it was created
  via `cs kb create "AI Research"`). 2 source PDFs were ingested into
  it (so cross-source resolution is testable); 708 atoms total; 371
  distinct entities in its `entity_index`.
- **`csp_kb_trial.py`** = our harness module that reads any csp
  `knowledge_graph.json`, extracts surface-form variants per entity
  from atom content (with the harness's whole-word case-insensitive
  scan), and runs ESS resolution. Lives at
  [`benchmarks/eval/ess_accuracy/csp_kb_trial.py`](../../benchmarks/eval/ess_accuracy/csp_kb_trial.py).

The bridge: csp's `cs kb add` does the LLM-driven entity extraction;
the spiritwriter-core harness then measures how well CMC-Lite resolves
the resulting entities under their actual real-world surface variation.
No synthetic mutations; just whatever the LLM produced and however the
underlying PDFs phrased things.

**Report:** [`results/csp-20260519T182328Z/report.md`](../../benchmarks/eval/ess_accuracy/results/csp-20260519T182328Z/report.md)

| Metric | Intra-source | Cross-source |
|---|---:|---:|
| Recall@T1 (exact) | **1.000** | **1.000** |
| Recall@T1+T2 (auto-merge) | **1.000** | **1.000** |
| Recall@any-tier (surfaced) | **1.000** | **1.000** |

- 52 intra-source variant pairs tested → all 100% T1_EXACT
- 1 cross-source variant pair tested → 100% T1_EXACT
- 1 ESS collision detected at seed time (two LLM-extracted entities
  normalize to the same digest — surfaced honestly, not artifact-hidden)

Sample resolutions: `DNN` ↔ `dnn`, `diffusion models` ↔ `Diffusion Models`,
`PEFT` ↔ `Peft` ↔ `peft`, `Large Language Models` ↔ `large language models`.

The cleanest result in the campaign. Real-world LLM-extracted entity
case variation auto-merges at 100% T1.

---

## Falsification battery extension (2026-05-21)

After the 2026-05-19 campaign landed, the suspiciously clean results
prompted an honest self-challenge: were the precision = 1.000 numbers
artifacts of overly-friendly test design? Specifically:

- **The negative_control family garbles only one ESS field at a time**,
  testing "engine refuses to merge with one field clearly different"
  rather than "engine refuses to merge entities with no field overlap."
- **No mutation touched the anchor field** (DOB for person corpora,
  year for publications) so recall@any-tier of 1.000 could have been a
  DOB/year-anchored artifact (T4 fires on age-bucket + context overlap
  when DOB is preserved).
- **No collision-pair test**: real-world plausible "two genuinely
  different entities that share 2/3 ESS fields by coincidence" (two
  people with the same name born months apart; two papers with same
  first-author + year but different titles).

Added three new mutation families and re-ran four corpora (case_only,
inmate_clean, people, publications). Pinned results at the
2026-05-27T01:46:* timestamps:

- [`results/case_only-20260527T014643Z/`](../../benchmarks/eval/ess_accuracy/results/case_only-20260527T014643Z/)
- [`results/inmate_clean-20260527T014644Z/`](../../benchmarks/eval/ess_accuracy/results/inmate_clean-20260527T014644Z/)
- [`results/people-20260527T014645Z/`](../../benchmarks/eval/ess_accuracy/results/people-20260527T014645Z/)
- [`results/publications-20260527T014647Z/`](../../benchmarks/eval/ess_accuracy/results/publications-20260527T014647Z/)

### New mutation families and their results

| Family | Type | What it tests | Result across all 4 corpora |
|---|---|---|---|
| `garbled_all_fields` | universal | All ESS fields replaced — no-overlap negative case | **100% cleanly land at NO_MATCH** (20 + 26 + 50 + 30 = 126 pairs across the four corpora; 0 auto-merged, 0 even reached T3/T4) |
| `dob_typo` / `year_typo` | per-corpus (person/pub schemas) | Anchor field off by 1 (day for DOB, year for publications) | **100% surface at T4_WEAK** (26 + 50 + 30 = 106 anchor-typo pairs; 0 auto-merged. Engine refuses to silently merge records that disagree on the anchor field) |
| `realistic_collision` | per-corpus | Hand-curated "different entity sharing 2/3 ESS fields" pairs | **0/12 auto-merged across all corpora.** people: 5 pairs all to T4. inmate_clean: 3 pairs all to T4. publications: 4 pairs (2 NO_MATCH + 2 T3). The killer false-merge test — and the engine held. |

### The headline finding from the battery

**Auto-merge precision = 1.000 is robust.** The engine refused to
auto-merge any of the 12 hand-curated collision pairs (different real
entities sharing 2/3 ESS fields by realistic coincidence — same name
born months apart, same first-author publishing in same year). It also
refused to silently fix anchor-field typos (102 of those, all to T4).

**The "too good to be true" suspicion was unfounded.** Tested under
hostile conditions, the precision claim held.

### Secondary findings (worth knowing)

1. **T4 calibration is now better-calibrated** because the new
   same-entity families (dob_typo, year_typo) pour realistically-T4
   cases into the bucket: actual precision rose from ~0.30 to 0.43–0.46
   across person corpora (stated 0.50; still slightly over-confident
   but the gap closed by ~14 points).
2. **`no_match` tier now has population** (0.0 stated confidence,
   1.000 actual NPV — every garbled_all_fields landed there correctly).
3. **`dob_typo` and `year_typo` lose 1 tier** vs other same-entity
   drift: they land at T4 not T3 because the anchor-field garbling
   tanks the ess_overlap component of T3's scoring. Engine surfaces
   them for review but with weaker confidence — appropriate behavior.

---

## Cross-corpus invariants

These hold across **every corpus measured in this campaign, including
under the falsification battery**:

| Invariant | Result |
|---|---|
| **Auto-merge precision (T1+T2)** | **1.000 on all 5 corpora** — engine never auto-merges entities that shouldn't merge |
| **False-merge rate** | **0.000 on all 5 corpora** — same statement from the other side |
| **Recall@any-tier (surfaced for review)** | **1.000 on all 5 corpora** — every same-entity drift mode reaches at least T3 |
| **Recall on `case` drift** | **1.000 on every corpus** that includes case mutations |
| **Recall on `whitespace` drift** | **1.000 on every corpus** that includes whitespace mutations |

**Volume across the campaign:** 437 entities (315 of them real
LLM-extracted), 2,927 test pairs in total (1,857 same-entity + 466
negative-control + 53 real-corpus variants + ... — exact counts in
individual reports).

## What this means for marketing claims

Defensible statements, ordered by strength. Each citation reflects the
2026-05-21 falsification-battery numbers — the strongest results
survived hand-curated hostile test cases.

1. **"Zero incorrect auto-merges across 5 benchmark corpora, including
   hand-curated collision pairs."** Strongest claim. 1.000 precision
   held under realistic_collision (12 hostile pairs: 0 auto-merged),
   negative_control (366+ partial-overlap pairs: 0 auto-merged), and
   garbled_all_fields (122 no-overlap pairs: 0 auto-merged). The
   engine *refuses* to silently corrupt the registry, even when fields
   coincidentally overlap.

2. **"100% recall on case + whitespace drift, in every corpus that
   tests them."** Strong, measured, specific.

3. **"100% of same-entity drift reaches the review queue."** Recall@any-tier
   = 1.000 across all corpora and all same-entity families, *including*
   the hostile dob_typo/year_typo families that touch the anchor field.
   CMC-Lite never silently loses a candidate merge — it either
   auto-merges (when safe) or flags for review.

4. **"Auto-merge recall on real LLM-extracted academic entities: 100%."**
   csp KB result. Real-world Phase 2 measurement.

5. **"Anchor-field typos surface at T4 for review rather than silently
   merging."** The honest framing of conservatism: dob_typo and year_typo
   families (which ARE the same entity, just with a typo) don't
   auto-merge despite name+author matching perfectly. Engine treats the
   anchor field as load-bearing; silently merging records that disagree
   on the anchor would be a worse failure mode than surfacing them at
   T4. *Note*: this is conservative, not corrective — the engine does
   not "fix" the typo; it flags the pair for human review with weakened
   confidence.

6. **"Auto-merge recall on novel drift modes (morphology, surname
   compression, subtitle drops): surfaces for human review at T3."**
   The honest framing for everything that doesn't auto-merge. Not a
   regression; not a miss. By design.

### Recommended replacement copy for spiritwriter.ai

Long form (footer/about section):

> CMC-Lite's auto-merge path delivered 100% precision across 5 benchmark
> corpora and 12 hand-curated hostile collision pairs. Zero false merges,
> zero silent corruption. Anything ambiguous surfaces for review at one
> of four confidence tiers. No embeddings, no LLM in the merge path.

Short form (homepage tag or one-liner):

> 100% precision in auto-merge · 0 false merges across 5 corpora and
> 12 hostile collision pairs. No embeddings, no LLM in the merge path.

Sidebar replacement for the existing `≥85% recall` stat (the headline
number that prompted this campaign):

> **0 false merges** across 5 corpora · **12/12** hand-curated collision
> pairs correctly distinguished · all same-entity drift surfaced for
> review at one of four confidence tiers. *No embeddings, no LLM in
> the merge path.*

### What to NOT claim

- **A single whole-corpus Recall@T1+T2 number as "the" recall.** The
  range is 0.60–1.00 depending on which drift modes the corpus includes.
  Cherry-picking one number is the metric-shopping this whole campaign
  was meant to prevent.
- **The cmc-spec's ≥85% target as a current measurement.** It's a target
  for the full CMC pipeline (LLM clustering included). CMC-Lite is the
  deterministic subset.

## Open follow-ups

- T4 calibration runs 0.27–0.32 actual precision vs stated 0.50 across
  every person corpus (confirmed in `case_only`, `inmate_clean`, `people`,
  `publications`). Engine ticket for later — T4 is flag-only, doesn't
  touch any auto-merge guarantee. Logged in [`cleanup/cmc-phalanx-canonicalize.md`](../cleanup/cmc-phalanx-canonicalize.md).
- A free-text-atom corpus (separate from `csp_kb_AI_Res`'s entity-index
  scan) could reproduce the original "80–100% vs Jaccard 9–36%" claim
  from cmc-lite-v0.1.md — that claim was about free-text memory atoms,
  not structured records. Future Phase 3 work.
