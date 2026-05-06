# spiritwriter-core

Content-addressed agent memory for AI systems. Create, store, encrypt, share, and recall structured knowledge — with built-in provenance, access control, entity resolution, and delegated sub-agent jobs.

## What It Does

- **Memory Shards** — immutable, content-addressed bundles of structured knowledge (SHA-256, Git-style object layout)
- **Shard Store** — local-first file storage with scope queries, named refs, and DHT-ready network fallback
- **Encryption** — AES-256-GCM for agent-to-agent sharing; NaCl sealed boxes for zero-knowledge storage
- **Entitlements** — bearer tokens that bundle decryption keys + scope patterns + capabilities + budget
- **Delegated Jobs** — package encrypted content + task + entitlement into a unit of sub-agent work; every step traced
- **Entity Resolution (Phalanx)** — domain-agnostic canonicalization with tiered confidence matching (T1–T4), based on the Consensus Memory Canonicalization (CMC) spec
- **Tracing** — hash-chained JSONL provenance logs with optional Ed25519 signing; render as Mermaid workflow / genealogy / multi-agent diagrams
- **IPFS Distribution** — publish and resolve shards over a private IPFS swarm, with cache-on-fetch L2 fallback
- **Android Audits** — tamper-evident security audits for APKs (`spiritwriter.audit`)

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
├── stopwords.py    # Centralized stopword list
└── trace/          # Deprecated shim re-exporting fabric/ (removed in 0.6.0)
```

## Used By

- **[frio](https://frio.help)** — zero-knowledge jail roster monitoring (encrypted search shards, fuzzy name matching)
- **[texascrime.org](https://texascrime.org)** — dual-perspective enforcement news with cross-consumer shard sharing
- **[podcasts.spiritwriter.ai](https://podcasts.spiritwriter.ai)** — AI-generated podcasts from multi-agent video production
- **[Claude Studio Producer](https://github.com/aaronmarkham/claude-studio-producer)** — media production pipeline; the canonical worked example in `traced-workflows.md`

## Tests

```bash
python -m pytest tests/ -v                    # full suite
python -m pytest tests/test_demos.py -v       # the four examples above
python -m pytest tests/test_ipfs_backend.py -v -m ipfs   # IPFS integration (requires Kubo)
```

## License

Apache 2.0
