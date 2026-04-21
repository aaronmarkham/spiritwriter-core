# Distributing Shards over IPFS

How to publish, resolve, and discover shards across nodes using the network resolver.

## Overview

The network layer adds L2 resolution to ShardStore:

- **Publish** shards from a local store to a private IPFS swarm
- **Resolve** shards that aren't local by fetching them from the network
- **Discover** available shards via published manifests
- **Cache** fetched shards locally so subsequent reads are instant

All of this is opt-in. Projects that don't need network resolution keep using ShardStore as-is with no new dependencies.

## Prerequisites

```bash
pip install 'spiritwriter-core[network]'
```

Read the relevant skills for deeper reference:
- `skills/shards/SKILL.md` — shard format, storage, hydration
- `skills/network/SKILL.md` — API reference, config, exceptions
- `skills/entitlements/SKILL.md` — encryption before publishing (recommended)

You'll also need a running Kubo (IPFS) node. For local development:
```bash
# Install Kubo: https://docs.ipfs.tech/install/
ipfs init
ipfs daemon
```

## Quick Start

### 1. Create the Backend

```python
from spiritwriter.fabric.backends.ipfs import IPFSBackend, IPFSConfig
from spiritwriter.fabric.store import ShardStore

# For development (disable private swarm requirement)
config = IPFSConfig(require_private_swarm=False)
backend = IPFSBackend(store_root="./my-shards", config=config)

# Wire into ShardStore for automatic L2 fallback
store = ShardStore("./my-shards", resolver=backend)
```

### 2. Publish a Shard

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind

shard = MemoryShard(
    atoms=[ShardAtom(text="IPFS distribution is live", kind=AtomKind.FACT)],
    scope="project:infra",
    origin="deploy-agent",
)

# Store locally first
store.put(shard)

# Then publish to IPFS (explicit — put() never auto-publishes)
loc = backend.publish(shard)
print(f"Published: {loc.shard_id[:16]}... -> CID {loc.cid}")
```

### 3. Resolve from Another Node

On a different machine with the same private swarm:

```python
# This node has the CID map (shared or synced separately)
store2 = ShardStore("./other-shards", resolver=backend2)

# get() checks local first, then falls back to IPFS
shard = store2.get(shard_id)
# First call: fetched from IPFS, cached locally
# Second call: served from local cache
```

### 4. Publish a Manifest for Discovery

```python
from spiritwriter.fabric.network import ShardManifest

# Publish all active job shards
locations = [backend.publish(s) for s in store.by_scope("project:jobs")]

manifest = ShardManifest(
    scope="project:jobs:active",
    entries=locations,
    publisher_id="orchestrator-01",
)
manifest_cid = backend.publish_manifest(manifest)
print(f"Manifest CID: {manifest_cid}")
# Share this CID via API endpoint, DNS TXT, IPNS, etc.
```

### 5. Consume a Manifest

```python
manifest = backend.resolve_manifest(manifest_cid)
print(f"Scope: {manifest.scope}, {len(manifest.entries)} shards")

for entry in manifest.entries:
    raw = backend.resolve_by_cid(entry.cid)
    shard = MemoryShard.from_json(raw.decode("utf-8"))
    print(f"  {shard.shard_id[:16]}... [{shard.scope}] {len(shard.atoms)} atoms")
```

## Encryption Before Publishing

Publishing plaintext shards to any network — even a private swarm — means anyone on the swarm can read them. For sensitive content, encrypt first:

```python
from spiritwriter.fabric.crypto import encrypt_shard, generate_job_key

key = generate_job_key()
encrypted = encrypt_shard(shard, key)

# Store and publish the encrypted version
store.put_encrypted(encrypted)
loc = backend.publish_encrypted(encrypted)
# Network sees: shard_id, scope, opaque ciphertext, atom_count, timestamps
# Network does NOT see: atom text, entity/key/value, tags, meta
```

For zero-knowledge scenarios (operator can't see content):

```python
from spiritwriter.fabric.sealed import seal_shard, generate_owner_keypair

keypair = generate_owner_keypair()
sealed = seal_shard(shard, keypair.public_key)

store.put_sealed(sealed)
loc = backend.publish_sealed(sealed)
# Only the owner (holder of private key) can decrypt
```

## Private Swarm Setup

### Why Private by Default

Even sealed shards leak metadata in plaintext on the envelope: scope, origin_agent, atom_count, timestamps. On the public IPFS network, anyone can discover and read this metadata. A private swarm limits the audience to nodes you control.

### End-to-End Setup

**1. Generate a swarm key:**

```bash
# Requires go-ipfs-swarm-key-gen or similar tool
go run github.com/Kubuxu/go-ipfs-swarm-key-gen/ipfs-swarm-key-gen > swarm.key
```

**2. Store it in spiritwriter secrets:**

```bash
# Extract the hex key from swarm.key (last line)
spiritwriter secrets set IPFS_SWARM_KEY
# Paste the 64-character hex string
```

**3. Configure each Kubo node:**

```bash
# Copy swarm.key to IPFS config directory
cp swarm.key ~/.ipfs/swarm.key

# Remove public bootstrap peers
ipfs bootstrap rm --all

# Add only your private peers
ipfs bootstrap add /ip4/10.0.1.5/tcp/4001/p2p/QmYourPeerId

# Force private network mode
export LIBP2P_FORCE_PNET=1
ipfs daemon
```

**4. Verify from spiritwriter:**

```python
config = IPFSConfig(require_private_swarm=True)  # default
backend = IPFSBackend(store_root="./shards", config=config)

assert backend.is_available()  # True if Kubo is up AND on private swarm
```

### Docker Compose

For containerized deployments, configure via environment:

```yaml
services:
  ipfs:
    image: ipfs/kubo:latest
    environment:
      - LIBP2P_FORCE_PNET=1
    volumes:
      - ./swarm.key:/data/ipfs/swarm.key
      - ipfs-data:/data/ipfs

  worker:
    environment:
      - IPFS_API_URL=http://ipfs:5001
      - IPFS_GATEWAY_URL=http://ipfs:8080
      - IPFS_REQUIRE_PRIVATE_SWARM=1
```

```python
# In the worker container
config = IPFSConfig.from_env()  # reads IPFS_API_URL, etc.
backend = IPFSBackend(store_root="/data/shards", config=config)
```

## How CID Mapping Works

Shard IDs are SHA-256 hex digests. IPFS CIDs are base58btc-encoded multihashes. They refer to the same content but use different encodings.

The `cid_map.json` file bridges them:

```json
{
  "a1b2c3d4...": "QmXyz...",
  "sealed:e5f6g7h8...": "QmAbc...",
  "encrypted:i9j0k1l2...": "QmDef...",
  "manifest:m3n4o5p6...": "QmGhi..."
}
```

- Prefixed keys prevent collisions between plaintext/sealed/encrypted versions of the same shard
- Checked before every publish (idempotent — won't re-add)
- Checked on resolve (no CID = can't fetch from network)

## Resolve Flow

```
store.get(shard_id)
  |
  +-- L1: Check local file (shards/ab/cd1234...json)
  |     Found? Return immediately.
  |
  +-- L2: resolver.resolve(shard_id)
        |
        +-- Look up CID in cid_map.json
        |     No CID? Return None.
        |
        +-- POST /api/v0/cat?arg={cid}
        |     Timeout/error? Return None.
        |
        +-- Deserialize JSON -> MemoryShard
        +-- Verify shard_id matches (IntegrityError if not)
        +-- store.put(shard) to cache locally
        +-- Return shard
```

Same pattern applies to `get_sealed()` and `get_encrypted()`.

## Testing

### Unit tests (no IPFS needed)

```bash
python -m pytest tests/test_network.py -v
```

Tests CID map, ShardStore fallback with mock resolver, manifest serialization, swarm config.

### Integration tests (requires local Kubo)

```bash
python -m pytest tests/test_ipfs_backend.py -v -m ipfs
```

Publish/resolve round-trips, pin/unpin, manifests. Skipped automatically if Kubo isn't running.

## File Reference

| File | Purpose |
|------|---------|
| `spiritwriter/fabric/network.py` | NetworkResolver protocol, ShardLocation, ShardManifest, exceptions |
| `spiritwriter/fabric/backends/__init__.py` | Backends package |
| `spiritwriter/fabric/backends/ipfs.py` | IPFSBackend, IPFSConfig, swarm verification |
| `spiritwriter/fabric/store.py` | ShardStore with optional resolver injection |
| `tests/test_network.py` | Unit tests (mock resolver) |
| `tests/test_ipfs_backend.py` | Integration tests (requires Kubo) |
| `docs/specs/network-resolver-spec.md` | Original design spec |
