# Shard Store

The **ShardStore** is a content-addressed, file-based storage engine for memory shards. The layout mirrors Git's object database: shards are immutable, addressed by SHA-256 of their content, and fanned out into directories by the first two hex chars of their ID. Refs are mutable pointers — like Git branches — that name the *current* shard for a given purpose.

Local-first by default. Plug in a network resolver and the same store becomes the L1 cache in front of an L2 DHT.

## Why content-addressed?

Three properties fall out of using `sha256(content)` as the shard ID:

- **`put()` is idempotent.** Storing the same content twice is a no-op. No "upsert" logic, no version conflicts.
- **Integrity is free.** If the bytes change, the ID changes. Tampering is detectable without a separate hash field.
- **Distribution is trivial.** Two stores holding the same shard ID are holding bit-identical content. That's what makes the network resolver (L1/L2) work without a coordination layer.

Shards are immutable. To "update" a shard, you create a new one (with `parent_shard_id` linking to the predecessor) and re-point a named ref. This is the same pattern as Git commits.

## Storage Layout

```
~/.myapp/shards/                   # store root (the path you pass to ShardStore)
├── shards/                        # content-addressed objects
│   ├── a1/
│   │   ├── b2c3d4e5f6...json          # plaintext shard
│   │   ├── 01234567ab...enc.json      # AES-256-GCM encrypted
│   │   └── 89abcdef01...sealed.json   # NaCl sealed-box (zero-knowledge)
├── refs/                          # named pointers (mutable, like git branches)
│   ├── project-myapp.ref          # contains a single shard_id
│   └── user-identity.ref
└── index.json                     # scope → [shard_id] map for fast queries
```

The `shards/` subdirectory inside the store root holds the object database — the doubled name is intentional and matches Git's `.git/objects/`. Encrypted and sealed shards live alongside plaintext ones, distinguished only by filename suffix. The scope index is rebuildable from disk; treat it as a cache, not a source of truth.

## Initialize

```python
from spiritwriter.fabric.store import ShardStore

store = ShardStore("~/.myapp/shards")
```

The directory structure is created on first call. Pass an optional `resolver=` to enable network fallback — see [Network Fallback](#network-fallback-l1l2) below.

## Storing and Retrieving

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass

shard = MemoryShard(
    atoms=[
        ShardAtom(text="User prefers verbose output", kind=AtomKind.PREFERENCE,
                  entity="config", key="verbosity", value="verbose"),
    ],
    scope="user:config",
    origin="setup-agent",
    decay_class=DecayClass.STABLE,
)

ref = store.put(shard)               # idempotent — same content, no-op
retrieved = store.get(ref.shard_id)  # by content address
exists = store.has(ref.shard_id)
store.delete(ref.shard_id)           # removes file and updates scope index
```

`put()` returns a `ShardRef` — a lightweight pointer (shard_id + scope + decay_class + tags) suitable for passing across agent boundaries without dragging the full content along. The actual atoms stay in the store until `hydrate()` resolves them.

`get()` returns `None` for unknown IDs. If a network resolver is configured, it transparently falls back to L2 before giving up.

## Querying

```python
# All shards in a scope (uses index.json — fast)
shards = store.by_scope("user:config")

# All shards mentioning a given entity (scans plaintext shards — slower)
articles = store.by_entity("article:f3a8b2...")

# All known scopes
scopes = store.list_scopes()

# Iterate everything (skips encrypted and sealed payloads by filename)
for shard in store.iter_all():
    print(f"{shard.scope}: {len(shard.atoms)} atoms")

print(store.count())
```

`by_entity()` exists for a specific pattern: multiple agents each contribute atoms about the same entity (e.g. `article:{sha256(url)}`) without coordinating. Consumers merge atoms across the returned shards. It scans every plaintext shard, so it's not free — index by scope when you can.

`iter_all()` only yields plaintext shards. Encrypted (`.enc.json`) and sealed (`.sealed.json`) payloads are skipped by filename. This is deliberate: their atoms are unreadable without the relevant key, so deserializing them as plain `MemoryShard` would fail anyway.

## Named Refs — Mutable Pointers

Refs are how you name "the current X" without breaking immutability:

```python
store.set_ref("project-myapp", shard.shard_id)
shard_id = store.get_ref("project-myapp")     # → str | None
latest = store.resolve_ref("project-myapp")   # → MemoryShard | None
all_refs = store.list_refs()
project_refs = store.list_refs(prefix="project-")
store.delete_ref("project-myapp")             # ref only — shard is preserved
```

The supersede pattern — replacing one shard with a newer one while keeping a stable name:

```python
new_shard = MemoryShard(
    atoms=[...],
    scope="project:myapp",
    origin="dev-agent",
    parent_shard_id=shard.shard_id,   # provenance link to predecessor
)
store.put(new_shard)
store.set_ref("project-myapp", new_shard.shard_id)
```

The old shard stays on disk. Anyone holding the old `shard_id` directly can still resolve it. Only the *name* moved.

Ref names are filesystem-safe across platforms — Windows-illegal characters (`<>:"/\|?*`), reserved device names (`CON`, `COM1`, ...), and trailing dots/spaces are percent-encoded transparently. You can use any string as a ref name.

## Scope Movement

Shards are immutable, so changing a shard's scope means creating a new one:

```python
new_shard = store.move_scope(shard.shard_id, "project:myapp")
# Original stays in "user:config"
# new_shard is a copy in "project:myapp" with parent_shard_id linking to original
```

Use this to promote a session-scoped scratch shard into a project-scoped permanent record without losing the lineage.

## Hydration — The Main Use Case

Hydration is what makes shards useful at runtime: convert refs into a string an agent can drop into a prompt.

```python
refs = [shard_a.ref, shard_b.ref, shard_c.ref]
context = store.hydrate(refs)        # XML-tagged context string
shards = store.resolve_many(refs)    # raw shards, missing ones silently skipped
```

The orchestrator passes refs (cheap) to a sub-agent. The sub-agent calls `hydrate()` on its own store to produce the actual context. This separation is the whole point: lightweight refs cross agent boundaries, full content materializes only at the consumption site.

`hydrate()` returns an empty string if no refs resolve. `resolve_many()` skips missing refs without raising — pair it with `len(shards) == len(refs)` if you need to detect partial resolution.

## Encrypted Shards — Operator-Visible Metadata

AES-256-GCM encrypted shards keep scope and atom count visible (the operator needs them for indexing and entitlement checks) while the content is opaque without the key:

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)

# Operator side — can list and route, can't read content
enc = store.get_encrypted(encrypted.shard_id)
print(enc.atom_count)        # visible
print(enc.scope)             # visible
# enc.encrypted_payload      # opaque without the key

# Holder of the key — full access
decrypted = store.decrypt_and_get(encrypted.shard_id, key)
```

Use this when the operator needs to participate in routing or scope-based access control but shouldn't see content.

## Sealed Shards — Zero-Knowledge

NaCl sealed-boxes are stricter: the operator cannot decrypt, period. Only the holder of the owner private key can:

```python
from spiritwriter.fabric.sealed import generate_owner_keypair

keypair = generate_owner_keypair()
sealed = store.seal_and_store(shard, keypair.public_key)

# Operator — scope/atom_count visible, payload truly opaque
s = store.get_sealed(sealed.shard_id)

# Owner — needs the private key
decrypted = store.unseal_and_get(sealed.shard_id, keypair.private_key)
```

| Mode | Operator can decrypt? | Use when |
|------|----------------------|----------|
| Plaintext | n/a — no encryption | Local-only, trusted environment |
| Encrypted (`encrypt_and_store`) | Yes, with key | Operator routes shards but is also a holder |
| Sealed (`seal_and_store`) | No — owner-only | Operator must not see content (zero-knowledge) |

Sealed-box support requires `pip install -e ".[sealed]"` (PyNaCl). Encrypted-box support is in core.

## Entitlement-Aware Hydration

`hydrate_with_entitlement()` is the safe way to give a sub-agent access to encrypted shards. The token carries the keys, the scopes, the capabilities, and the budget — the store enforces all of them before decrypting:

```python
from spiritwriter.fabric.entitlement import create_entitlement, Capability
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)

token = create_entitlement(
    granted_to="sub-agent",
    granted_by="orchestrator",
    shard_keys={encrypted.shard_id: key},     # raw bytes; create_entitlement serializes
    scopes=["project:*"],                      # fnmatch patterns
    capabilities=[Capability.SHARD_READ],
    secrets=[],                                # no secret-store entitlements
    budget_usd=5.0,
)

context = store.hydrate_with_entitlement(token)
```

What the store checks before returning content:

1. **Token not expired** — raises `PermissionError` if past `expires_at`.
2. **Token has `SHARD_READ`** — raises `PermissionError` if missing.
3. **Token's scopes match each shard's scope** — fnmatch, so `"project:*"` covers `"project:myapp"`. Raises `PermissionError` on mismatch.
4. **Shard exists** — missing shards are skipped silently (might be on a DHT the resolver hasn't reached yet).

Pass `key` as raw bytes. `create_entitlement` does the serialization internally — passing an already-serialized key double-serializes and breaks decryption.

## Maintenance

### Pruning

```python
pruned = store.prune_expired()
print(f"Pruned {pruned} expired shards")
```

TTL is anchored to `last_checked` if set, otherwise `created_at`. Setting `last_checked` on a shard whenever an agent re-validates it keeps actively-polled shards alive without rewriting them — a poll-as-keepalive pattern.

| DecayClass | TTL |
|------------|-----|
| `PERMANENT` | Never pruned |
| `STABLE` | 90 days |
| `ACTIVE` | 14 days |
| `SESSION` | 24 hours |
| `CHECKPOINT` | 4 hours |

Pruning is opt-in. Nothing runs automatically — call `prune_expired()` from a cron job, an agent's idle hook, or a `studio` job teardown.

### Statistics

```python
stats = store.stats()
# {
#     "total_shards": 142,
#     "total_atoms": 891,
#     "scopes": {"project:myapp": 45, "user:config": 12, ...},
#     "by_decay_class": {"stable": 80, "active": 42, "session": 20},
# }
```

`stats()` walks every plaintext shard. For large stores, prefer `count()` and `list_scopes()` if that's all you need.

## Network Fallback (L1/L2)

Plug in a resolver and `get()` becomes a two-tier lookup: local file first, network second, then cache the result locally:

```python
from spiritwriter.fabric.backends.ipfs import IPFSBackend

ipfs = IPFSBackend()
store = ShardStore("~/.myapp/shards", resolver=ipfs)

shard = store.get("abc123...")   # L1 miss → L2 fetch → local cache → return
```

This applies to all three shard types — plaintext (`get`), encrypted (`get_encrypted`), and sealed (`get_sealed`) each have their own resolver hook. A miss at both tiers returns `None`.

The cache-on-fetch behavior means a once-resolved shard is local forever (until `prune_expired()` reclaims it). This is the right behavior for content-addressed storage — the bytes can't change, so caching has no staleness risk.

## What the Store Is Not

A few things worth being explicit about:

- **Not a database.** No transactions, no joins, no full-text search. Queries are limited to scope and entity lookups.
- **Not concurrent-safe.** Multiple processes writing to the same store can race on `index.json`. Use one writer per store, or lock at the application layer.
- **Not access-controlled by itself.** `get()` returns whatever's on disk. Encryption + entitlements provide access control; the bare store doesn't.
- **Not a queue.** Shards have no ordering beyond `created_at`. `parent_shard_id` gives you lineage but not a stream.

For richer query patterns (entity resolution across shards, semantic search over atoms), see [entity-resolution.md](entity-resolution.md) and the higher-level `kb/` module.
