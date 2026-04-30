# Getting Started with spiritwriter-core

AI agents pick up knowledge faster than they can keep track of it. The same agent that discovered "this codebase uses FastAPI for the web layer" needs that fact again ten minutes later, or a sub-agent needs it, or a teammate's agent needs it tomorrow. The shapes that solve this — vector stores, prompt-stuffing, ad-hoc JSON files — each fail somewhere: no audit trail, no access control, no way to dedupe across agents, no way to expire stale knowledge.

**spiritwriter-core** is a Python library for content-addressed agent memory. Knowledge lives in **shards** — immutable, SHA-256-addressed bundles with provenance, scope-based access control, decay metadata, and optional encryption. Shards persist locally, distribute over IPFS, hydrate into agent context on demand, and carry hash-chained traces of every action taken with them.

This doc walks the core model end-to-end, then points at the deep-dive docs for each piece.

## Install

```bash
# Core: shards, store, AES-GCM encryption, entity resolution
pip install -e .

# Add zero-knowledge encryption (NaCl sealed boxes)
pip install -e ".[sealed]"

# Add IPFS-based distribution
pip install -e ".[network]"

# Everything (core + sealed + network + dev tools)
pip install -e ".[dev,sealed,network]"
```

Requires Python 3.9+.

## A Tiny End-to-End

The minimum useful shape is: create a shard, store it, hand a ref to a sub-agent, hydrate the ref into prompt context. About 20 lines:

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.fabric.store import ShardStore

store = ShardStore("~/.myapp/shards")

# Capture knowledge as structured atoms
shard = MemoryShard(
    atoms=[
        ShardAtom(text="Project uses FastAPI for the web layer",
                  kind=AtomKind.FACT, entity="myproject",
                  key="web_framework", value="FastAPI"),
        ShardAtom(text="Always run migrations before deploying",
                  kind=AtomKind.CONVENTION, entity="myproject",
                  key="deploy_rule", value="migrations-first"),
    ],
    scope="project:myproject",
    origin="dev-agent",
    decay_class=DecayClass.STABLE,    # 90-day TTL
    tags=["myproject", "architecture"],
)

ref = store.put(shard)                # idempotent — same content, same ID

# Sub-agent gets the cheap ref, hydrates locally
context = store.hydrate([ref])
# <shard scope="project:myproject" label="myproject">
# - [fact] myproject.web_framework = FastAPI
# - [convention] myproject.deploy_rule = migrations-first
# </shard>
```

That's the loop. The orchestrator passes refs across agent boundaries; each agent calls `hydrate()` on its own store to materialize content. Lightweight orchestration, heavy content stays at the edge.

Everything else in the library — encryption, entity resolution, hash-chained traces, network distribution — composes on top of this shape.

## Core Concepts

The library is layered. Each layer adds one capability on top of the layer below.

### Shards

A **MemoryShard** is the unit of knowledge — an immutable bundle of structured atoms with an entitlement scope and a decay class. The `shard_id` is `SHA-256(atoms + scope + origin)`, so same content always produces the same ID, and two agents producing identical knowledge produce a single object.

Atoms have a `kind` (`FACT`, `DECISION`, `CONVENTION`, `INSTRUCTION`, ...) that controls how they render at hydration time. The kind isn't decorative — it signals intent to consuming agents.

**Deep dive:** [memory-shards.md](memory-shards.md)

### Shard Store

The **ShardStore** persists shards to disk using a Git-style content-addressed object layout — shards fan out into directories by the first two hex chars of their ID, named refs (mutable pointers, like Git branches) live alongside in `refs/`, and a `scope -> [shard_id]` index lives in `index.json` for fast scope queries.

```python
store.put(shard)                          # idempotent
store.get(shard.shard_id)                 # by content address
store.by_scope("project:myproject")       # all shards in a scope
store.set_ref("latest-myproject", sid)    # mutable name -> immutable shard
```

Plug in a network resolver and `get()` becomes a two-tier lookup: local first, IPFS fallback, cache-on-fetch.

**Deep dive:** [shard-store.md](shard-store.md)

### Encryption

Two encryption layers cover different operator trust boundaries:

```python
# AES-256-GCM — symmetric. Operator can decrypt with the key.
from spiritwriter.fabric.crypto import generate_job_key
key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)

# NaCl sealed-box — asymmetric, zero-knowledge. Only the owner's private key opens it.
from spiritwriter.fabric.sealed import generate_owner_keypair
keypair = generate_owner_keypair()
sealed = store.seal_and_store(shard, keypair.public_key)
```

Pick AES when the operator and the key-holder cooperate. Pick sealed-box when the operator must not see content — multi-tenant hosting, source protection, zero-knowledge monitoring.

**Entitlement tokens** package a decryption key + scope patterns + capabilities + budget into a delegatable bearer credential. A sub-agent presents the token; the store validates expiry → `SHARD_READ` → per-shard scope match before decrypting.

**Deep dive:** [encryption.md](encryption.md)

### Entity Resolution

Three rosters list "Martinez, Carlos", "MARTINEZ, CARLOS A", and "C. Martinez" — same DOB, same person? **Phalanx** is the entity resolution system; the **CanonicalRegistry** is its runtime engine. Domain-agnostic — supply a schema, get tiered confidence resolution backed by Entity Sense Signatures (content-addressed identity anchors) and fuzzy matching.

```python
from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry, CanonicalSchema, ResolutionTier,
)

schema = CanonicalSchema(
    name="person",
    ess_fields=["last_name", "first_name", "dob"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
    context_fields=["facility", "gender"],
)

with CanonicalRegistry("/tmp/people.db", schema) as registry:
    result = registry.resolve({
        "last_name": "Smith", "first_name": "John", "dob": "1990-05-12",
    })
    # result.tier in {T1_EXACT, T2_STRONG, T3_FUZZY, T4_WEAK, NO_MATCH}
    # result.confidence in [0.0, 0.95]
```

T1 and T2 auto-merge; T3 and T4 create merge events for review. No embedding model, no LLM calls — SQLite, normalization, and tiered scoring.

**Deep dive:** [entity-resolution.md](entity-resolution.md)

### Tracing

The **TraceEmitter** writes hash-chained JSONL events for a tamper-evident audit trail. Each event SHA-256s its own payload and links to the previous event's hash; mutate any field after the fact and the chain breaks.

```python
from spiritwriter.fabric.emitter import TraceEmitter, verify_chain

emitter = TraceEmitter(
    run_id="run-2026-04-29-001",
    agent_id="my-agent",
    out_path="/tmp/trace.jsonl",
)

emitter.shard_created(shard_id=shard.shard_id, scope=shard.scope,
                      atom_count=len(shard.atoms))
emitter.shard_resolved(shard_id=shard.shard_id, by_agent="sub-agent")

events = emitter.get_events()
assert verify_chain(events)        # False on any tamper, mid-chain removal, or reordering
```

Optional Ed25519 signing adds non-repudiation. Render traces as Mermaid (workflow / genealogy / multi-agent diagrams) for human-readable provenance reports.

**Deep dive:** [tracing.md](tracing.md), [traced-workflows.md](traced-workflows.md)

## Reading Paths by Use Case

The deep-dive docs aren't a linear tutorial — they're reference material organized by capability. Pick a path:

| If you're building... | Read in this order |
|-----------------------|---------------------|
| Memory for a single agent | [memory-shards](memory-shards.md) → [shard-store](shard-store.md) |
| Memory shared across agents | + [encryption](encryption.md) (entitlements section) |
| A multi-stage agent pipeline | + [traced-workflows](traced-workflows.md) (uses CSP as the worked example) |
| A zero-knowledge service | + [encryption](encryption.md) (sealed-box section) |
| Cross-source entity dedup | [entity-resolution](entity-resolution.md) |
| Distributed agent memory | + [network-distribution](network-distribution.md) |
| Audit infrastructure | [tracing](tracing.md) |
| Anything — full surface | [api-reference](api-reference.md), [integration-guide](integration-guide.md) |

## What spiritwriter-core Is Not

Worth being explicit about scope:

- **Not a vector store.** No embeddings, no semantic search at this layer. Shards are SHA-256-addressed by content, not by meaning. If you need semantic retrieval, layer it on top — index your shards in your favorite vector DB and treat shards as the canonical store.
- **Not a database.** Queries are limited to scope (indexed) and entity (scan). No SQL, no joins, no full-text search.
- **Not an agent framework.** It's the memory and provenance substrate. Bring your own orchestrator (LangGraph, plain asyncio, the Anthropic SDK directly).
- **Not a queue.** Shards have no ordering beyond `created_at`; `parent_shard_id` gives you lineage, not a stream.
- **Not concurrent-safe across writers on one store.** One writer per `ShardStore`, or coordinate at the application layer.

For richer query patterns and semantic retrieval, see the higher-level `kb/` module (knowledge-base manager that uses spiritwriter primitives underneath).
