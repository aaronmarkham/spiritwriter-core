# Integration Guide

How real-world applications use spiritwriter-core's memory shards, encryption, and entity resolution. These examples come from production systems.

## Frio — Zero-Knowledge Jail Roster Monitoring

[Frio](https://github.com/aaronmarkham/frio) monitors jail rosters across ~55 sources to alert families when a person is booked. It uses spiritwriter-core for encrypted search shards, fuzzy name matching, and result delivery — all without the operator seeing search terms.

### Architecture

```
Web Form → shard_engine.py → FrioStore (ShardStore wrapper)
                                 ↓
                          Daemon check cycle
                                 ↓
                          Match → seal result → notify
```

### Creating Search Shards

Each search target becomes a MemoryShard with structured atoms:

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass

def shard_from_web_intake(last_name, first_names, name, region,
                          threshold=0.85, notify_channel="signal",
                          signal_group_link=None, language="en"):
    """Create a search shard from web intake form."""
    # Generate name variants for fuzzy matching
    variants = generate_name_variants(first_names)

    atoms = [
        # Target identity
        ShardAtom(text=f"Target: {last_name}", kind=AtomKind.ENTITY,
                  entity="target", key="last_name", value=last_name),
        ShardAtom(text=f"First names: {','.join(first_names)}",
                  kind=AtomKind.ENTITY, entity="target",
                  key="first_names", value=",".join(first_names)),
        ShardAtom(text=f"Variants: {','.join(variants)}",
                  kind=AtomKind.ENTITY, entity="target",
                  key="name_variants", value=",".join(variants)),
        # Display name
        ShardAtom(text=f"Display: {name}", kind=AtomKind.ENTITY,
                  entity="display", key="full_name", value=name),
        # Search config
        ShardAtom(text=f"Region: {region}", kind=AtomKind.ENTITY,
                  entity="search", key="region", value=region),
        ShardAtom(text=f"Threshold: {threshold}", kind=AtomKind.FACT,
                  entity="search", key="threshold", value=str(threshold)),
    ]

    # Optional notification atoms
    if signal_group_link:
        atoms.append(ShardAtom(
            text=f"Signal: {signal_group_link}", kind=AtomKind.ENTITY,
            entity="notify", key="signal_group_link",
            value=signal_group_link,
        ))

    return MemoryShard(
        atoms=atoms,
        scope="frio:search",
        origin="frio-web",
        decay_class=DecayClass.ACTIVE,  # 14-day TTL, extended on each check
        tags=[f"frio:{last_name.lower()}"],
        meta={
            "frio_status": "hot",
            "intake_channel": "web",
            "lang": language,
            "region": region,
            "notify_channel": notify_channel,
        },
    )
```

### Sealed Search Shards (Zero-Knowledge)

For maximum privacy, search params are encrypted so the operator can't see them:

```python
from spiritwriter.fabric.sealed import (
    generate_owner_keypair, seal_for_owner, unseal_as_owner,
)
import json, base64

def sealed_shard_from_web_intake(last_name, first_names, name, region,
                                  service_pubkey, **kwargs):
    """Create a sealed shard — operator cannot see search params."""
    # Generate requestor keypair
    keypair = generate_owner_keypair()

    # Encrypt sensitive data to service public key
    sensitive = {
        "last_name": last_name,
        "first_names": first_names,
        "name": name,
        "threshold": kwargs.get("threshold", 0.85),
        "name_variants": generate_name_variants(first_names),
    }
    encrypted = seal_for_owner(
        json.dumps(sensitive).encode(), service_pubkey
    )
    encrypted_b64 = base64.b64encode(encrypted).decode()

    shard = MemoryShard(
        atoms=[
            ShardAtom(text="encrypted", kind=AtomKind.ENTITY,
                      entity="target", key="encrypted_params",
                      value=encrypted_b64),
        ],
        scope="frio:search:sealed",
        origin="frio-web",
        decay_class=DecayClass.ACTIVE,
        tags=["frio:sealed"],
        meta={
            "frio_status": "hot",
            "encrypted": True,
            "requestor_pubkey": keypair.public_key_b64,
            "intake_channel": "web",
            "region": region,
        },
    )

    # Return shard + capability key (private key for the requestor)
    return shard, keypair.private_key_b64
```

### Shard Lifecycle (Hot → Warm → Cold → Matched/Expired)

Frio extends ShardStore with lifecycle management:

```python
from spiritwriter.fabric.store import ShardStore

class FrioStore:
    """Wraps ShardStore with frio-specific lifecycle management."""

    def __init__(self, store_path):
        self._store = ShardStore(store_path)

    def get_active_shards(self):
        """Get all shards with frio_status in (hot, warm, cold)."""
        active = []
        for shard in self._store.iter_all():
            status = shard.meta.get("frio_status")
            if status in ("hot", "warm", "cold"):
                if not shard.meta.get("canary"):
                    active.append(shard)
        return active

    def update_checked(self, shard):
        """Bump last_checked and check_count after a check cycle."""
        from spiritwriter.fabric.shard import _now_iso
        # Shards are immutable — we write updated metadata
        shard.last_checked = _now_iso()
        shard.check_count += 1
        # Re-persist (same shard_id since atoms/scope/origin unchanged)
        path = self._store._shard_path(shard.shard_id)
        path.write_text(shard.to_json(), encoding="utf-8")

    def set_status(self, shard, status):
        """Transition shard status: hot→warm→cold→matched→expired."""
        shard.meta["frio_status"] = status
        path = self._store._shard_path(shard.shard_id)
        path.write_text(shard.to_json(), encoding="utf-8")
```

### Name Matching with Canonicalization

Frio uses spiritwriter's normalization when available:

```python
from spiritwriter.fabric.canonicalize import normalize_name, fuzzy_score

def match_name(candidate_name, target_last, target_firsts, threshold=0.85):
    """Check if a roster name matches the search target."""
    # Normalize both sides
    cand_norm = normalize_name(candidate_name)

    # Check last name
    if fuzzy_score(cand_norm.split()[0], target_last) < 0.90:
        return False

    # Check first name against all variants
    cand_first = " ".join(cand_norm.split()[1:])
    for first in target_firsts:
        if fuzzy_score(cand_first, first) >= threshold:
            return True

    return False
```

---

## Perseus-News — Dual-Perspective News Analysis

[Perseus-News](https://github.com/aaronmarkham/perseus-news) generates enforcement-focused news sites. It stores articles as shards with dual-perspective analysis and shares them across consumers via a common `sw:article` scope.

### Dual-Scope Architecture

Each article produces two shards:

1. **`perseus:article:{region}`** — Internal scope for dedup and site generation
2. **`sw:article`** — Cross-consumer scope shared with frio, texascrime, etc.

```python
import hashlib

def article_to_shard(article, store):
    """Store article with perseus-internal scope."""
    url_hash = hashlib.sha256(article.url.encode()).hexdigest()[:16]
    entity = f"article:{url_hash}"

    atoms = [
        # Source facts
        ShardAtom(text=article.title, kind=AtomKind.FACT,
                  entity=entity, key="source_title", value=article.title),
        ShardAtom(text=article.summary, kind=AtomKind.FACT,
                  entity=entity, key="source_summary", value=article.summary),
        ShardAtom(text=article.url, kind=AtomKind.FACT,
                  entity=entity, key="source_url", value=article.url),
        ShardAtom(text=article.region, kind=AtomKind.FACT,
                  entity=entity, key="region", value=article.region),
        # Cover perspective (enforcement)
        ShardAtom(text=f"Bias: {article.bias_label}", kind=AtomKind.DECISION,
                  entity=entity, key="bias_score",
                  value=str(article.bias_score)),
        ShardAtom(text=article.variant_title, kind=AtomKind.DECISION,
                  entity=entity, key="variant_title",
                  value=article.variant_title),
        # Frio perspective (rights)
        ShardAtom(text=f"Rights relevance: {article.rights_relevance}",
                  kind=AtomKind.DECISION, entity=entity,
                  key="rights_relevance",
                  value=str(article.rights_relevance)),
        ShardAtom(text=article.frio_summary, kind=AtomKind.DECISION,
                  entity=entity, key="frio_summary",
                  value=article.frio_summary),
    ]

    shard = MemoryShard(
        atoms=atoms,
        scope=f"perseus:article:{article.region}",
        origin="perseus-analyzer",
        decay_class=DecayClass.STABLE,
        tags=[f"region:{article.region}"],
    )
    store.put(shard)
    return shard
```

### Cross-Consumer Shards with Lineage

The `sw:article` shard uses a different entity key format and tracks revision lineage:

```python
def article_to_sw_shard(article, store, lineage_index=None):
    """Store article in shared sw:article scope with lineage."""
    url_hash = hashlib.sha256(article.url.encode()).hexdigest()
    entity = f"article:{url_hash}"

    # Check for previous version
    parent_id = None
    if lineage_index and entity in lineage_index:
        parent_id = lineage_index[entity][0]  # (shard_id, created_at)

    atoms = [
        # Frio convention: "title" not "source_title"
        ShardAtom(text=article.title, kind=AtomKind.FACT,
                  entity=entity, key="title", value=article.title),
        ShardAtom(text=article.summary, kind=AtomKind.FACT,
                  entity=entity, key="summary", value=article.summary),
        ShardAtom(text=article.url, kind=AtomKind.FACT,
                  entity=entity, key="source_url", value=article.url),
        # Analysis
        ShardAtom(text=str(article.rights_relevance),
                  kind=AtomKind.DECISION, entity=entity,
                  key="rights_relevance",
                  value=str(article.rights_relevance)),
    ]

    # Build tags for faceted filtering
    tags = [f"region:{article.region}"]
    for fac in article.facility_mentions:
        tags.append(f"facility:{fac}")

    shard = MemoryShard(
        atoms=atoms,
        scope="sw:article",
        origin="perseus-analyzer",
        decay_class=DecayClass.STABLE,
        parent_shard_id=parent_id,  # revision chain
        tags=tags,
        meta={"entity_key": entity},
    )
    store.put(shard)
    return shard
```

### Deduplication via URL Hash

```python
def known_url_hashes(store):
    """Get set of already-processed URL hashes for dedup."""
    known = set()
    for shard in store.iter_all():
        entity_key = shard.meta.get("entity_key", "")
        if entity_key.startswith("article:"):
            known.add(entity_key.split(":", 1)[1])
    return known
```

---

## Claude Studio Producer — Multi-Tenant Agent Memory

[Claude Studio Producer](https://github.com/aaronmarkham/claude-studio-producer) is a multi-agent video production system. While it doesn't yet import spiritwriter-core directly, its memory architecture is designed to align with the shard model.

### Memory Hierarchy → Shard Scope Mapping

The 4-level namespace maps directly to shard scopes:

| Level | Namespace | Shard Scope |
|-------|-----------|-------------|
| PLATFORM | `/platform/learnings/global` | `platform:learnings` |
| ORG | `/org/{orgId}/learnings/...` | `org:{orgId}:learnings` |
| USER | `/org/{orgId}/actor/{actorId}/...` | `org:{orgId}:actor:{actorId}` |
| SESSION | `/org/{orgId}/actor/{actorId}/sessions/{sessionId}` | `session:{sessionId}` |

### Learning Promotion Pattern

Learnings flow upward through the hierarchy as they're validated:

```python
# SESSION: Critic discovers a pattern during one production run
session_shard = MemoryShard(
    atoms=[
        ShardAtom(
            text="Luma AI produces better results with 4-second scenes",
            kind=AtomKind.DECISION,
            entity="luma",
            key="optimal_duration",
            value="4s",
            confidence=0.7,
        ),
    ],
    scope="session:run-001",
    origin="critic-agent",
    decay_class=DecayClass.SESSION,
)

# After 3+ validations, promote to USER level
user_shard = MemoryShard(
    atoms=session_shard.atoms,
    scope="org:acme:actor:aaron:learnings",
    origin="critic-agent",
    decay_class=DecayClass.STABLE,
    parent_shard_id=session_shard.shard_id,
)

# Eventually promote to PLATFORM (universal truth)
platform_shard = MemoryShard(
    atoms=session_shard.atoms,
    scope="platform:learnings:provider:luma",
    origin="critic-agent",
    decay_class=DecayClass.PERMANENT,
    parent_shard_id=user_shard.shard_id,
)
```

### Knowledge Base Integration

Claude Studio Producer's document ingestion produces atoms compatible with spiritwriter shards:

```python
# Ingest a PDF and create a knowledge shard
atoms = []
for doc_atom in document_graph.atoms:
    atoms.append(ShardAtom(
        text=doc_atom.text,
        kind=AtomKind.FACT if doc_atom.type == "paragraph"
             else AtomKind.CONTEXT,
        entity=doc_atom.source_id,
        key=doc_atom.type.value,  # "title", "abstract", "figure", etc.
    ))

kb_shard = MemoryShard(
    atoms=atoms,
    scope="job:content",
    origin="document-ingestor",
    decay_class=DecayClass.STABLE,
    tags=["knowledge-base", doc_atom.source_id],
)
```

---

## Third-Party Memory Systems

spiritwriter-core is designed to complement — not compete with — existing memory retrieval systems. It provides the **trust layer** (content addressing, encryption, entity resolution, provenance, access control, distribution) while retrieval systems provide **semantic search**. All integrations use the pluggable provider protocol in `spiritwriter.integrations`.

### License Compatibility

| System | License | Compatible? | Integration Status |
|--------|---------|-------------|-------------------|
| spiritwriter-core | Apache-2.0 | -- | -- |
| MemPalace | MIT | Yes | Available (`spiritwriter.integrations.mempalace`) |
| Mem0 | Apache-2.0 | Yes | Planned |
| Zep | Apache-2.0 | Yes | Planned |

Install order doesn't matter. If both packages are installed, integration activates automatically via auto-discovery.

### Provider Protocol

Any memory system can integrate by implementing the provider protocol:

```python
from spiritwriter.integrations.base import (
    RetrievalProvider, StorageProvider, EntityProvider,
    ProviderInfo, ProviderCapability, SearchQuery, SearchResult,
)

class MyMemoryProvider(RetrievalProvider):
    def info(self) -> ProviderInfo:
        return ProviderInfo(
            name="mymemory",
            version="1.0.0",
            description="My memory system",
            capabilities=[ProviderCapability.SEMANTIC_SEARCH],
        )

    def search(self, query: SearchQuery) -> list[SearchResult]:
        # Your retrieval logic here
        ...

# Register it
from spiritwriter.integrations import register_provider
register_provider("mymemory", MyMemoryProvider())
```

Consumers discover providers without hard dependencies:

```python
from spiritwriter.integrations import available_providers, get_provider

# See what's installed
providers = available_providers()  # {"mempalace": ..., "mem0": ...}

# Use a specific provider
mp = get_provider("mempalace")
if mp and mp.is_available():
    results = mp.search(SearchQuery(text="what database for sessions?"))
```

---

### MemPalace Integration

[MemPalace](https://github.com/MemPalace/mempalace) is a local-first AI memory system with 96.6% recall on LongMemEval. It stores verbatim conversation text and retrieves via hybrid BM25 + vector search. No API keys, no cloud.

**What each side contributes:**

| MemPalace | spiritwriter-core |
|-----------|-------------------|
| Semantic search (ChromaDB) | Content-addressed storage (SHA-256) |
| BM25 keyword reranking | AES-256-GCM / NaCl encryption |
| Palace navigation (wings/rooms/drawers) | Entitlements & access control |
| AAAK compression dialect | Entity resolution (CMC-Lite) |
| L0-L3 memory wake-up stack | Hash-chained provenance |
| Knowledge graph (SQLite) | IPFS distribution |
| Diary ingest / hooks | Decay lifecycle management |

**Install:**

```bash
pip install spiritwriter-core[mempalace]
# or just install both:
pip install spiritwriter-core mempalace
```

#### ShardBackend: Content-Addressed Drawers

Every MemPalace drawer becomes a content-addressed shard with tamper detection, revision history, and optional encryption:

```python
from spiritwriter.integrations.mempalace import ShardBackend

# Drop-in storage backend for MemPalace
backend = ShardBackend("~/.mempalace/shards")

# Store drawers — they become content-addressed shards
backend.add(
    documents=[
        "Max had his first swim meet today. He was nervous but did great.",
        "Riley got into the advanced math program. She wants to be an engineer.",
    ],
    ids=["drawer_001", "drawer_002"],
    metadatas=[
        {"wing": "family", "room": "swimming"},
        {"wing": "family", "room": "school"},
    ],
)

# Every drawer now has a SHA-256 content address
shard_id = backend.get_shard_id("drawer_001")
print(shard_id)  # "82f83e54cbe90a6f..."

# Upsert creates a revision chain (parent_shard_id links)
backend.upsert(
    documents=["Max won his second swim meet! Backstroke PB by 2 seconds."],
    ids=["drawer_001"],
    metadatas=[{"wing": "family", "room": "swimming"}],
)

# Walk the full revision history
history = backend.get_drawer_history("drawer_001")
print(len(history))       # 2 (latest + original)
print(history[0].atoms[0].text)  # "Max won his second swim meet!..."
print(history[1].atoms[0].text)  # "Max had his first swim meet..."
```

#### Encrypted Palaces

Add encryption with zero code changes to MemPalace:

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()  # 32-byte AES-256 key

# All drawers encrypted at rest — same API
backend = ShardBackend(
    "~/.mempalace/shards",
    encryption_key=key,
)
backend.add(documents=["sensitive content"], ids=["drawer_secret"],
            metadatas=[{"wing": "private"}])
# On disk: AES-256-GCM ciphertext. In memory: plaintext only during access.
```

For zero-knowledge storage (operator can't decrypt):

```python
from spiritwriter.fabric.sealed import generate_owner_keypair, seal_shard

keypair = generate_owner_keypair()
# Give public key to the service, keep private key as capability token
# Service can store but never read the drawer content
```

#### EntityBridge: Cross-Conversation Entity Resolution

MemPalace's `entity_registry.py` does regex-based name detection. CMC-Lite adds tiered confidence resolution so "Max" in session 1 and "Max" in session 47 are linked as the same person:

```python
from spiritwriter.integrations.mempalace import EntityBridge

bridge = EntityBridge("~/.mempalace/entities.db")

# Session 1: "my son Max had a swim meet"
r1 = bridge.resolve_person("Max", context={
    "wing": "family", "room": "swimming", "relationship": "son",
}, source_id="session_01")
print(r1.tier)  # NO_MATCH (first time — creates new entity)

# Session 47: "Max's chess tournament is Saturday"
r2 = bridge.resolve_person("Max", context={
    "wing": "family", "room": "chess", "relationship": "son",
}, source_id="session_47")
print(r2.tier)          # T1_EXACT (same ESS — name + relationship)
print(r2.confidence)    # 0.95
print(r2.canonical_id)  # stable UUID, same across all sessions

# Fuzzy matching works too
r3 = bridge.resolve_person("Maxwell", context={
    "wing": "family", "relationship": "son",
}, source_id="session_90")
print(r3.tier)  # T2_STRONG or T3_FUZZY depending on score
```

#### Provenance Logging

Track every memory access with a tamper-evident audit trail:

```python
from spiritwriter.fabric.emitter import TraceEmitter

tracer = TraceEmitter(
    run_id="session-42", agent_id="atlas",
    out_path="~/.mempalace/trace.jsonl",
)

backend = ShardBackend(
    "~/.mempalace/shards",
    tracer=tracer,  # every add/get logged to hash-chained JSONL
)
```

#### Overhead

Benchmarked on the provider harness (`bench_providers.py`):

| Operation | Overhead | Notes |
|-----------|----------|-------|
| Content addressing per drawer | ~9ms | SHA-256 + file write + index |
| Encryption per drawer | +0.2ms | AES-256-GCM on top |
| Drawer retrieval | 0.6ms avg | Includes hash verification |
| Lineage traversal (10 revisions) | 1.8ms | Full chain walk |
| Entity resolution (known) | 0.022ms | 45K ops/sec for T1 exact |
| Fuzzy entity resolution | 0.031ms | 32K ops/sec |

---

### Mem0 Integration (Planned)

[Mem0](https://github.com/mem0ai/mem0) (Apache-2.0) is a self-improving memory layer for LLM applications. It extracts facts from conversations using an LLM, stores them in a vector database, and provides a simple add/search API.

**What spiritwriter would add to Mem0:**

- **Content addressing** for extracted facts (tamper detection, dedup)
- **Encryption at rest** for sensitive memory (AES-GCM or sealed boxes)
- **Entity resolution** across Mem0's extracted facts (CMC-Lite tiered matching vs Mem0's LLM-based extraction)
- **Provenance** for memory operations (who accessed what, when)
- **Distribution** via IPFS for multi-node Mem0 deployments

**Planned integration surface:**

```python
# Future: spiritwriter.integrations.mem0
from spiritwriter.integrations import get_provider

mem0 = get_provider("mem0")
if mem0 and mem0.is_available():
    # Mem0's add/search with spiritwriter's trust layer
    results = mem0.search(SearchQuery(text="user's database preferences"))
```

The provider protocol is ready — the Mem0 adapter needs to wrap Mem0's `Memory` class behind `RetrievalProvider` + `StorageProvider`.

---

### Zep Integration (Planned)

[Zep](https://github.com/getzep/zep) (Apache-2.0) is a long-term memory store for AI assistants. It features a temporal knowledge graph, entity extraction, and conversation classification.

**What spiritwriter would add to Zep:**

- **Content addressing** for Zep's memory artifacts
- **Sealed-box encryption** for multi-tenant deployments (Zep Cloud users)
- **CMC-Lite entity resolution** complementing Zep's built-in entity extraction
- **Hash-chained provenance** for compliance and audit trails
- **Offline entity resolution** (Zep's extraction requires an LLM; CMC-Lite works offline)

**Planned integration surface:**

```python
# Future: spiritwriter.integrations.zep
from spiritwriter.integrations import get_provider

zep = get_provider("zep")
if zep and zep.is_available():
    results = zep.search(SearchQuery(text="project deadlines"))
    # Zep provides: temporal knowledge graph + classification
    # spiritwriter provides: content addressing + encryption + provenance
```

The provider protocol supports Zep's temporal queries via `SearchQuery.after` / `SearchQuery.before` filters and `EntityProvider` for knowledge graph access.

---

## Common Integration Patterns

### Lazy Imports (Optional Dependency)

When spiritwriter-core is optional, use lazy imports:

```python
_shard_module = None

def _ensure_imports():
    global _shard_module
    if _shard_module is None:
        try:
            from spiritwriter.fabric.shard import (
                MemoryShard, ShardAtom, AtomKind, DecayClass,
            )
            _shard_module = type("M", (), {
                "MemoryShard": MemoryShard,
                "ShardAtom": ShardAtom,
                "AtomKind": AtomKind,
                "DecayClass": DecayClass,
            })
        except ImportError:
            raise ImportError(
                "spiritwriter-core is required for shard operations. "
                "Install with: pip install spiritwriter-core"
            )
    return _shard_module
```

### Shard Store Initialization

```python
import os
from pathlib import Path
from spiritwriter.fabric.store import ShardStore

def init_store(app_name="myapp"):
    """Initialize shard store at the standard location."""
    store_path = os.environ.get(
        f"{app_name.upper()}_SHARD_STORE",
        str(Path.home() / f".{app_name}" / "shards"),
    )
    return ShardStore(store_path)
```

### Cross-Consumer Scope Conventions

When multiple applications share shards, use the `sw:` prefix:

```python
# Application-specific scope (internal)
scope = "frio:search"           # only frio reads these
scope = "perseus:article:texas" # only perseus reads these

# Shared scope (cross-consumer)
scope = "sw:article"            # frio, perseus, texascrime all read these
```

### Atom Key Conventions

Agree on atom key names across consumers:

```python
# Shared convention for sw:article shards
#   key="title"       (not "source_title")
#   key="summary"     (not "source_summary")
#   key="source_url"  (universal)
```
