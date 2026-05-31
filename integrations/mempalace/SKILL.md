---
name: spiritwriter-mempalace
description: "Bridge between spiritwriter and MemPalace — content addressing, encryption, and entity resolution for MemPalace drawers; semantic search for spiritwriter shards. Install both, they find each other."
version: 0.1.0
homepage: https://github.com/aaronmarkham/spiritwriter-core
user-invocable: true
metadata:
  openclaw:
    emoji: "\U0001F517"
    os:
      - darwin
      - linux
      - win32
    requires:
      anyBins:
        - python3
    install:
      - id: spiritwriter-mempalace
        kind: pip
        label: "Install spiritwriter + MemPalace"
        package: "spiritwriter[mempalace]"
---

# spiritwriter + MemPalace

Two systems, complementary strengths, zero overlap:

| MemPalace owns | spiritwriter owns |
|---|---|
| Semantic search (ChromaDB embeddings) | Content-addressed storage (SHA-256) |
| BM25 keyword reranking | AES-256-GCM / NaCl encryption |
| Palace navigation (wings/rooms/drawers) | Entitlements & scoped access control |
| AAAK compression dialect | Entity resolution (CanonicalRegistry) |
| L0-L3 memory wake-up stack | Hash-chained provenance |
| Knowledge graph (SQLite) | IPFS distribution |

Install both and they discover each other automatically. Install order doesn't matter.

```bash
pip install spiritwriter mempalace
# or: pip install spiritwriter[mempalace]
```

## What Changes When Both Are Installed

### MemPalace gains

**Content addressing** — every drawer gets a SHA-256 content address. Tamper detection and free deduplication without embedding comparisons.

**Encryption at rest** — AES-256-GCM or NaCl sealed boxes for the entire palace. Same MemPalace API, encrypted on disk.

**Entity resolution across conversations** — MemPalace's entity_registry.py does regex disambiguation ("is 'Max' a person or a word?"). spiritwriter's `CanonicalRegistry` adds tiered confidence matching so "Max" in session 1 and "Max" in session 47 are linked as the same canonical entity.

**Revision history** — upserts create lineage chains via parent_shard_id. Walk the full edit history of any drawer.

**Provenance** — hash-chained JSONL audit trail of every memory read/write.

### spiritwriter gains

**Semantic search** — shards are retrievable by natural language query, not just content address or scope. MemPalace's hybrid BM25 + vector search finds relevant shards without knowing their IDs.

**Knowledge graph** — MemPalace's temporal entity-relationship graph (subject/predicate/object with valid_from/valid_to) is accessible through spiritwriter's EntityProvider protocol.

## Usage

### Auto-discovery

```python
from spiritwriter.integrations import available_providers, get_provider

# Both installed — MemPalace auto-discovered
providers = available_providers()  # {"mempalace": <MemPalaceProvider>}

mp = get_provider("mempalace")
print(mp.is_available())  # True if a palace exists with drawers
print(mp.count())         # number of drawers
```

### Semantic search over shards

```python
from spiritwriter.integrations.base import SearchQuery

results = mp.search(SearchQuery(text="database migration", top_k=5))
for r in results:
    print(f"{r.score:.3f} | {r.text[:80]}...")
```

### Shard-backed drawers

```python
from spiritwriter.integrations.mempalace import ShardBackend

backend = ShardBackend("~/.mempalace/shards")

backend.add(
    documents=["Max had his first swim meet today."],
    ids=["drawer_001"],
    metadatas=[{"wing": "family", "room": "swimming"}],
)

# Content address
print(backend.get_shard_id("drawer_001"))  # SHA-256

# Revision history
backend.upsert(
    documents=["Max won his second swim meet — backstroke PB!"],
    ids=["drawer_001"],
    metadatas=[{"wing": "family", "room": "swimming"}],
)
history = backend.get_drawer_history("drawer_001")
# [latest, original] — linked via parent_shard_id
```

### Encrypted palace

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()
backend = ShardBackend("~/.mempalace/shards", encryption_key=key)
# Same API. Encrypted on disk. Survives restarts.
```

### Cross-conversation entity resolution

```python
from spiritwriter.integrations.mempalace import EntityBridge

bridge = EntityBridge("~/.mempalace/entities.db")

# Session 1
r1 = bridge.resolve_person("Max", context={
    "wing": "family", "relationship": "son",
}, source_id="session_01")

# Session 47 — same person, different room
r2 = bridge.resolve_person("Max", context={
    "wing": "family", "relationship": "son",
}, source_id="session_47")

print(r2.tier)        # T1_EXACT
print(r2.confidence)  # 0.95
```

## Overhead

Benchmarked with the provider harness (`benchmarks/bench_providers.py`):

| Operation | Overhead |
|---|---|
| Content addressing per drawer | ~9ms |
| Encryption per drawer | +0.2ms |
| Drawer retrieval | 0.6ms avg |
| Entity resolution (known) | 0.022ms |

MemPalace's 96.6% recall is unchanged. spiritwriter adds the trust layer for ~9ms per write.

## Token Efficiency

MemPalace stores verbatim text and returns full conversation chunks (~500 tokens per hit). spiritwriter stores structured atoms and hydrates only what's needed (~50 tokens per shard). When used together, you get MemPalace's retrieval quality with spiritwriter's context efficiency — same recall, fewer tokens burned.

Benchmarks for token savings are in progress.

## License

spiritwriter: Apache 2.0. MemPalace: MIT. Fully compatible.
