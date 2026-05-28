# CSP KB ESS Trial — 2026-05-19T18:23:28Z
source: `C:\Users\aaron\Documents\GitHub\claude-studio-producer\artifacts\kb\kb_cf30f8f4e225\knowledge_graph.json`

## Corpus shape

- Entities in `entity_index`: 371
- Entities scanned (after short/common filter): 315
- Distinct ESS canonicals after seeding: 314
- ESS collisions at seed time: 1 (LLM-extracted entities that normalize to the same ESS digest)
- Entities with multiple surface forms: 50
- Intra-source variant pairs tested: 52
- Cross-source variant pairs tested: 1

## Resolution accuracy

| metric | intra-source | cross-source |
|---|---:|---:|
| Recall@T1 (exact) | 1.000 | 1.000 |
| Recall@T1+T2 (auto-merge) | 1.000 | 1.000 |
| Recall@any tier (surfaced) | 1.000 | 1.000 |

## Tier distribution

**Intra-source:**
  - `t1_exact`: 52

**Cross-source:**
  - `t1_exact`: 1

## Sample resolutions

| canonical | variant | tier | conf | scope |
|---|---|---|---:|---|
| `DNN` | `dnn` | `t1_exact` | 0.95 | intra |
| `diffusion models` | `Diffusion Models` | `t1_exact` | 0.95 | intra |
| `Large Language Models` | `large language models` | `t1_exact` | 0.95 | intra |
| `PEFT` | `Peft` | `t1_exact` | 0.95 | intra |
| `PEFT` | `peft` | `t1_exact` | 0.95 | intra |

## What this trial validates

- **Real-corpus mode** for the ESS accuracy harness: consumes a csp `knowledge_graph.json` produced by `cs kb add`. No synthetic mutations — every pair is a surface form that actually appears in atom content from a real paper.
- **Cross-source resolution** specifically: when the same entity surfaces in different documents with different surface forms, does the registry merge them?
- **Honest limits**: pluralization (DNN/DNNs) typically lands at NO_MATCH on surface strings alone. See `docs/benchmarks/ess-accuracy-spec.md` for why — this is by design at the CMC-Lite layer, and is what the full CMC pipeline's LLM clustering stage is for.