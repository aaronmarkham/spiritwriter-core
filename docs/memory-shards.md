# Memory Shards

A **MemoryShard** is the fundamental unit of distributable agent memory in spiritwriter-core. Shards are content-addressed, immutable bundles of structured knowledge atoms with provenance, access control, and lifecycle metadata.

## Design Principles

1. **Content-addressed** — SHA-256 hash of (atoms + scope + origin). Same content = same ID. Deduplication is free.
2. **Immutable** — Editing atoms creates a new shard with a new ID. The original is preserved.
3. **Local-first** — File-based storage, no external databases. DHT-ready for distribution.
4. **Pull-based hydration** — Agents receive lightweight refs (pointers), not full content. They resolve refs on demand.
5. **Scoped access** — Every shard has an entitlement boundary (scope) for access control.

## ShardAtom

Atoms are the smallest unit of retrievable knowledge. Each atom has a semantic type and optional structured fields for exact lookup:

```python
from spiritwriter.fabric.shard import ShardAtom, AtomKind

# Structured atom with entity/key/value (exact lookup)
fact = ShardAtom(
    text="The API rate limit is 100 requests per minute",
    kind=AtomKind.FACT,
    entity="api-gateway",
    key="rate_limit",
    value="100/min",
    confidence=0.95,
)

# Decision with rationale
decision = ShardAtom(
    text="Use PostgreSQL over Redis for session storage because we need ACID guarantees",
    kind=AtomKind.DECISION,
    entity="myproject",
    key="session_backend",
    value="postgresql",
)

# Convention (always/never rule)
convention = ShardAtom(
    text="Never deploy on Fridays — incident risk is too high",
    kind=AtomKind.CONVENTION,
    entity="team",
    key="deploy_policy",
    value="no-friday-deploys",
)

# Instruction (actionable step)
instruction = ShardAtom(
    text="Run 'alembic upgrade head' before starting the app",
    kind=AtomKind.INSTRUCTION,
    key="startup_step_1",
)

# Entity reference
entity_atom = ShardAtom(
    text="Martinez, Carlos — booking #2024-1234",
    kind=AtomKind.ENTITY,
    entity="target",
    key="last_name",
    value="martinez",
)
```

### AtomKind Reference

| Kind | Use Case | Example |
|------|----------|---------|
| `FACT` | Structured entity/key/value triples | `api.rate_limit = 100/min` |
| `DECISION` | Choices with rationale | "Chose PostgreSQL because of ACID" |
| `CONVENTION` | Always/never rules | "Never deploy on Fridays" |
| `PREFERENCE` | User preferences | "Prefers dark mode" |
| `ENTITY` | Named entity information | Person, facility, organization |
| `CONTEXT` | Freeform contextual knowledge | Background info, notes |
| `INSTRUCTION` | How-to steps, workflows | "Run migrations first" |
| `CHECKPOINT` | Temporary state snapshots | Pre-flight saves |

### Content Addressing

Each atom has its own content hash, computed from its semantic fields:

```python
print(fact.content_hash)  # SHA-256 of (text, kind, entity, key, value)
```

## MemoryShard

A shard bundles atoms with metadata:

```python
from spiritwriter.fabric.shard import MemoryShard, DecayClass

shard = MemoryShard(
    atoms=[fact, decision, convention],
    scope="project:myproject",          # entitlement boundary
    origin="architect-agent",           # creating agent
    decay_class=DecayClass.STABLE,      # 90-day TTL
    tags=["architecture", "myproject"], # human-readable labels
    meta={"version": "2.1"},            # application metadata
)
```

### Shard ID

The shard_id is a deterministic SHA-256 hash of `(atoms + scope + origin)`:

```python
print(shard.shard_id)
# e.g. "a1b2c3d4e5f6..."

# Same content always produces the same ID
shard2 = MemoryShard(
    atoms=[fact, decision, convention],
    scope="project:myproject",
    origin="architect-agent",
)
assert shard.shard_id == shard2.shard_id
```

### DecayClass (TTL)

Shards have a lifecycle. The decay class controls how long they live:

| Class | TTL | Use Case |
|-------|-----|----------|
| `PERMANENT` | Never expires | Identities, architecture decisions, core conventions |
| `STABLE` | 90 days | Project details, relationships, learned patterns |
| `ACTIVE` | 14 days | Current tasks, sprint goals, active monitoring |
| `SESSION` | 24 hours | Debugging context, temporary state |
| `CHECKPOINT` | 4 hours | Pre-flight state saves |

Shards that are actively polled (via `last_checked`) have their TTL anchored to the last check time, not creation time. This keeps active monitoring shards alive.

### Parent Shard ID (Lineage)

When a shard supersedes another, link them via `parent_shard_id`:

```python
# Original analysis
v1 = MemoryShard(
    atoms=[ShardAtom(text="Initial analysis", kind=AtomKind.CONTEXT)],
    scope="sw:article",
    origin="analyzer",
)
store.put(v1)

# Updated analysis — links to original
v2 = MemoryShard(
    atoms=[ShardAtom(text="Revised analysis with corrections", kind=AtomKind.CONTEXT)],
    scope="sw:article",
    origin="analyzer",
    parent_shard_id=v1.shard_id,  # revision chain
)
store.put(v2)
```

### Hydration

Shards render as injectable agent context (XML-tagged text):

```python
context = shard.hydrate_context()
print(context)
```

Output:
```xml
<shard scope="project:myproject" label="architecture">
- [fact] api-gateway.rate_limit = 100/min
- [decision] myproject.session_backend = postgresql
- [convention] team.deploy_policy = no-friday-deploys
</shard>
```

Instructions render differently:
```xml
- **startup_step_1**: Run 'alembic upgrade head' before starting the app
```

### Token Estimation

Estimate the token cost of hydrating a shard:

```python
print(shard.token_estimate)  # rough char-to-token ratio (÷4)
```

### ShardRef (Lightweight Pointer)

Agents receive refs, not full shards. A ref is a tiny pointer:

```python
ref = shard.ref
print(ref.shard_id)   # content address
print(ref.scope)      # entitlement boundary
print(ref.label)      # first tag (human hint)
print(ref.origin)     # creating agent
```

### Serialization

Shards serialize to/from JSON and dicts:

```python
# To JSON (canonical, for storage)
json_str = shard.to_json()

# From JSON
restored = MemoryShard.from_json(json_str)
assert restored.shard_id == shard.shard_id

# To/from dict
d = shard.to_dict()
restored2 = MemoryShard.from_dict(d)
```

Content address integrity is verified on deserialization — if the stored `shard_id` doesn't match the recomputed hash, a `ValueError` is raised.

### Atom Lookup

Find atoms by key:

```python
atom = shard.get_atom("rate_limit")
print(atom.value)  # "100/min"
```

## Scope Conventions

Scopes are free-form strings, but these conventions are used across the ecosystem:

| Pattern | Used By | Example |
|---------|---------|---------|
| `project:{name}` | General project context | `project:csp` |
| `user:{identity}` | User-specific memory | `user:aaron` |
| `frio:search` | Plaintext search shards | Active monitoring |
| `frio:search:sealed` | Encrypted search shards | Zero-knowledge monitoring |
| `frio:profile` | Candidate profiles | Extension submissions |
| `sw:article` | Cross-consumer articles | Shared between frio & perseus |
| `perseus:article:{region}` | Regional articles | Internal dedup |
| `studio:{prefix}:content` | Studio job content | Sub-agent knowledge |
| `studio:{prefix}:task` | Studio job task spec | Sub-agent instructions |
