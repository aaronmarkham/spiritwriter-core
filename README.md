# spiritwriter-core

Content-addressed agent memory for AI systems. Create, store, encrypt, share, and recall structured knowledge — with built-in provenance, access control, and entity resolution.

## What It Does

- **Memory Shards** — Immutable, content-addressed bundles of structured knowledge (SHA-256, Git-style object layout)
- **Shard Store** — Local-first file storage with scope queries, named refs, and DHT-ready network fallback
- **Encryption** — AES-256-GCM for agent-to-agent sharing; NaCl sealed boxes for zero-knowledge storage
- **Entitlements** — Scoped access tokens with capability checks, budget tracking, and per-shard decryption keys
- **Entity Resolution (Phalanx)** — Domain-agnostic canonicalization with tiered confidence matching (T1-T4), based on the Consensus Memory Canonicalization (CMC) spec
- **Tracing** — Hash-chained JSONL provenance logs with optional Ed25519 signing
- **IPFS Distribution** — Publish and resolve shards over a private IPFS swarm

## Install

```bash
pip install -e .                      # core
pip install -e ".[sealed]"            # + NaCl sealed boxes
pip install -e ".[network]"           # + IPFS backend
pip install -e ".[dev,sealed,network]"  # everything
```

Requires Python 3.9+.

## Quick Start

```python
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.trace.store import ShardStore

# Create a shard
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

# Store it
store = ShardStore("~/.myapp/shards")
ref = store.put(shard)

# Retrieve by content address
retrieved = store.get(ref.shard_id)

# Hydrate as agent context
context = retrieved.hydrate_context()
```

## Encryption

```python
from spiritwriter.trace.crypto import generate_job_key, encrypt_shard, decrypt_shard

key = generate_job_key()
encrypted = encrypt_shard(shard, key)
decrypted = decrypt_shard(encrypted, key)
```

Zero-knowledge (operator can't decrypt):

```python
from spiritwriter.trace.sealed import generate_owner_keypair, seal_shard, unseal_shard

keypair = generate_owner_keypair()
sealed = seal_shard(shard, keypair.public_key)
decrypted = unseal_shard(sealed, keypair.private_key)
```

## Entity Resolution

```python
from spiritwriter.trace.canonicalize import CanonicalRegistry, CanonicalSchema

schema = CanonicalSchema(
    name="person",
    ess_fields=["last_name", "first_name", "dob"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
)

with CanonicalRegistry("/tmp/people.db", schema) as registry:
    result = registry.resolve({"last_name": "Smith", "first_name": "John", "dob": "1990-05-12"})
    cid = registry.upsert(result_dict, result, "source_a", "001")
```

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | Installation, core concepts, quick examples |
| [Memory Shards](docs/memory-shards.md) | Atoms, decay classes, hydration, content addressing |
| [Shard Store](docs/shard-store.md) | Storage layout, named refs, scope queries, maintenance |
| [Encryption](docs/encryption.md) | AES-GCM, NaCl sealed boxes, entitlements |
| [Entity Resolution](docs/entity-resolution.md) | Phalanx (CMC-Lite): ESS, tiered matching, batch processing |
| [Tracing](docs/tracing.md) | Hash-chained provenance, chain verification |
| [Integration Guide](docs/integration-guide.md) | How frio, perseus-news, and claude-studio-producer use it |
| [API Reference](docs/api-reference.md) | Complete public API surface |

## Benchmarks

```bash
python -m pytest benchmarks/ -v -s
```

See [benchmarks/README.md](benchmarks/README.md) for details on what's measured and how to interpret results.

## Architecture

```
spiritwriter/
├── models/      # Data models (DocumentAtom, KnowledgeProject)
├── secrets/     # OS keychain API key management
├── classify/    # Content/theme classification
├── llm/         # LLM provider abstraction (Anthropic)
├── ingest/      # PDF document ingestion
├── kb/          # Knowledge base CRUD
└── trace/       # Memory shard system
    ├── shard.py         # MemoryShard, ShardAtom, ShardRef
    ├── store.py         # ShardStore (Git-style content addressing)
    ├── crypto.py        # AES-256-GCM encryption
    ├── sealed.py        # NaCl sealed boxes, Ed25519 signing
    ├── entitlement.py   # Scoped access tokens
    ├── canonicalize.py  # CMC-Lite entity resolution
    ├── emitter.py       # Hash-chained trace events
    ├── network.py       # NetworkResolver protocol
    └── backends/
        └── ipfs.py      # IPFS/Kubo backend
```

## Used By

- **[frio](https://frio.help)** — Zero-knowledge jail roster monitoring (encrypted search shards, fuzzy name matching)
- **[texascrime.org](https://texascrime.org)** — Dual-perspective enforcement news with cross-consumer shard sharing
- **[podcasts.spiritwriter.ai](https://podcasts.spiritwriter.ai)** — AI-generated podcasts from multi-agent video production

## Tests

```bash
python -m pytest tests/ -v
```

## License

Apache 2.0
