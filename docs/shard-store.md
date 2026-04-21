# Shard Store

The **ShardStore** is a content-addressed, file-based storage engine for memory shards. Its layout mirrors Git's object storage — designed to be local-first but DHT-ready for distributed access.

## Storage Layout

```
shards/                          # root directory
├── shards/                      # content-addressed objects
│   ├── a1/
│   │   └── b2c3d4e5f6...json   # shard file (first 2 hex chars = directory)
│   ├── ef/
│   │   └── 01234567ab...enc.json     # AES-encrypted shard
│   │   └── 89abcdef01...sealed.json  # NaCl-sealed shard
│   └── ...
├── refs/                        # named references (like git branches)
│   ├── project-myapp.ref        # contains a shard_id string
│   └── user-identity.ref
└── index.json                   # scope → [shard_id] mapping
```

## Basic Operations

### Initialize

```python
from spiritwriter.fabric.store import ShardStore

store = ShardStore("~/.myapp/shards")
# Creates directory structure on first call
```

### Store and Retrieve

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

# Store — returns a ShardRef. Idempotent (same content = no-op).
ref = store.put(shard)

# Retrieve by content address
retrieved = store.get(ref.shard_id)
assert retrieved is not None
assert retrieved.atoms[0].value == "verbose"

# Check existence
assert store.has(ref.shard_id)

# Delete
store.delete(ref.shard_id)
assert not store.has(ref.shard_id)
```

### Query by Scope

```python
# All shards in a scope
shards = store.by_scope("user:config")

# List all scopes
scopes = store.list_scopes()  # ["user:config", "project:myapp", ...]

# Iterate everything
for shard in store.iter_all():
    print(f"{shard.scope}: {len(shard.atoms)} atoms")

# Count
print(store.count())
```

### Named Refs

Named refs work like Git branches — mutable pointers to immutable shards:

```python
# Point "project-myapp" to the latest project context shard
store.set_ref("project-myapp", shard.shard_id)

# Resolve ref to shard_id
shard_id = store.get_ref("project-myapp")

# Resolve ref directly to shard content
latest = store.resolve_ref("project-myapp")

# List all refs
all_refs = store.list_refs()
project_refs = store.list_refs(prefix="project-")

# Delete ref (doesn't delete shard)
store.delete_ref("project-myapp")
```

**Pattern: Updating a ref when content changes**

```python
# New shard supersedes old
new_shard = MemoryShard(
    atoms=[...],  # updated atoms
    scope="project:myapp",
    origin="dev-agent",
    parent_shard_id=shard.shard_id,  # link to predecessor
)
ref = store.put(new_shard)
store.set_ref("project-myapp", new_shard.shard_id)  # update pointer
```

### Scope Movement

Create a copy of a shard under a different scope:

```python
# Promote a session shard to project scope
new_shard = store.move_scope(shard.shard_id, "project:myapp")
# Original stays in "user:config", new shard in "project:myapp"
# new_shard.parent_shard_id links to original
```

## Hydration

The primary use case for stores is **agent context hydration** — resolving shard refs into injectable context:

```python
# Agent receives refs (lightweight pointers)
refs = [shard_a.ref, shard_b.ref, shard_c.ref]

# Resolve refs → render context
context = store.hydrate(refs)
# Returns XML-tagged text ready for prompt injection

# Resolve individual refs
shards = store.resolve_many(refs)  # skips missing refs
```

## Encrypted Shard Operations

The store supports AES-256-GCM encrypted shards. Scope metadata remains visible; content is encrypted:

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()

# Encrypt and store in one step
encrypted = store.encrypt_and_store(shard, key)

# Retrieve encrypted (without decrypting)
enc = store.get_encrypted(encrypted.shard_id)
print(enc.atom_count)    # visible
print(enc.scope)         # visible
# enc.encrypted_payload  # opaque

# Decrypt and retrieve in one step
decrypted = store.decrypt_and_get(encrypted.shard_id, key)
```

## Sealed Shard Operations

NaCl sealed-box shards support zero-knowledge storage — the operator cannot decrypt:

```python
from spiritwriter.fabric.sealed import generate_owner_keypair

keypair = generate_owner_keypair()

# Seal and store
sealed = store.seal_and_store(shard, keypair.public_key)

# Retrieve sealed (operator side — can't decrypt)
s = store.get_sealed(sealed.shard_id)
print(s.atom_count)  # visible
# s.sealed_payload   # opaque to operator

# Unseal (owner side — needs private key)
decrypted = store.unseal_and_get(sealed.shard_id, keypair.private_key)
```

## Entitlement-Aware Hydration

Hydrate shards through an entitlement token — validates expiry, scope, and capability before decrypting:

```python
from spiritwriter.fabric.entitlement import create_entitlement, Capability
from spiritwriter.fabric.crypto import generate_job_key, serialize_key

key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)

token = create_entitlement(
    granted_to="sub-agent",
    granted_by="orchestrator",
    shard_keys={shard.shard_id: serialize_key(key)},
    scopes=["project:*"],
    capabilities=[Capability.SHARD_READ],
    budget_usd=5.0,
)

# Sub-agent hydrates with their token
context = store.hydrate_with_entitlement(token)
# Validates: not expired, has SHARD_READ, scope matches
# Decrypts all entitled shards, renders as context string
```

## Maintenance

### Pruning Expired Shards

```python
# Remove shards past their decay TTL
pruned_count = store.prune_expired()
print(f"Pruned {pruned_count} expired shards")
```

TTL is anchored to `last_checked` (if set) or `created_at`:

| DecayClass | TTL |
|------------|-----|
| PERMANENT | Never pruned |
| STABLE | 90 days |
| ACTIVE | 14 days |
| SESSION | 24 hours |
| CHECKPOINT | 4 hours |

### Statistics

```python
stats = store.stats()
print(stats)
# {
#     "total_shards": 142,
#     "total_atoms": 891,
#     "scopes": {"project:myapp": 45, "user:config": 12, ...},
#     "by_decay_class": {"stable": 80, "active": 42, "session": 20},
# }
```

## Network Fallback (L1/L2)

The store supports an optional network resolver for distributed access. If a shard isn't found locally (L1), the store falls back to the network (L2):

```python
from spiritwriter.fabric.backends.ipfs import IPFSBackend

ipfs = IPFSBackend()
store = ShardStore("~/.myapp/shards", resolver=ipfs)

# get() checks local first, then IPFS
shard = store.get("abc123...")  # L1 miss → L2 fetch → local cache
```

Fetched shards are automatically cached locally for future access.
