# spiritwriter-core

**Agent memory you own.**

Durable, content-addressed memory · provable traces · scoped delegation · entity resolution — local-first, with no service to run and no data handed over. Drop it under whatever orchestrator and retriever you already use.

---

If you've built more than one agentic system, you've rebuilt the same glue every time. Where does what the agent learned live, and how do you keep it from drifting into contradiction? How do you hand a sub-task to another agent without handing over the keys to everything? And three steps later, when something went wrong — can you prove what actually happened?

Most teams rebuild that layer per app, suffer the misalignments and delegation failures and memory drift, or rent it from a managed service that takes custody of their data and their latency budget. Nobody does *provable* tracing or scoped entitlements without heavy infrastructure. There's no standard library for the layer **beneath** the agent.

spiritwriter is that layer.

## What it is

A **trust-configurable memory substrate**. One set of primitives — content-addressed memory shards, hash-chained traces, scoped entitlements, deterministic entity resolution — that you dial from *fully public and provable* to *fully private and zero-knowledge* by changing one thing: the shard's [posture](docs/shard-postures.md).

It is **not** an agent framework and **not** a vector database. It's the layer those sit on:

```
  your orchestrator   (LangGraph / CrewAI / a raw loop)     ← bring your own
  your retriever      (vector DB / RAG / full-text search)  ← bring your own
  ─────────────────────────────────────────────────────────
  spiritwriter        memory · provenance · delegation       ← yours: local-first, data never leaves
                      · entity resolution
```

Bring your own everything-else. spiritwriter handles what those layers leave out: durable memory that doesn't drift, traces you can prove to a third party, delegation you can actually scope, and entity records that don't duplicate or collide. It's additive — you don't migrate, you slip it underneath what you already run. And because it's local-first, your data never leaves your machine: nothing to provision, nothing to meter, almost nothing to switch off if you change your mind. The registry is a file you own, not a row in someone else's database.

## Proven at both extremes

The substrate is real because two live products run on it at opposite ends of the trust dial — same primitives, opposite postures:

| | [news.spiritwriter.ai](https://news.spiritwriter.ai) | [frio.help](https://frio.help) |
|---|---|---|
| **Posture** | maximum transparency | maximum privacy |
| **What it does** | articles atomized, rewritten across the spectrum, every variant linked back to its source | families searching for an incarcerated relative; rosters matched, alerts sent |
| **What you can see** | the full lineage — follow a fact as it mutates | nothing — searches are sealed from the operator; matching happens in memory |

One library wrote both. The only difference is the posture.

## Install

```bash
pip install -e .                        # core
pip install -e ".[sealed]"              # + NaCl sealed boxes (zero-knowledge)
pip install -e ".[network]"             # + IPFS backend
pip install -e ".[dev,sealed,network]"  # everything
```

Requires Python 3.9+.

## Quick Start

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.fabric.store import ShardStore

store = ShardStore("~/.myapp/shards")

shard = MemoryShard(
    atoms=[
        ShardAtom(text="Project uses FastAPI", kind=AtomKind.FACT,
                  entity="myproject", key="framework", value="FastAPI"),
        ShardAtom(text="Always run migrations before deploying",
                  kind=AtomKind.CONVENTION, entity="myproject",
                  key="deploy_rule", value="migrations-first"),
    ],
    scope="project:myproject",
    origin="dev-agent",
    decay_class=DecayClass.STABLE,
)

ref = store.put(shard)              # idempotent — same content, same ID
context = store.hydrate([ref])      # XML-tagged context, ready for prompt injection
```

That's the whole onramp: store what the agent learned, hydrate it back into a prompt later. Everything below is the same substrate dialed up — encryption, delegation, provenance, resolution. See [docs/getting-started.md](docs/getting-started.md) for the layered model and use-case reading paths.

> **Teach it to your agent, not just your app.** Each capability ships with an agent-readable skill (`skills/*/SKILL.md`). An agent can learn the primitives by reading a skill — no install, no integration code on your side.

## What it ends

Each capability maps to a problem you'd otherwise re-solve by hand:

- **Memory that doesn't drift** — *Memory Shards.* Knowledge grows without losing history: new observations supersede old ones via lineage links; identical content from different agents dedupes into one record; decay classes (`PERMANENT`, `STABLE`, `ACTIVE`, `SESSION`, `CHECKPOINT`) prune what shouldn't outlive its purpose. Content-addressed (SHA-256 over atoms + scope + origin), so the same content is always the same ID.
- **Storage you own** — *Shard Store.* Local-first on disk, Git-style object layout. Named refs (mutable pointers to immutable shards) give you "latest version of X" without breaking content addressing. Optional network fetch when a backend is configured.
- **Privacy as a setting** — *Encryption.* AES-256-GCM when the operator and key-holder cooperate; NaCl sealed boxes when the operator must *not* see content (multi-tenant hosting, source protection, zero-knowledge services).
- **Delegation you can scope** — *Entitlements + Jobs.* Hand a sub-agent a token bundling decryption keys + scope patterns + capabilities + budget; the store enforces every constraint before it decrypts. Package content + task + entitlement into one unit of work; the sub-agent hydrates, runs, returns a result shard. Every step traced.
- **Proof of what happened** — *Tracing.* Hash-chained JSONL, optionally Ed25519-signed. Replay a run, prove nothing's been edited, render it as workflow / genealogy / multi-agent diagrams — for debugging expensive failures, auditing before deploy, or proving a run's integrity to a third party.
- **Entities that don't collide** — *Entity Resolution.* Tell "Bear" the dog from "Bear" the brand; merge "Carlos Martinez" and "MARTINEZ, CARLOS A" into one. Deterministic-then-fuzzy, no embeddings, no LLM in the merge path. (See [The Bear Problem](#the-bear-problem) below.)
- **Sharing without a database** — *IPFS distribution.* Publish shards to a private swarm; consumers fetch missing shards from the network and cache locally.
- **Tamper-evident audits** — *Android APK audits.* Inputs, evidence, findings, and report bound into a hash-chained trace plus a self-hashing witness — anyone with the APK can re-run verification offline.

## Encryption

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)        # AES-256-GCM, operator can decrypt with the key
decrypted = store.decrypt_and_get(encrypted.shard_id, key)
```

Zero-knowledge (operator can't decrypt — this is the posture `frio` runs):

```python
from spiritwriter.fabric.sealed import generate_owner_keypair

keypair = generate_owner_keypair()
sealed = store.seal_and_store(shard, keypair.public_key)   # only the owner's private key opens it
decrypted = store.unseal_and_get(sealed.shard_id, keypair.private_key)
```

## Entity Resolution

```python
from spiritwriter.fabric.canonicalize import CanonicalRegistry, CanonicalSchema

schema = CanonicalSchema(
    name="person",
    ess_fields=["last_name", "first_name", "dob"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
)

candidate = {"last_name": "Smith", "first_name": "John", "dob": "1990-05-12"}
with CanonicalRegistry("/tmp/people.db", schema) as registry:
    result = registry.resolve(candidate)
    cid = registry.upsert(candidate, result, "source_a", "001")
```

The interesting part is *why* this resolves correctly without an embedding model or an LLM in the loop — that's the Bear Problem, below.

## The Bear Problem

You're extracting facts about Aaron from a stack of documents. Document 1 surfaces "Bear is Aaron's favorite." Document 2: "Aaron and Bear were at the park." Document 3: "Aaron's dog Bear, a 10-year-old black lab / border collie mix (a Borador)."

Each document gives partial defining-field coverage, and your extractor classifies Bear three different ways: a name in Document 1, a generic animal in Document 2, a specific dog in Document 3. Three identifiers for the same entity, and they don't align. A naive system keeps them separate (you have three Bears, no convergence as more documents arrive) or collapses by surface name alone (now Bear-the-dog merges with Bear-the-beer brand mentioned in Document 4). Embedding-based systems hallucinate the boundaries — they score "Bear" the dog close to "Bear" the bear close to "Bear" the brand, and the merge decisions become unauditable.

The resolver hashes the *defining fields* (name + entity type + owner + …) into an **Entity Sense Signature (ESS)**, a deterministic identity hash. As more documents land, defining fields accumulate per entity. Document 1 gives `name=Bear, owner=Aaron`. Document 3 adds `entity_type=dog, breed=borador`. The growing field set produces a stable ESS the moment you have enough fields to disambiguate. Fields not yet known don't penalize the match — they're absent from the hash, and ESS overlap rewards the fields you *do* share.

The same primitive handles the inverse: "Carlos Martinez", "MARTINEZ, CARLOS A", and "C. Martinez" across three rosters dedupe into one entity, because their defining fields normalize to the same hash regardless of surface spelling. (One caveat worth knowing up front: the registry normalizes only case and whitespace — anything more is the caller's job. See [Normalize before you resolve](docs/entity-resolution.md#normalize-before-you-resolve).)

### Resolution Tiers

| Tier | Match | Action |
|------|-------|--------|
| T1 | Exact ESS digest | Auto-merge |
| T2 | High fuzzy quality + high ESS overlap | Auto-merge |
| T3 | Fuzzy with lower combined score | Flag, don't merge |
| T4 | Weak context overlap | Flag only |

### Tech Stack

Two layers, one per concern:

- **`CanonicalRegistry`** — one SQLite file. The entity-resolution index: three tables (`entities`, `sightings`, `merges`), WAL mode for concurrent readers.
- **`ShardStore`** — content-addressed JSON-LD atoms on disk. The underlying knowledge the registry points at.

The registry holds *which canonical entity each sighting maps to*; the shards hold *what the entity actually is*. Same architecture whether you're on a laptop or a multi-node deployment. See [Memory Shards](docs/memory-shards.md) and [Shard Store](docs/shard-store.md).

### Why These Design Choices

- **Local-first.** A `CanonicalRegistry` is one SQLite file; the shards it points at are plain JSON-LD. No service to run, no vector DB to host, no daemon to keep alive. The registry *is* the artifact — email it, version-control it, copy it between machines, restore it from a backup.
- **Deterministic before fuzzy.** Auto-merge only at T1 and T2. Anything weaker becomes a flagged event for human review. False merges are the worst failure mode in entity resolution, and silent ones are unauditable. The resolver fails loud.
- **No LLM in the auto-merge path.** LLMs hallucinate, and for entity resolution that means silently combining records of two different people. Deterministic + fuzzy with explicit tiers is verifiable end-to-end; LLM judgment isn't. Use an LLM upstream to extract atoms if you want; keep it out of the merge decision.
- **Schema-driven, domain-agnostic.** Same engine handles people, products, papers, articles — anything where you can name the defining fields. Tier thresholds tune per domain. The schema's hash is stored on first open; reopening with a different schema raises `ValueError` rather than silently misclassifying records.
- **Lightweight to bootstrap.** No embedding model to train or host, no GPU, no vector index to rebuild on schema change. From `pip install` to resolving entities in seconds, on a laptop, offline.

### The Numbers

**100% auto-merge precision — 0 incorrect merges across 5 benchmark corpora**, and it catches 100% of same-entity matches: it auto-merges at T1/T2 and flags everything weaker for review, so nothing slips through silently. No embeddings, no LLM calls — SQLite, normalization, and string matching. See [docs/benchmarks/runs-log.md](docs/benchmarks/runs-log.md) for the measurements and the falsification battery behind them.

The full spec ([docs/specs/cmc-spec-v0.1.md](docs/specs/cmc-spec-v0.1.md)) draws on academic prior art (EDC/EMNLP 2024, Graphiti/Zep, SimpleMem, EMem-G); the implementation pulls the three highest-impact ideas — content-addressed identity, tiered escalation, and [shingled extraction](docs/shingled-extraction.md) — and ships them with zero new infrastructure.

**Deeper:** [Entity Resolution guide](docs/entity-resolution.md), [Shingled Extraction](docs/shingled-extraction.md), [CMC-Lite spec](docs/specs/cmc-lite-v0.1.md).

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | installation, the layered model, use-case reading paths |
| [Memory Shards](docs/memory-shards.md) | atoms, decay classes, hydration, content addressing |
| [Atoms](docs/atoms.md) | what's flexible vs not, worked examples for every AtomKind |
| [Shard Store](docs/shard-store.md) | storage layout, named refs, scope queries, maintenance |
| [Shard Postures](docs/shard-postures.md) | the trust dial — encryption, signing, scope, decay, distribution as one setting |
| [Encryption](docs/encryption.md) | AES-GCM, NaCl sealed boxes, threat model |
| [Entitlements](docs/entitlements.md) | bearer tokens, capabilities, budget, scope enforcement |
| [Jobs](docs/jobs.md) | packaging delegated sub-agent work; issuer / runner sides |
| [Entity Resolution](docs/entity-resolution.md) | ESS, tiered matching, normalization, batch processing |
| [Shingled Extraction](docs/shingled-extraction.md) | overlapping-window extraction with multi-pass consensus |
| [Tracing](docs/tracing.md) | hash-chained provenance, chain verification, signed traces |
| [Traced Workflows](docs/traced-workflows.md) | multi-stage pipelines with checkpoint/resume |
| [Network Distribution](docs/network-distribution.md) | IPFS backend, manifests, private swarm, L1/L2 resolution |
| [Substrate Flavor](docs/substrate-flavor.md) | wire format + verification rules for library-free implementers in any language |
| [Audit](docs/audit.md) | tamper-evident Android APK security audits |
| [Integration Guide](docs/integration-guide.md) | how frio, perseus-news, and Claude Studio Producer use it |
| [API Reference](docs/api-reference.md) | complete public API surface |

## Examples

Self-contained demos that exercise the fabric APIs end-to-end — no LLM calls, no network, plain Python composing shards, traces, entitlements, jobs, and resolution. Each runs with `python examples/NN_xxx/run.py` and exits 0.

| Demo | What it shows |
|------|---------------|
| [01_simple_trace](examples/01_simple_trace/) | Parent packages a job, spawns a subagent, receives a result shard — two independent hash-chained traces |
| [02_todo_fanout](examples/02_todo_fanout/) | Compound request split into 4 subagents, each writing a result shard with `source_ref` lineage, assembled by the parent |
| [03_skills_and_tools](examples/03_skills_and_tools/) | Agent uses skills and tools to plan a trip; every invocation recorded with input/output hashes |
| [04_governance_divergence](examples/04_governance_divergence/) | Same job run twice — Run A behaves, Run B exceeds budget and capabilities; parent detects violations via trace |
| [05_delegation_with_trace](examples/05_delegation_with_trace/) | Per-key delegation: root → orchestrator → 3 workers, each with its own Ed25519 leaf cap; signed shards trace back to the event that produced them |
| [06_phalanx_flow](examples/06_phalanx_flow/) | Full pipeline — paper → shingled chunking → atoms → memory shard → delegated job → entity resolution, all under one trace |

Run them under test with `python -m pytest tests/test_demos.py -v`.

## Benchmarks

```bash
python -m pytest benchmarks/ -v -s
```

See [benchmarks/README.md](benchmarks/README.md) for what's measured and how to read it, and [docs/benchmarks/runs-log.md](docs/benchmarks/runs-log.md) for the tracked measurements over time.

## Architecture

```
spiritwriter/
├── audit/          # Tamper-evident Android APK security audits
├── classify/       # Content/theme classification
├── fabric/         # Shards, store, encryption, entitlements, jobs, traces, network
│   ├── shard.py         # MemoryShard, ShardAtom, ShardRef
│   ├── store.py         # ShardStore (Git-style content addressing)
│   ├── crypto.py        # AES-256-GCM encryption
│   ├── sealed.py        # NaCl sealed boxes, Ed25519 signing
│   ├── entitlement.py   # Scoped access tokens
│   ├── canonicalize.py  # Entity resolution (CanonicalRegistry, ESS, tiers)
│   ├── emitter.py       # Hash-chained trace events
│   ├── extract.py       # Atom extraction utilities
│   ├── visualize.py     # Mermaid diagram rendering
│   ├── network.py       # NetworkResolver protocol
│   ├── jobs.py          # JobSpec, package_job
│   ├── runner.py        # hydrate_job, BudgetTracker, create_result_shard
│   └── backends/
│       └── ipfs.py      # IPFS / Kubo backend
├── geo/            # Geographic types and view shards (experimental)
├── ingest/         # Document ingestion (PDF)
├── integrations/   # Third-party memory-provider adapters (mempalace, ...)
├── kb/             # Knowledge base CRUD
├── llm/            # LLM provider abstraction (Anthropic)
├── models/         # DocumentAtom, KnowledgeProject
├── secrets/        # OS keychain API key management
├── sw_vocab/       # Terminology canonicalization for spiritwriter's own docs
└── stopwords.py    # Centralized stopword list
```

## Integrations

spiritwriter-core ships a pluggable memory-provider protocol (`spiritwriter/integrations/base.py`) so any external memory system can be backed by content-addressed shards. One adapter is in-tree:

- **[mempalace](https://github.com/aaronmarkham/mempalace)** — atomic memory store with decay-based recall and contextual entity weighting. The `spiritwriter/integrations/mempalace/` adapter wires it to the shard store and entity registry.

The same protocol can plug in **Mem0**, **Zep**, **Mastra**, or any custom memory layer — implement `MemoryProvider` and `MemoryBackend`, and spiritwriter handles shard storage, entity resolution, encryption, and tracing underneath.

## Used By

Two postures, several products:

- **[frio.help](https://frio.help)** — *zero-knowledge.* Jail-roster monitoring with encrypted search shards and fuzzy name matching; the operator can't see who searched.
- **[news.spiritwriter.ai](https://news.spiritwriter.ai)** / **[texascrime.org](https://texascrime.org)** — *fully transparent.* Source → agent → variant news with public lineage and cross-consumer shard sharing.
- **[podcasts.spiritwriter.ai](https://podcasts.spiritwriter.ai)** — AI-generated podcasts from multi-agent video production.
- **[Claude Studio Producer](https://github.com/aaronmarkham/claude-studio-producer)** — media production pipeline; the canonical worked example in [traced-workflows.md](docs/traced-workflows.md).

## Tests

```bash
python -m pytest tests/ -v                              # full suite
python -m pytest tests/test_demos.py -v                 # the demos above
python -m pytest tests/test_ipfs_backend.py -v -m ipfs  # IPFS integration (requires Kubo)
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for release notes (0.8.0+). Pre-1.0 SemVer: **minor** for breaking changes, **patch** for additive/non-breaking changes.

## License

Apache 2.0
