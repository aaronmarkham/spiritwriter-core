# Getting Started with spiritwriter-core

spiritwriter-core is a Python library for **content-addressed agent memory**. It gives AI agents a way to create, store, encrypt, share, and recall structured knowledge — with built-in provenance, access control, and entity resolution.

## Install

```bash
# Core (memory shards, shard store, AES encryption, entity resolution)
pip install -e .

# With sealed-box encryption (zero-knowledge, NaCl)
pip install -e ".[sealed]"

# With IPFS network distribution
pip install -e ".[network]"

# Everything (dev, sealed, network)
pip install -e ".[dev,sealed,network]"
```

Requires Python 3.9+.

## Core Concepts

### Memory Shards

A **MemoryShard** is the fundamental unit of distributable agent memory. Think of it as a content-addressed bundle of structured knowledge:

```python
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass

# Create a shard with structured knowledge atoms
shard = MemoryShard(
    atoms=[
        ShardAtom(
            text="Project uses FastAPI for the web layer",
            kind=AtomKind.FACT,
            entity="myproject",
            key="web_framework",
            value="FastAPI",
        ),
        ShardAtom(
            text="Always run migrations before deploying",
            kind=AtomKind.CONVENTION,
            entity="myproject",
            key="deploy_rule",
            value="migrations-first",
        ),
        ShardAtom(
            text="Switched from Redis to PostgreSQL for session storage",
            kind=AtomKind.DECISION,
            entity="myproject",
            key="session_backend",
            value="postgresql",
        ),
    ],
    scope="project:myproject",
    origin="dev-agent",
    decay_class=DecayClass.STABLE,  # 90-day TTL
    tags=["myproject", "architecture"],
)

print(shard.shard_id)  # deterministic SHA-256 content address
```

Every shard has a **content address** — a SHA-256 hash of its atoms, scope, and origin. Same content always produces the same ID. Changing any atom creates a new shard with a new ID.

### Shard Store

The **ShardStore** persists shards to disk using a Git-style object layout:

```python
from spiritwriter.trace.store import ShardStore

store = ShardStore("~/.myapp/shards")

# Store a shard (idempotent — same content = no-op)
ref = store.put(shard)
print(ref.shard_id)  # same as shard.shard_id

# Retrieve by content address
retrieved = store.get(ref.shard_id)
assert retrieved.atoms[0].value == "FastAPI"

# Query by scope
project_shards = store.by_scope("project:myproject")

# Named refs (like git branches) for "latest" pointers
store.set_ref("project-myproject", shard.shard_id)
latest = store.resolve_ref("project-myproject")
```

### Encryption

Two layers of encryption protect shards at rest and in transit:

**AES-256-GCM** — symmetric encryption for agent-to-agent sharing:

```python
from spiritwriter.trace.crypto import generate_job_key, encrypt_shard, decrypt_shard

key = generate_job_key()  # 32-byte random key
encrypted = encrypt_shard(shard, key)

# Store encrypted (scope visible, content hidden)
store.put_encrypted(encrypted)

# Decrypt with key
decrypted = store.decrypt_and_get(encrypted.shard_id, key)
```

**NaCl Sealed Boxes** — asymmetric encryption for zero-knowledge scenarios:

```python
from spiritwriter.trace.sealed import generate_owner_keypair, seal_shard, unseal_shard

# Owner generates keypair (private key = capability key)
keypair = generate_owner_keypair()

# Service seals shard — only owner can decrypt
sealed = seal_shard(shard, keypair.public_key)
store.put_sealed(sealed)

# Owner decrypts with their private key
decrypted = unseal_shard(sealed, keypair.private_key)
```

### Entity Resolution (Phalanx)

The **CanonicalRegistry** is the runtime component of Phalanx (spiritwriter's entity resolution system, based on the [Consensus Memory Canonicalization](specs/cmc-spec-v0.1.md) spec). It resolves entities across records using tiered confidence matching:

```python
from spiritwriter.trace.canonicalize import (
    CanonicalRegistry, CanonicalSchema, canonicalize_batch,
)

schema = CanonicalSchema(
    name="person",
    ess_fields=["last_name", "first_name", "dob"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
    context_fields=["facility", "gender"],
)

with CanonicalRegistry("/tmp/people.db", schema) as registry:
    # Resolve a candidate
    result = registry.resolve({
        "last_name": "Smith",
        "first_name": "John",
        "dob": "1990-05-12",
    })
    print(result.tier)        # ResolutionTier.NO_MATCH (first time)
    print(result.confidence)  # 0.0

    # Persist the entity
    cid = registry.upsert(
        {"last_name": "Smith", "first_name": "John", "dob": "1990-05-12"},
        result, source_name="roster_a", source_id="001",
    )

    # Same person, different source — resolves to T1
    result2 = registry.resolve({
        "last_name": "Smith",
        "first_name": "John",
        "dob": "1990-05-12",
    })
    print(result2.tier)  # ResolutionTier.T1_EXACT
```

### Trace & Provenance

The **TraceEmitter** produces hash-chained JSONL logs — a tamper-evident audit trail:

```python
from spiritwriter.trace.emitter import TraceEmitter, verify_chain

emitter = TraceEmitter(
    run_id="run-001",
    agent_id="my-agent",
    out_path="/tmp/trace.jsonl",
)

emitter.shard_created(shard.shard_id, shard.scope, len(shard.atoms))
emitter.shard_resolved(shard.shard_id, by_agent="sub-agent")

# Verify chain integrity
events = emitter.get_events()
assert verify_chain(events)
```

## What's Next

- [Memory Shards](memory-shards.md) — deep dive into atoms, decay, hydration
- [Shard Store](shard-store.md) — storage layout, named refs, scope queries
- [Encryption & Sealed Shards](encryption.md) — AES-GCM, NaCl sealed boxes, entitlements
- [Entity Resolution (Phalanx)](entity-resolution.md) — ESS, tiered matching, batch processing
- [Network Distribution](network-distribution.md) — IPFS backend, manifests, L1/L2 fallback
- [Tracing & Provenance](tracing.md) — hash-chained events, chain verification
- [Integration Guide](integration-guide.md) — how frio, perseus-news, and claude-studio-producer use spiritwriter-core
- [API Reference](api-reference.md) — complete public API surface
