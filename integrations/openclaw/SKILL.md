---
name: spiritwriter
description: "Local-first agent memory — content-addressed shards, encryption, entity resolution, provenance. No third-party services. Your intents and memories stay on your machine."
version: 0.1.0
homepage: https://github.com/aaronmarkham/spiritwriter-core
user-invocable: false
metadata:
  openclaw:
    emoji: "\U0001F9E0"
    os:
      - darwin
      - linux
      - win32
    requires:
      anyBins:
        - python3
    install:
      - id: spiritwriter-core
        kind: pip
        label: "Install spiritwriter-core"
        package: spiritwriter-core
      - id: spiritwriter-sealed
        kind: pip
        label: "Install with sealed-box encryption (NaCl)"
        package: "spiritwriter-core[sealed]"
      - id: spiritwriter-full
        kind: pip
        label: "Install everything (sealed + IPFS network)"
        package: "spiritwriter-core[sealed,network]"
---

# spiritwriter — Agent Memory That Stays Local

spiritwriter-core was built because agent memory shouldn't cost $100/day in token burn, and your intents shouldn't flow through third-party services. It gives your agent structured, encrypted, content-addressed memory — stored locally, recalled efficiently, with a full provenance chain.

Battle-tested in production since February 2026 with OpenClaw agents.

## Why This Exists

Most AI memory systems store raw conversation text and replay it into context. That's expensive (hundreds of tokens per recall) and sends your intents through external services. spiritwriter stores **structured atoms** — entity/key/value triples — so your agent recalls only what it needs. A 500-token conversation chunk becomes a 50-token shard with the same information.

No API keys. No cloud services. No telemetry. Your memory, your machine. IPFS distribution is opt-in for multi-agent sharing; core memory is always local.

## Getting Started with OpenClaw

spiritwriter is a Python library, not a CLI tool. Your agent uses it via Python API calls — typically in hooks or agent code that runs alongside your OpenClaw session.

```bash
# Install
pip install spiritwriter-core

# From your agent code or hooks:
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.trace.store import ShardStore

store = ShardStore("~/.openclaw/shards")
```

spiritwriter works with whatever vector DB your OpenClaw agent already uses. If you also install MemPalace, spiritwriter auto-discovers it and adds semantic search over shards — see the `mempalace` integration skill.

## What Your Agent Gets

### Structured memory (not raw text dumps)

```python
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.trace.store import ShardStore

store = ShardStore("~/.openclaw/shards")

# Store structured knowledge — not raw conversation
shard = MemoryShard(
    atoms=[
        ShardAtom(text="Project uses FastAPI for the web layer",
                  kind=AtomKind.FACT, entity="myproject",
                  key="web_framework", value="FastAPI"),
        ShardAtom(text="Never deploy on Fridays",
                  kind=AtomKind.CONVENTION, entity="team",
                  key="deploy_policy", value="no-friday-deploys"),
        ShardAtom(text="Switched to PostgreSQL for ACID guarantees",
                  kind=AtomKind.DECISION, entity="myproject",
                  key="session_backend", value="postgresql"),
    ],
    scope="project:myproject",
    origin="my-agent",
    decay_class=DecayClass.STABLE,
)

ref = store.put(shard)
# Content-addressed: same content = same ID, always
# Immutable: edits create new shards linked via parent_shard_id
```

### Recall only what's needed (token efficiency)

```python
# Hydrate as injectable agent context
context = shard.hydrate_context()
# Returns ~50 tokens of structured XML, not 500 tokens of raw conversation

# Estimate token cost before hydrating
print(shard.token_estimate)  # rough count

# Resolve by scope — get all project context
project_shards = store.by_scope("project:myproject")

# Named refs — like git branches for "latest" pointers
store.set_ref("project-myproject", shard.shard_id)
latest = store.resolve_ref("project-myproject")
```

### Encryption (your memory, not theirs)

```python
from spiritwriter.trace.crypto import generate_job_key

# AES-256-GCM — encrypt at rest
key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)
# On disk: ciphertext. In memory: plaintext only during access.

# NaCl sealed boxes — zero-knowledge (even the operator can't decrypt)
from spiritwriter.trace.sealed import generate_owner_keypair, seal_shard
keypair = generate_owner_keypair()
sealed = seal_shard(shard, keypair.public_key)
```

### Entity resolution (Phalanx) — who is "Max"?

```python
from spiritwriter.trace.canonicalize import CanonicalRegistry, CanonicalSchema

schema = CanonicalSchema(
    name="person",
    ess_fields=["name", "relationship"],
    fuzzy_fields={"name": 0.85},
)

with CanonicalRegistry("~/.openclaw/entities.db", schema) as registry:
    # Session 1: "my son Max had a swim meet"
    result = registry.resolve({"name": "Max", "relationship": "son"})
    registry.upsert({"name": "Max", "relationship": "son"},
                     result, source_name="chat", source_id="session-1")

    # Session 47: "Max's chess tournament is Saturday"
    result = registry.resolve({"name": "Max", "relationship": "son"})
    # T1 exact match — same canonical entity across conversations
```

### Provenance (tamper-evident audit trail)

```python
from spiritwriter.trace.emitter import TraceEmitter, verify_chain

tracer = TraceEmitter(run_id="session-42", agent_id="my-agent",
                      out_path="~/.openclaw/trace.jsonl")
tracer.shard_created(shard.shard_id, shard.scope, len(shard.atoms))

# Every event is hash-chained — tampering breaks the chain
assert verify_chain(tracer.get_events())
```

## With MemPalace

If [MemPalace](https://github.com/MemPalace/mempalace) is also installed, spiritwriter auto-discovers it and adds semantic search over your shards. MemPalace handles retrieval (vector search, BM25 reranking), spiritwriter handles trust (encryption, provenance, entity resolution). See the `mempalace` integration skill for details.

```bash
pip install spiritwriter-core[mempalace]
```

## Decay Classes

Shards have a lifecycle — not everything lives forever:

| Class | TTL | Use for |
|---|---|---|
| `PERMANENT` | Never | Identities, architecture decisions |
| `STABLE` | 90 days | Project context, learned patterns |
| `ACTIVE` | 14 days | Current tasks, active monitoring |
| `SESSION` | 24 hours | Debugging context, temp state |
| `CHECKPOINT` | 4 hours | Pre-flight state saves |

```python
# Auto-prune expired shards
pruned = store.prune_expired()
```

## License

Apache 2.0
