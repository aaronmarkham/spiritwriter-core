# Memory Shards

A **MemoryShard** is the atomic unit of distributable agent memory. Think of it as a Git commit for knowledge: an immutable, content-addressed bundle of structured atoms with provenance, scope, and a lifecycle. Agents pass *refs* across boundaries (cheap pointers); content materializes only at the consumption site via hydration.

If you're new to the model, read this in order: atoms first (what knowledge looks like), then shards (the bundle), then refs (the wire format).

## Why this shape?

The shard format makes five hard problems disappear:

- **Deduplication.** Same content → same SHA-256 → same `shard_id`. Two agents producing identical knowledge produce a single object.
- **Integrity.** Content addresses are tamper-evident by construction. `from_json()` recomputes the hash and raises `ValueError` if it disagrees with the stored ID.
- **Distribution.** Bit-identical content across nodes means any DHT or content-addressed network (IPFS, custom swarm) can store and route shards without coordination.
- **Provenance.** `origin`, `parent_shard_id`, and `trace_ref` are baked into the shape — every shard knows who made it, what it supersedes, and what trace produced it.
- **Lifecycle.** `decay_class` declares how long the shard should live, so storage layers can prune without parsing content.

Everything else (storage, encryption, network distribution, hydration into agent context) builds on this shape.

## ShardAtom — the unit of knowledge

Atoms are the smallest retrievable thing. Each atom has a `kind` (semantic type) and an optional `entity`/`key`/`value` triple for structured lookup. The `text` field always carries the human-readable form for embedding or full-text search.

```python
from spiritwriter.fabric.shard import ShardAtom, AtomKind

# Structured fact — entity/key/value enables exact lookup
fact = ShardAtom(
    text="The API rate limit is 100 requests per minute",
    kind=AtomKind.FACT,
    entity="api-gateway",
    key="rate_limit",
    value="100/min",
    confidence=0.95,
)

# Decision — captures the choice and why
decision = ShardAtom(
    text="Use PostgreSQL over Redis for session storage because we need ACID guarantees",
    kind=AtomKind.DECISION,
    entity="myproject",
    key="session_backend",
    value="postgresql",
)

# Convention — always/never rule
convention = ShardAtom(
    text="Never deploy on Fridays — incident risk is too high",
    kind=AtomKind.CONVENTION,
    entity="team",
    key="deploy_policy",
    value="no-friday-deploys",
)

# Instruction — actionable step (renders differently in hydration)
instruction = ShardAtom(
    text="Run 'alembic upgrade head' before starting the app",
    kind=AtomKind.INSTRUCTION,
    key="startup_step_1",
)
```

### Pick the right AtomKind

The kind isn't decorative. It changes how hydration renders the atom and signals intent to consuming agents.

| Kind | Use for | Renders as |
|------|---------|------------|
| `FACT` | Structured entity/key/value triples | `[fact] api-gateway.rate_limit = 100/min` |
| `DECISION` | Choices with rationale baked in | `[decision] myproject.session_backend = postgresql` |
| `CONVENTION` | Always/never rules the agent should respect | `[convention] team.deploy_policy = no-friday-deploys` |
| `PREFERENCE` | User preferences (less binding than convention) | `[preference] user.theme = dark` |
| `ENTITY` | Named entity records (people, orgs, places) | `[entity] target.last_name: Martinez, Carlos` |
| `CONTEXT` | Freeform background — when nothing else fits | `[context] ...freeform text...` |
| `INSTRUCTION` | How-to steps; rendered as actionable bullets | `- **startup_step_1**: Run 'alembic upgrade head'...` |
| `CHECKPOINT` | Pre-flight state snapshots (typically 4-hour TTL) | `[checkpoint] ...state...` |

Rule of thumb: if a future agent needs to *act* on this knowledge, use `CONVENTION` or `INSTRUCTION`. If it needs to *recall* a value, use `FACT`. If the choice has a story behind it, use `DECISION`.

### Atom content addressing

Each atom has its own hash, computed from its semantic fields only (text, kind, entity, key, value). `confidence` and `source_ref` are not part of the address — two agents can produce the same atom with different confidence levels and still dedup.

```python
print(fact.content_hash)  # SHA-256 hex digest
```

## MemoryShard — the bundle

A shard wraps a list of atoms with the metadata storage and distribution need:

```python
from spiritwriter.fabric.shard import MemoryShard, DecayClass

shard = MemoryShard(
    atoms=[fact, decision, convention],
    scope="project:myproject",          # entitlement boundary
    origin="architect-agent",           # creating agent id
    decay_class=DecayClass.STABLE,      # 90-day TTL
    tags=["architecture", "myproject"], # human-readable labels; first becomes ref.label
    meta={"version": "2.1"},            # application metadata (not part of shard_id)
)
```

The first tag is special: it becomes `ref.label` and the `label` attribute on the rendered `<shard>` XML wrapper. Order your tags accordingly.

### What's in the shard_id

The shard ID is `sha256(canonical_json(atoms + scope + origin))`. That's it.

```python
shard2 = MemoryShard(
    atoms=[fact, decision, convention],
    scope="project:myproject",
    origin="architect-agent",
)
assert shard.shard_id == shard2.shard_id    # different created_at, same ID
```

What's *not* in the ID: `created_at`, `tags`, `meta`, `decay_class`, `last_checked`, `parent_shard_id`, `trace_ref`. These are descriptive metadata, not content. Two shards with the same atoms+scope+origin are the same shard regardless of when or how they were minted.

This is deliberate. It means an agent re-deriving the same knowledge tomorrow gets the same ID it got today, and a network of agents converges on a single object instead of a swarm of near-duplicates.

### DecayClass — how long shards live

```python
DecayClass.PERMANENT   # never pruned
DecayClass.STABLE      # 90 days
DecayClass.ACTIVE      # 14 days
DecayClass.SESSION     # 24 hours
DecayClass.CHECKPOINT  # 4 hours
```

| Class | TTL | Use for |
|-------|-----|---------|
| `PERMANENT` | Never | Identities, foundational architecture, core conventions |
| `STABLE` | 90 days | Project details, learned patterns, established relationships |
| `ACTIVE` | 14 days | Current sprint goals, active monitoring, in-progress tasks |
| `SESSION` | 24 hours | Debugging context, scratch state, transient observations |
| `CHECKPOINT` | 4 hours | Pre-flight state saves, recovery snapshots |

Pick the shortest TTL that still serves the use case. Permanent isn't a default — it's a commitment.

### Keepalive via `last_checked`

If `last_checked` is set when an agent re-validates a shard, the TTL anchors to that timestamp instead of `created_at`. This is the keepalive pattern for actively-polled shards: they stay alive as long as something is checking them, and decay naturally once attention moves elsewhere.

There's a subtlety. `last_checked` and `check_count` aren't part of the content address — mutating them doesn't change `shard_id`. And `store.put()` is no-op for existing IDs, so calling `put()` after mutation will *not* update the on-disk file. To persist a keepalive update, write the shard JSON directly to its content-addressed path:

```python
from datetime import datetime, timezone

shard.last_checked = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
shard.check_count += 1

# Direct write — store.put() would be a no-op since the file already exists
path = store._shard_path(shard.shard_id)
path.write_text(shard.to_json(), encoding="utf-8")
```

This reaches into a private method (`_shard_path`); there's no public keepalive helper today. If you need this pattern often, wrap it in your application layer or open an issue for a first-class API.

### Lineage via `parent_shard_id`

Shards are immutable. To "update" a shard, mint a new one and link the predecessor:

```python
v1 = MemoryShard(
    atoms=[ShardAtom(text="Initial analysis", kind=AtomKind.CONTEXT)],
    scope="sw:article",
    origin="analyzer",
)
store.put(v1)

v2 = MemoryShard(
    atoms=[ShardAtom(text="Revised analysis with corrections", kind=AtomKind.CONTEXT)],
    scope="sw:article",
    origin="analyzer",
    parent_shard_id=v1.shard_id,  # provenance chain
)
store.put(v2)
```

`v1` stays addressable. Anyone holding the old ID can still resolve it. Lineage is forward-readable (`v2.parent_shard_id` points back) but not backward — the store doesn't index "children of v1." If you need that, use a named ref and walk the chain manually.

For the supersede-with-stable-name pattern (a ref pointing to "the latest"), see [Named Refs in shard-store.md](shard-store.md#named-refs).

## Hydration — turning shards into prompt context

`hydrate_context()` renders a shard as XML-tagged text suitable for prompt injection:

```python
print(shard.hydrate_context())
```

```xml
<shard scope="project:myproject" label="architecture">
- [fact] api-gateway.rate_limit = 100/min
- [decision] myproject.session_backend = postgresql
- [convention] team.deploy_policy = no-friday-deploys
</shard>
```

Instructions render as actionable bullets instead of bracketed kind labels:

```xml
- **startup_step_1**: Run 'alembic upgrade head' before starting the app
```

For token-constrained contexts, `hydrate_compact()` strips the XML wrapper and atom kinds — typically 40-60% fewer characters depending on atom shape (a 3-atom mixed shard measures around 50%). Use it when assembling large multi-shard contexts under a tight budget.

### Token estimation

```python
print(shard.token_estimate)   # rough char/4 estimate, with structured-field overhead
```

A character-based estimate, not a real tokenizer call. Good enough for budgeting; not exact. If you need precision, run the rendered string through your model's tokenizer.

## ShardRef — the wire format

Refs are what cross agent boundaries. A ref is a four-field pointer:

```python
ref = shard.ref
ref.shard_id   # SHA-256 content address
ref.scope      # entitlement boundary (used for access checks)
ref.label      # first tag, or None
ref.origin     # creating agent id
```

That's everything a sub-agent needs to fetch and validate the shard. The orchestrator doesn't ship atoms across the wire — it ships refs, and the sub-agent calls `store.hydrate(refs)` to materialize content locally. This separation is the whole architecture in one move: lightweight orchestration, heavy content stays at the edge.

## Serialization

```python
json_str = shard.to_json()                   # canonical JSON for storage
restored = MemoryShard.from_json(json_str)
assert restored.shard_id == shard.shard_id

d = shard.to_dict()
restored2 = MemoryShard.from_dict(d)
```

Deserialization verifies content address integrity. If the stored `shard_id` doesn't match the recomputed hash, `from_dict()` raises `ValueError`. This catches truncated files, in-flight tampering, and accidentally-mutated atoms — every load is a checksum.

`to_json()` skips empty optional fields (no `tags`, `meta`, `parent_shard_id`, etc.) to keep the wire form compact.

## Atom lookup

```python
atom = shard.get_atom("rate_limit")
print(atom.value)   # "100/min"
```

Returns the *first* atom with a matching key, or `None`. If you have multiple atoms with the same key, this returns one of them — typically a sign your atoms should be in different shards or have more specific keys.

## Scope conventions

Scopes are free-form strings — the format is whatever you want — but these patterns are used across the spiritwriter ecosystem. Stick to them when interoperating with existing modules.

| Pattern | Used by | Example |
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

Entitlement tokens match scopes via `fnmatch`, so `"project:*"` covers any `project:foo`. Pick scope hierarchies that let you grant access at the right granularity.

## What shards are not

A few common misconceptions worth heading off:

- **Not editable.** "Update this atom" doesn't exist. Mint a new shard with `parent_shard_id` linking back.
- **Not queryable beyond scope/entity.** No SQL, no joins, no full-text search at this layer. For richer query patterns, see the higher-level `kb/` module.
- **Not access-controlled by themselves.** A bare `MemoryShard` carries no encryption. Access control comes from [encryption](encryption.md) and [entitlements](shard-store.md#entitlement-aware-hydration) layered on top.
- **Not ordered.** Atoms are a list, but the order has no semantic weight. Don't encode sequencing in atom position; use explicit `key` values or `INSTRUCTION` atoms with numbered keys.

For where to put shards once you have them, see [shard-store.md](shard-store.md).
