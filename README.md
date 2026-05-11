# spiritwriter-core

Content-addressed agent memory for AI systems. Create, store, encrypt, share, and recall structured knowledge — with built-in provenance, access control, entity resolution, and delegated sub-agent jobs.

## What It Does

- **Memory Shards** — knowledge that grows without losing history. New observations supersede old ones via lineage links; identical content from different agents dedupes into a single record. Decay classes (`PERMANENT`, `STABLE`, `ACTIVE`, `SESSION`, `CHECKPOINT`) prune what shouldn't outlive its purpose. *(Content-addressed: SHA-256 over atoms + scope + origin.)*

- **Shard Store** — local-first storage on disk; transparently fetches missing shards from a network when one is configured. Named refs (mutable pointers to immutable shards) handle the "latest version of X" pattern without breaking content addressing. *(Git-style object layout; optional IPFS L2 fallback.)*

- **Encryption** — two layers picked by who you don't trust. AES-256-GCM when the operator and key-holder cooperate. NaCl sealed boxes when the operator must not see content — multi-tenant hosting, source protection, zero-knowledge monitoring.

- **Entitlements** — delegate scoped access to a sub-agent without handing over master keys. Tokens bundle decryption keys + scope patterns + capabilities + budget; the store enforces every constraint before decrypting.

- **Delegated Jobs** — package encrypted content + task + entitlement into one unit of sub-agent work. The orchestrator hands the package over; the sub-agent hydrates, executes, returns a result shard. Every step traced.

- **Entity Resolution (Phalanx)** — tell entities apart even when names collide ("Bear" the dog vs. "Bear" the brand) and merge them when surface forms diverge ("Carlos Martinez" vs. "MARTINEZ, CARLOS A"). Same primitive handles both. *(See [The Bear Problem](#the-bear-problem) below.)*

- **Tracing** — replay exactly what an agent did, prove nothing's been edited, render the run as workflow / genealogy / multi-agent diagrams. Useful for debugging expensive failures, auditing before deploy, or proving a run's integrity to a third party. *(Hash-chained JSONL with optional Ed25519 signing.)*

- **IPFS Distribution** — share shards across machines without running a database. Publish to a private IPFS swarm; consumers transparently fetch missing shards from the network and cache locally.

- **Android Audits** — tamper-evident security audits for APKs. Inputs, evidence, findings, and final report are bound into a hash-chained trace plus a self-hashing witness — anyone with the APK can re-run verification offline.

## Install

```bash
pip install -e .                        # core
pip install -e ".[sealed]"              # + NaCl sealed boxes
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
context = store.hydrate([ref])      # XML-tagged context ready for prompt injection
```

See [docs/getting-started.md](docs/getting-started.md) for the layered model and use-case reading paths.

## Encryption

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)        # AES-256-GCM, operator can decrypt with key
decrypted = store.decrypt_and_get(encrypted.shard_id, key)
```

Zero-knowledge (operator can't decrypt):

```python
from spiritwriter.fabric.sealed import generate_owner_keypair

keypair = generate_owner_keypair()
sealed = store.seal_and_store(shard, keypair.public_key)   # only owner's private key opens it
decrypted = store.unseal_and_get(sealed.shard_id, keypair.private_key)
```

## Entity Resolution (Phalanx)

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

For why this works — and why we built it the way we did — see below.

## The Bear Problem

You're extracting facts about Aaron from a stack of documents. Document 1 surfaces "Bear is Aaron's favorite." Document 2: "Aaron and Bear were at the park." Document 3: "Aaron's dog Bear, a 10-year-old black lab / border collie mix (a Borador)."

Each document gives partial defining-field coverage, and your extractor classifies Bear three different ways: a name in Document 1, a generic animal in Document 2, a specific dog in Document 3. Three identifiers for the same entity, and they don't align. A naive system keeps them separate (you have three Bears, no convergence as more documents arrive) or collapses by surface name alone (now Bear-the-dog merges with Bear-the-beer brand mentioned in Document 4). Embedding-based systems hallucinate the boundaries — they score "Bear" the dog close to "Bear" the bear close to "Bear" the brand, and the merge decisions become unauditable.

Phalanx hashes the *defining fields* (name + entity type + owner + …) into an **Entity Sense Signature** — a deterministic identity hash. As more documents land, defining fields accumulate per entity. Document 1 gives `name=Bear, owner=Aaron`. Document 3 adds `entity_type=dog, breed=borador`. The growing field set produces a stable ESS the moment you have enough fields to disambiguate. Fields not yet known don't penalize the match — they're absent from the hash, and ESS overlap rewards the fields you *do* share.

The same primitive handles the inverse: "Carlos Martinez", "MARTINEZ, CARLOS A", and "C. Martinez" across three rosters dedupe into one entity, because their defining fields normalize to the same hash regardless of surface spelling.

### Resolution Tiers

| Tier | Match | Action |
|------|-------|--------|
| T1 | Exact ESS digest | Auto-merge |
| T2 | High fuzzy quality + high ESS overlap | Auto-merge |
| T3 | Fuzzy with lower combined score | Flag, don't merge |
| T4 | Weak context overlap | Flag only |

### Tech Stack

Two layers, one per concern:

- **`CanonicalRegistry`** — one SQLite file. The entity-resolution index. Three tables: `entities`, `sightings`, `merges`. WAL mode for concurrent readers.
- **`ShardStore`** — content-addressed JSON-LD atoms on disk. The underlying knowledge the registry points at.

The registry holds *which canonical entity each sighting maps to*; the shards hold *what the entity actually is*. Same architecture whether you're running on a laptop or a multi-node deployment. See [Memory Shards](docs/memory-shards.md) and [Shard Store](docs/shard-store.md) for the atom and storage layers.

### Why These Design Choices

- **Local-first.** A `CanonicalRegistry` is one SQLite file (and the shards it points at are plain JSON-LD on disk). No service to run, no vector DB to host, no daemon to keep alive. The registry *is* the artifact — email it, version-control it, copy it between machines, restore it from a backup.

- **Deterministic before fuzzy.** Auto-merge only at T1 and T2 (the upper rows of the table above). Anything weaker becomes a flagged merge event for human review. False merges are the worst failure mode in entity resolution, and silent ones are unauditable. Phalanx fails loud.

- **No LLM in the auto-merge path.** LLMs hallucinate, and for entity resolution that means silently combining records of two different people. Deterministic + fuzzy with explicit tiers is verifiable end-to-end; LLM judgment isn't. Use an LLM upstream to extract atoms from text if you want; keep it out of the merge decision.

- **Schema-driven, domain-agnostic.** Same engine handles people, products, papers, articles — anything where you can name the defining fields. Tier thresholds are tunable per domain. The schema's hash is stored in the registry on first open; reopening with a different schema raises `ValueError` rather than silently misclassifying records as a different domain's entities.

- **Lightweight to bootstrap.** No embedding model to train or host. No GPU. No vector index to rebuild on schema change. Goes from `pip install` to resolving entities in seconds, on a laptop, offline.

### The Numbers

≥85% recall on semantic duplicates with ≤5% false-merge rate. No embeddings, no LLM calls — SQLite, normalization, and string matching. The full spec draws on academic prior art (EDC/EMNLP 2024, Graphiti/Zep, SimpleMem, EMem-G); Phalanx pulls the three highest-impact ideas — content-addressed identity, tiered escalation, overlapping-window extraction — and ships them with zero new infrastructure.

**Deeper:** [Entity Resolution guide](docs/entity-resolution.md), [CMC-Lite spec](docs/specs/cmc-spec-v0.1.md).

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | installation, layered model, use-case reading paths |
| [Memory Shards](docs/memory-shards.md) | atoms, decay classes, hydration, content addressing |
| [Shard Store](docs/shard-store.md) | storage layout, named refs, scope queries, maintenance |
| [Encryption](docs/encryption.md) | AES-GCM, NaCl sealed boxes, threat model |
| [Entitlements](docs/entitlements.md) | bearer tokens, capabilities, budget, scope enforcement |
| [Jobs](docs/jobs.md) | packaging delegated sub-agent work; issuer / runner sides |
| [Shard Postures](docs/shard-postures.md) | choosing the trust model — encryption, signing, scope, decay, distribution as one dial |
| [Entity Resolution](docs/entity-resolution.md) | Phalanx (CMC-Lite): ESS, tiered matching, batch processing |
| [Tracing](docs/tracing.md) | hash-chained provenance, chain verification, signed traces |
| [Traced Workflows](docs/traced-workflows.md) | multi-stage pipelines with checkpoint/resume; CSP as worked example |
| [Network Distribution](docs/network-distribution.md) | IPFS backend, manifests, private swarm, L1/L2 resolution |
| [Audit](docs/audit.md) | tamper-evident Android APK security audits |
| [Integration Guide](docs/integration-guide.md) | how frio, perseus-news, and Claude Studio Producer use it |
| [API Reference](docs/api-reference.md) | complete public API surface |

## Examples

The `examples/` directory contains self-contained demos that exercise the fabric APIs end-to-end — no LLM calls, no network, plain Python functions composing shards, traces, entitlements, and jobs. Each demo runs with `python examples/NN_xxx/run.py` and exits 0.

| Demo | What it shows |
|------|---------------|
| [01_simple_trace](examples/01_simple_trace/) | Parent packages a job, spawns a subagent, receives a result shard — two independent hash-chained traces |
| [02_todo_fanout](examples/02_todo_fanout/) | Compound request split into 4 subagents, each writing a result shard with `source_ref` lineage, assembled by the parent |
| [03_skills_and_tools](examples/03_skills_and_tools/) | Agent uses skills and tools to plan a trip; every invocation recorded with input/output hashes |
| [04_governance_divergence](examples/04_governance_divergence/) | Same job run twice — Run A behaves, Run B exceeds budget and capabilities; parent detects violations via trace |

Run the test suite with `python -m pytest tests/test_demos.py -v`.

## Benchmarks

```bash
python -m pytest benchmarks/ -v -s
```

See [benchmarks/README.md](benchmarks/README.md) for what's measured and how to interpret results.

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
│   ├── canonicalize.py  # CMC-Lite entity resolution
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
├── integrations/   # Third-party integration adapters (mempalace, ...)
├── kb/             # Knowledge base CRUD
├── llm/            # LLM provider abstraction (Anthropic)
├── models/         # DocumentAtom, KnowledgeProject
├── secrets/        # OS keychain API key management
└── stopwords.py    # Centralized stopword list
```

## Integrations

spiritwriter-core ships with a pluggable memory-provider protocol (`spiritwriter/integrations/base.py`) so any external memory system can be backed by content-addressed shards. One adapter is in-tree:

- **[mempalace](https://github.com/aaronmarkham/mempalace)** — atomic memory store with decay-based recall and contextual entity weighting. The `spiritwriter/integrations/mempalace/` adapter wires mempalace's API to spiritwriter's shard store and entity registry.

The same protocol can plug in **Mem0**, **Zep**, **Mastra**, or any custom memory layer — implement `MemoryProvider` and `MemoryBackend` in your adapter; spiritwriter handles shard storage, entity resolution, encryption, and tracing on the back end.

## Used By

- **[frio](https://frio.help)** — zero-knowledge jail roster monitoring (encrypted search shards, fuzzy name matching)
- **[texascrime.org](https://texascrime.org)** — dual-perspective enforcement news with cross-consumer shard sharing
- **[podcasts.spiritwriter.ai](https://podcasts.spiritwriter.ai)** — AI-generated podcasts from multi-agent video production
- **[Claude Studio Producer](https://github.com/aaronmarkham/claude-studio-producer)** — media production pipeline; the canonical worked example in `traced-workflows.md`

## Tests

```bash
python -m pytest tests/ -v                              # full suite
python -m pytest tests/test_demos.py -v                 # the four examples above
python -m pytest tests/test_ipfs_backend.py -v -m ipfs  # IPFS integration (requires Kubo)
```

## License

Apache 2.0
