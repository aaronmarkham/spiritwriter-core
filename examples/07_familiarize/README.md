# 07_familiarize — docs folder → CMC-aligned, agent-ready brief

The streamlined answer to a common pain: *every session you tell the agent
to "get familiar with my app", then watch it fumble through your docs
re-deriving the same facts.* This demo does that derivation **once**, with
an LLM, **aligns** the result along the CMC spec
([`docs/specs/cmc-spec-v0.1.md`](../../docs/specs/cmc-spec-v0.1.md)) so
semantic duplicates collapse, and stores it as a content-addressed shard
the agent hydrates on startup. One named ref instead of N file reads, and a
brief whose quality compounds because it's read every future session.

```
./sample_app/*.md
      │  extract_atoms_llm()   atoms + key_definition + entity sense
      ▼
  ShardAtom[]
      │  align_atoms()         tiered: exact → sense-gate → LLM adjudicate
      ▼
  MemoryShard ──set_ref──▶ "project-familiarity"
      │  hydrate_context()
      ▼
  <shard>…</shard>            injected at agent startup
```

## Run it

```bash
python examples/07_familiarize/run.py
```

Deterministic, offline, free. A `MockLLMProvider` answers **both** the
extraction and the adjudication prompts, so it's repeatable in CI. The
extraction and alignment code is **real** — only the model responses are
canned. To run against a live model, swap one line in `run.py`:

```python
provider = AnthropicProvider()      # needs ANTHROPIC_API_KEY
```

## Why alignment is LLM-tiered, not string-fuzzy

Fuzzy string matching on extracted atoms gets only **20-30% recall** on
true semantic duplicates ([CMC §1](../../docs/specs/cmc-spec-v0.1.md)). So
alignment doesn't compare spellings — it compares **definitions and
senses**, and asks the LLM about the genuinely hard pairs. Per Graphiti's
pattern (CMC §9.1) it stays **tiered**, not all-LLM, to avoid variance and
token burn:

| Tier | What | Cost |
|---|---|---|
| 1. Exact | `(entity, key, kind)` match after normalization → merge, union sources | deterministic |
| 1b. Sense gate | incompatible `scoped_to`/`domain` ⇒ can't be the same entity | deterministic |
| 2. LLM adjudicate | the ambiguous residual: `SAME` / `DIFFERENT` / `SUBSUMES` | LLM, gated |

On the sample docs this resolves what string matching cannot:

- **`Postgres` ⇄ `PostgreSQL`** are recognized as the **same entity** and
  unified (both facts end up under `PostgreSQL`).
- **`database` and `datastore`** — different keys, same meaning — merge by
  **definition**, not spelling.
- the **`database` fact** stated in two docs collapses to one atom citing
  **both** (`doc:README.md, doc:BACKLOG.md`).
- only **3 LLM calls** fire for the whole corpus — the gate keeps the model
  off the easy pairs.

## The sense gate (the "Bear Problem")

Tier 1's fold is itself sense-gated, so identical surface forms with
*incompatible* senses never merge — `Bear` (a dog, `scoped_to: Aaron`)
stays distinct from `bear` (wildlife) even though both normalize to the
same token. Without this, lowercasing alone would silently fuse them. See
[CMC §5.5](../../docs/specs/cmc-spec-v0.1.md) and
`TestAlignAtoms::test_sense_gate_blocks_incompatible_before_llm`.

## CMC enrichment on every atom

Extraction emits the two load-bearing CMC signals, persisted on the stored
`ShardAtom` (kept out of its content hash, so enrichment doesn't fork
identity):

- **`key_definition`** — the EDC "Define" step; what powers `database` ≈
  `datastore` matching.
- **`sense`** — `sense_type` / `scoped_to` / `domain`; what powers the gate.

Deferred (matches [cmc-lite §7](../../docs/specs/cmc-lite-v0.1.md)): an
embedding tier to pre-rank candidates before the LLM, abstraction chains,
and multi-pass consensus voting.

## Library entry point

The demo is a thin driver over a reusable API:

```python
from spiritwriter.fabric.familiarize import familiarize
from spiritwriter.llm import AnthropicProvider
from spiritwriter.fabric.store import ShardStore

result = await familiarize(
    "./docs", AnthropicProvider(), ShardStore("./shards"),
    "project-familiarity", scope="app:myproject",
)
brief = ShardStore("./shards").resolve_ref("project-familiarity").hydrate_context()
```

`sources` is anything [`spiritwriter.ingest.load_documents`](../../spiritwriter/ingest/loaders.py)
accepts — a directory of **markdown / text / PDF**, a single file, or a
pre-loaded `{source_ref: text}` dict. `ingest` is the multi-format
front-end; `familiarize` aligns whatever it yields into one KB. (PDF needs
PyMuPDF; for rich single-PDF structure use `ingest.DocumentIngestor`.)

See [`spiritwriter/fabric/familiarize.py`](../../spiritwriter/fabric/familiarize.py)
(`extract_atoms_llm`, `align_atoms`, `familiarize`).

## How this differs from 06

[06_phalanx_flow](../06_phalanx_flow/) demos the same *shape* but
hand-curates extraction and wraps the result in entitlements, delegated
jobs, and a verifiable trace — it teaches provenance and delegation. This
demo runs extraction **for real** and aligns with the CMC tiered matcher,
so it shows the one thing the "get familiar with my app" customer wants:
docs → aligned brief, end to end, with only the model call mocked.

## Tests

`tests/test_demos.py::TestDemo07Familiarize` (ref resolves, restated fact
cites both docs, near-variant entity unified, CMC enrichment round-trips)
plus `TestAlignAtoms` (exact tier without a provider, LLM entity
unification, sense gate) and `TestExtractAtomsLLM`.

## Related

- [`docs/specs/cmc-spec-v0.1.md`](../../docs/specs/cmc-spec-v0.1.md) — the full CMC pipeline this follows
- [`docs/specs/cmc-lite-v0.1.md`](../../docs/specs/cmc-lite-v0.1.md) — the pragmatic subset + deferrals
- [skills/shards/SKILL.md](../../skills/shards/SKILL.md) — the shard API this builds on
