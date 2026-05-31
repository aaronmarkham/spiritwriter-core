# Skill: Spiritwriter Network Resolver

IPFS-backed shard distribution with private swarm enforcement.

## When to Use

- You need to **publish shards** so remote consumers can fetch them (extensions, other nodes)
- You need to **resolve shards** that aren't in the local store by falling back to the network
- You need to **publish manifests** so consumers can discover which shards are available
- You need to **configure IPFS** for Docker/container deployments

## Install

```bash
pip install -e /path/to/spiritwriter-core
pip install 'spiritwriter[network]'  # adds requests
```

## Concepts

| Concept | What it is |
|---------|-----------|
| **NetworkResolver** | Protocol (interface) for network-backed resolution. Transport-agnostic. |
| **IPFSBackend** | First implementation of NetworkResolver. Talks to Kubo via HTTP API. |
| **ShardLocation** | Where a shard lives: shard_id, CID, local flag, pinned flag. |
| **ShardManifest** | Published index of ShardLocations. Solves discovery ("which shards exist?"). |
| **CID Map** | Local JSON file mapping shard_id to IPFS CID. Avoids re-publishing. |
| **Private Swarm** | Default mode. Kubo node must be on a private IPFS network (swarm key enforced). |

## Architecture

```
ShardStore (local files)           NetworkResolver
  put() / get() / has()     <->      publish() / resolve() / pin()
                                         |
                                    IPFSBackend (Kubo HTTP API)
                                         |
                                    http://localhost:5001/api/v0/...
```

ShardStore is L1 (local cache). Network is L2 (fallback on miss). Publishing is explicit — `put()` does NOT auto-publish.

## Python API

### Set up the backend

```python
from spiritwriter.fabric.backends.ipfs import IPFSBackend, IPFSConfig

# Default (localhost Kubo, private swarm required)
backend = IPFSBackend(store_root="/path/to/shards")

# Docker deployment (reads IPFS_API_URL, etc. from env)
config = IPFSConfig.from_env()
backend = IPFSBackend(store_root="/path/to/shards", config=config)

# Explicit config
config = IPFSConfig(
    api_url="http://frio-ipfs:5001",
    gateway_url="http://frio-ipfs:8080",
    require_private_swarm=True,
)
backend = IPFSBackend(store_root="/path/to/shards", config=config)
```

### Wire into ShardStore

```python
from spiritwriter.fabric.store import ShardStore

store = ShardStore("/path/to/shards", resolver=backend)

# get() now falls back to network on local miss
shard = store.get(shard_id)  # L1: local file, L2: IPFS, then cache locally
```

### Publish a shard

```python
# Publish to private swarm (default)
loc = backend.publish(shard)
print(loc.cid)  # QmXyz...

# Publish encrypted (network sees opaque bytes)
loc = backend.publish_encrypted(encrypted_shard)

# Publish sealed (NaCl sealed box)
loc = backend.publish_sealed(sealed_shard)

# Explicit public publish of PLAINTEXT (bypasses private swarm).
# Requires confirm_public=True — the content is permanently world-readable.
loc = backend.publish_public(shard, confirm_public=True)
```

### Resolve a shard

```python
# By shard_id (looks up CID in local cid_map.json)
shard = backend.resolve(shard_id)

# By CID (raw bytes, caller deserializes)
raw = backend.resolve_by_cid(cid)

# Sealed/encrypted variants
sealed = backend.resolve_sealed(shard_id)
encrypted = backend.resolve_encrypted(shard_id)
```

### Publish and resolve manifests

```python
from spiritwriter.fabric.network import ShardManifest

# Publisher side
manifest = ShardManifest(
    scope="frio:jobs:active",
    entries=[backend.publish(s) for s in active_shards],
    publisher_id="node-1",
)
manifest_cid = backend.publish_manifest(manifest)

# Consumer side
manifest = backend.resolve_manifest(manifest_cid)
for entry in manifest.entries:
    shard = backend.resolve_by_cid(entry.cid)
```

### Pin management

```python
backend.pin(cid)    # prevent GC on the IPFS node
backend.unpin(cid)  # allow GC
```

### Check availability

```python
if backend.is_available():
    # Kubo is reachable AND on the correct swarm (if required)
    ...
```

## Configuration

### IPFSConfig fields

| Field | Default | Description |
|-------|---------|-------------|
| `api_url` | `http://127.0.0.1:5001` | Kubo API endpoint |
| `gateway_url` | `http://127.0.0.1:8080` | Read-only gateway |
| `timeout_seconds` | `30` | HTTP timeout |
| `pin_by_default` | `True` | Auto-pin on publish |
| `require_private_swarm` | `True` | Refuse to operate on public IPFS |

### Environment variables (IPFSConfig.from_env())

| Env var | Maps to | Default |
|---------|---------|---------|
| `IPFS_API_URL` | `api_url` | `http://127.0.0.1:5001` |
| `IPFS_GATEWAY_URL` | `gateway_url` | `http://127.0.0.1:8080` |
| `IPFS_TIMEOUT` | `timeout_seconds` | `30` |
| `IPFS_PIN_BY_DEFAULT` | `pin_by_default` | `1` (set `0` to disable) |
| `IPFS_REQUIRE_PRIVATE_SWARM` | `require_private_swarm` | `1` (set `0` for public) |

## Private Swarm

By default, IPFSBackend requires a private IPFS swarm. This prevents accidental publication of shards to the public network.

**Setup:**

1. Store the swarm key in spiritwriter secrets:
   ```bash
   spiritwriter secrets set IPFS_SWARM_KEY
   ```

2. Configure Kubo with the matching `swarm.key` file and `LIBP2P_FORCE_PNET=1`

3. Remove public bootstrap peers from Kubo config

**What gets verified:**
- `IPFS_SWARM_KEY` exists in spiritwriter secrets
- Kubo node has no public bootstrap peers (libp2p.io, default IPs)

**Opt out** (e.g., for intentionally public content):
```python
config = IPFSConfig(require_private_swarm=False)
```

## Exceptions

| Exception | When |
|-----------|------|
| `NetworkUnavailable` | Kubo not reachable |
| `NetworkTimeout` | Request timed out |
| `IntegrityError` | Fetched shard_id doesn't match expected (corrupted data) |
| `SwarmMismatchError` | Node is on public IPFS when private swarm required |

## Storage Layout

```
store_root/
  shards/...          # existing shard files
  index.json          # existing scope index
  refs/...            # existing named refs
  cid_map.json        # NEW: { "sha256_hex": "QmCID", "sealed:sha256_hex": "QmCID", ... }
```

## Source Files

- `spiritwriter/fabric/network.py` — NetworkResolver protocol, ShardLocation, ShardManifest, exceptions
- `spiritwriter/fabric/backends/ipfs.py` — IPFSBackend (Kubo HTTP client), IPFSConfig
- `spiritwriter/fabric/store.py` — ShardStore with optional resolver injection
