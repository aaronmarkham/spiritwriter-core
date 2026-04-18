---
name: mempalace
description: "spiritwriter + MemPalace integration — adds content addressing, encryption, entity resolution (Phalanx), and provenance to MemPalace drawers. Install both, they find each other."
version: 0.4.0
homepage: https://github.com/aaronmarkham/spiritwriter-core
user-invocable: true
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
      - id: spiritwriter-pip
        kind: pip
        label: "Install spiritwriter-core (memory shards, encryption, entity resolution)"
        package: spiritwriter-core
      - id: spiritwriter-sealed
        kind: pip
        label: "Install with sealed-box encryption (NaCl)"
        package: "spiritwriter-core[sealed]"
      - id: spiritwriter-mempalace
        kind: pip
        label: "Install with MemPalace integration"
        package: "spiritwriter-core[mempalace]"
      - id: spiritwriter-full
        kind: pip
        label: "Install everything (sealed + network + mempalace)"
        package: "spiritwriter-core[sealed,network,mempalace]"
---

# spiritwriter-core — Trust Layer for AI Memory

Content-addressed agent memory with encryption, entity resolution, hash-chained provenance, and IPFS distribution. Designed to complement retrieval systems like MemPalace, Mem0, and Zep.

## What spiritwriter-core adds to your memory system

| Capability | What it does |
|---|---|
| **Content addressing** | SHA-256 hashes — tamper detection, free dedup, immutable history |
| **AES-256-GCM encryption** | Encrypt memory at rest, share via entitlement tokens |
| **NaCl sealed boxes** | Zero-knowledge storage — operator can't read the data |
| **Entity resolution (CMC-Lite)** | Deduplicate people/entities across conversations with tiered confidence |
| **Hash-chained provenance** | Tamper-evident audit trail of every memory read/write |
| **Entitlements** | Scoped access tokens with capability checks and budget tracking |
| **IPFS distribution** | Share memory across nodes via private swarm |
| **Decay lifecycle** | Automatic TTL management (permanent/stable/active/session) |

## With MemPalace

If MemPalace is installed, spiritwriter auto-discovers it and enables:

### Shard-backed drawers (content addressing for every memory)

```python
from spiritwriter.integrations.mempalace import ShardBackend

# Use as MemPalace storage backend — every drawer gets a content address
backend = ShardBackend("~/.mempalace/shards")
backend.add(
    documents=["Max had his first swim meet today. He was nervous but did great."],
    ids=["drawer_001"],
    metadatas=[{"wing": "wing_alice", "room": "swimming"}],
)

# Check drawer provenance
shard_id = backend.get_shard_id("drawer_001")
print(shard_id)  # SHA-256 content address

# Get revision history
history = backend.get_drawer_history("drawer_001")
```

### Encrypted palaces (zero-knowledge memory)

```python
from spiritwriter.trace.crypto import generate_job_key

key = generate_job_key()
backend = ShardBackend(
    "~/.mempalace/shards",
    encryption_key=key,  # all drawers encrypted at rest
)
```

### Cross-conversation entity resolution

```python
from spiritwriter.integrations.mempalace import EntityBridge

bridge = EntityBridge()

# Session 1: "my son Max had a swim meet"
r1 = bridge.resolve_person("Max", context={"relationship": "son", "wing": "wing_alice"})

# Session 47: "Max's chess tournament is Saturday"
r2 = bridge.resolve_person("Max", context={"relationship": "son", "wing": "wing_alice"})

# Same canonical entity — linked across conversations
assert r1.canonical_id == r2.canonical_id  # True (T1 exact match)
```

### Semantic search over shards

```python
from spiritwriter.integrations import get_provider

mp = get_provider("mempalace")
if mp and mp.is_available():
    results = mp.search(SearchQuery(text="Max's swimming"))
    for r in results:
        print(r.text, r.score)
```

## Without MemPalace (standalone)

spiritwriter-core works independently as a memory shard system:

```python
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.trace.store import ShardStore

store = ShardStore("~/.myapp/shards")
shard = MemoryShard(
    atoms=[ShardAtom(text="Project uses FastAPI", kind=AtomKind.FACT,
                     entity="myproject", key="framework", value="FastAPI")],
    scope="project:myapp",
    origin="dev-agent",
)
ref = store.put(shard)
```

## Provider Protocol

Any memory system can integrate with spiritwriter by implementing the provider protocol:

```python
from spiritwriter.integrations.base import RetrievalProvider, SearchQuery, SearchResult

class MyMemoryProvider(RetrievalProvider):
    def info(self):
        return ProviderInfo(name="mymemory", version="1.0", ...)

    def search(self, query: SearchQuery) -> list[SearchResult]:
        # Your retrieval logic here
        ...
```

Register it:
```python
from spiritwriter.integrations import register_provider
register_provider("mymemory", MyMemoryProvider())
```

## License

Apache 2.0
