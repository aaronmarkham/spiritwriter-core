# Distributing Shards over IPFS

A shard sitting on host A is invisible to an agent on host B. Local-first storage gives you durability and speed; eventually you need to cross the machine boundary. The **network resolver** wraps `ShardStore` with a transparent L2: when `get(shard_id)` misses locally, the resolver fetches from a private IPFS swarm, verifies the content address, and caches the bytes locally for next time.

The pattern works because shards are content-addressed. Two stores holding the same `shard_id` are holding bit-identical content — no coordination layer, no consistency protocol, only SHA-256 and IPFS's CID lookup. Publishing is opt-in and explicit; nothing leaves a local store unless you call `publish()`.

## Install

```bash
pip install 'spiritwriter[network]'
```

The `[network]` extra adds the `requests` dependency for the Kubo HTTP API. You'll also need a running Kubo (IPFS) node — see [Private Swarm Setup](#private-swarm-setup) for production config; for local development:

```bash
ipfs init
ipfs daemon
```

## How L1/L2 Resolution Works

```
store.get(shard_id)
  │
  ├─ L1: check local file at shards/ab/cd1234...json
  │     found?  return immediately
  │
  └─ L2: resolver.resolve(shard_id)
        │
        ├─ look up CID in cid_map.json
        │     no CID?  return None — can't fetch what we don't know about
        │
        ├─ POST /api/v0/cat?arg={cid}  (Kubo HTTP API)
        │     timeout / error?  return None
        │
        ├─ deserialize JSON → MemoryShard
        ├─ verify shard_id matches recomputed hash (IntegrityError if not)
        ├─ store.put(shard) to cache locally
        └─ return shard
```

The same flow applies to `get_sealed()` and `get_encrypted()` — sealed and AES-encrypted shards each have their own resolver hook. The cache-on-fetch behavior is staleness-safe because content-addressing means the bytes can't change: a cached shard is the same shard forever (until `prune_expired()` reclaims it).

## Quick Start

```python
from spiritwriter.fabric.backends.ipfs import IPFSBackend, IPFSConfig
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind

# Local development only — allow public IPFS.
# For production, see "Private Swarm Setup" below; the default is True.
config = IPFSConfig(require_private_swarm=False)
backend = IPFSBackend(store_root="./my-shards", config=config)

# Wire into ShardStore for automatic L2 fallback
store = ShardStore("./my-shards", resolver=backend)

# Create and store a shard locally
shard = MemoryShard(
    atoms=[ShardAtom(text="IPFS distribution is live", kind=AtomKind.FACT)],
    scope="project:infra",
    origin="deploy-agent",
)
store.put(shard)

# Publish to IPFS — explicit. store.put() never auto-publishes.
loc = backend.publish(shard)
print(f"{loc.shard_id[:16]}... -> CID {loc.cid}")
```

`publish()` returns a `ShardLocation` (`shard_id`, `cid`, `local`, `pinned`). The CID lands in `cid_map.json` next to the shard files; subsequent `publish()` calls for the same shard are idempotent.

On a different machine in the same private swarm:

```python
store2 = ShardStore("./other-shards", resolver=backend2)

shard = store2.get(shard_id)
# First call:  L1 miss -> L2 fetch from IPFS -> verify -> cache locally
# Second call: served from local cache, no network round-trip
```

## Encryption Before Publishing

Plaintext shards on a private swarm are still readable by every node on the swarm. For sensitive content, encrypt first. See [encryption.md](encryption.md) for the trust-boundary decision (AES vs sealed-box).

```python
from spiritwriter.fabric.crypto import generate_job_key

key = generate_job_key()
encrypted = store.encrypt_and_store(shard, key)

# Publish the encrypted version
loc = backend.publish_encrypted(encrypted)
# Network sees: shard_id, scope, opaque ciphertext, atom_count, timestamps
# Network does NOT see: atom text, entity/key/value, tags, meta
```

For zero-knowledge — the operator literally cannot decrypt:

```python
from spiritwriter.fabric.sealed import generate_owner_keypair

keypair = generate_owner_keypair()
sealed = store.seal_and_store(shard, keypair.public_key)

loc = backend.publish_sealed(sealed)
# Only the owner (holder of the private key) can decrypt
```

The matching `resolve_encrypted(shard_id)` and `resolve_sealed(shard_id)` paths are wired into `ShardStore` automatically — `get_encrypted()` and `get_sealed()` fall back to L2 the same way `get()` does.

For delegated access (sub-agent gets a key for a specific shard, scoped + budgeted), see [entitlements.md](entitlements.md).

## Manifests for Discovery

Resolution by `shard_id` only works if the consumer already knows the ID. **Manifests** solve the discovery half — a published index of `(shard_id, cid)` pairs that consumers fetch by manifest CID, then resolve each entry.

```python
from spiritwriter.fabric.network import ShardManifest

# Publish all shards in a scope, collect their locations
locations = [backend.publish(s) for s in store.by_scope("project:jobs")]

manifest = ShardManifest(
    scope="project:jobs:active",
    entries=locations,
    publisher_id="orchestrator-01",
)
manifest_cid = backend.publish_manifest(manifest)
# Share manifest_cid via API endpoint, DNS TXT, IPNS, ...
```

On the consumer side:

```python
manifest = backend.resolve_manifest(manifest_cid)
print(f"{manifest.scope}: {len(manifest.entries)} shards")

for entry in manifest.entries:
    shard = store.get(entry.shard_id)   # L1 miss -> L2 fetch via the cid_map
    print(f"  {shard.shard_id[:16]}... [{shard.scope}] {len(shard.atoms)} atoms")
```

Manifests are themselves content-addressed (`manifest.manifest_id` is a SHA-256 hash of scope + entries + publisher_id), so an agent that re-publishes the same set produces the same manifest CID — same dedup story as shards. `entries` is plain `ShardLocation` objects; you can hand-build a manifest from arbitrary IDs without scanning a store.

## Private Swarm Setup

Even sealed shards leak metadata in the envelope: `scope`, `origin_agent`, `atom_count`, timestamps. On the public IPFS network, anyone can discover and read that metadata. A private swarm limits the audience to nodes you control — `IPFSConfig.require_private_swarm` defaults to `True` and refuses to operate against a node that doesn't pass swarm verification.

### Generate a Swarm Key

```bash
go run github.com/Kubuxu/go-ipfs-swarm-key-gen/ipfs-swarm-key-gen > swarm.key
```

Store the hex key (last line of `swarm.key`) in spiritwriter secrets — `IPFSBackend` reads it as `IPFS_SWARM_KEY`:

```bash
spiritwriter secrets set IPFS_SWARM_KEY
# paste the 64-character hex string
```

### Configure Each Kubo Node

```bash
cp swarm.key ~/.ipfs/swarm.key
ipfs bootstrap rm --all
ipfs bootstrap add /ip4/10.0.1.5/tcp/4001/p2p/QmYourPeerId
export LIBP2P_FORCE_PNET=1
ipfs daemon
```

`LIBP2P_FORCE_PNET=1` makes Kubo refuse to start without a swarm key. Every node in the swarm must use the same `swarm.key` file.

### Verify

```python
config = IPFSConfig(require_private_swarm=True)   # default
backend = IPFSBackend(store_root="./shards", config=config)

assert backend.is_available()   # True only if Kubo is up AND on the private swarm
                                # (returns False on swarm mismatch — does not raise)
```

`is_available()` is a boolean predicate — it catches both `NetworkUnavailable`/`NetworkTimeout` and `SwarmMismatchError` internally and returns `False`. The `SwarmMismatchError` does propagate from `publish()` and other operations, where the swarm check runs before any work; that's where you'd `try/except` it.

### Docker Compose

For containerized deployments, configure via env vars and use `IPFSConfig.from_env()`:

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
config = IPFSConfig.from_env()   # reads IPFS_API_URL, IPFS_GATEWAY_URL, IPFS_REQUIRE_PRIVATE_SWARM
backend = IPFSBackend(store_root="/data/shards", config=config)
```

## CID Mapping

Shard IDs are SHA-256 hex digests; IPFS CIDs are base58btc-encoded multihashes. They refer to the same content but use different encodings, so the backend keeps a `cid_map.json` in the store root (alongside the `shards/` directory) that bridges them:

```json
{
  "a1b2c3d4...":            "QmXyz...",
  "sealed:e5f6g7h8...":     "QmAbc...",
  "encrypted:i9j0k1l2...":  "QmDef...",
  "manifest:m3n4o5p6...":   "QmGhi..."
}
```

Three things to know:

- **Prefixed keys prevent collisions.** Plaintext, sealed, and encrypted versions of the same logical shard get different CIDs and live under different prefix namespaces.
- **`publish()` is idempotent.** The map is checked before every publish; re-publishing returns the existing CID without re-uploading.
- **No CID = no fetch.** Resolve consults the map first; if the shard ID isn't there, the network fallback returns `None` immediately. The map is the source of truth for "what this node has published."

The `cid_map.json` is local to each backend instance — sharing it (via API, manifest publication, or a manual sync) is how nodes discover each other's content.

## Pinning

Pin a CID to prevent garbage collection on the local Kubo node:

```python
backend.pin(loc.cid)      # returns True if pinned
backend.unpin(loc.cid)    # returns True if unpinned
```

`publish()` does not auto-pin. Pin explicitly when you want a node to remain a long-term host of the content; unpin when the content can be reclaimed.

## Testing

Unit tests don't need a live Kubo instance — they exercise the CID map, manifest serialization, swarm config parsing, and `ShardStore` fallback through a mock resolver:

```bash
python -m pytest tests/test_network.py -v
```

Integration tests require a local Kubo daemon and skip automatically if it's not running:

```bash
python -m pytest tests/test_ipfs_backend.py -v -m ipfs
```

## What Network Distribution Is Not

- **Not real-time replication.** Publishing is explicit; nothing pushes shards across the swarm without a `publish()` call. Two nodes can hold different sets of the same scope until manifests are exchanged.
- **Not distributed consensus.** There's no agreement protocol on which version of a ref points where. If two nodes update `set_ref("project-X", ...)` independently, they have different local views — content-addressed shards solve duplication, not coordination.
- **Not a CDN.** Kubo is the transport, not a high-throughput edge cache. Throughput is bounded by the slowest peer with the content; for large binary assets, ship the bytes through a real CDN and put a shard ref in the trace.
- **Not metadata-private.** Even with sealed-box content encryption, scope/atom_count/origin_agent/timestamps land in plaintext on the envelope. Private swarms restrict who can see them; nothing makes them invisible.

For tighter access control on encrypted shards, see [entitlements.md](entitlements.md). For the deeper config / exception reference (env-var precedence, swarm-key validation rules, exception taxonomy), see [`skills/network/SKILL.md`](../skills/network/SKILL.md).

## File Reference

| File | Purpose |
|------|---------|
| `spiritwriter/fabric/network.py` | `NetworkResolver` protocol, `ShardLocation`, `ShardManifest`, exceptions |
| `spiritwriter/fabric/backends/__init__.py` | Backends package |
| `spiritwriter/fabric/backends/ipfs.py` | `IPFSBackend`, `IPFSConfig`, swarm verification |
| `spiritwriter/fabric/store.py` | `ShardStore` with optional resolver injection |
| `tests/test_network.py` | Unit tests (mock resolver) |
| `tests/test_ipfs_backend.py` | Integration tests (requires Kubo) |
| `docs/specs/network-resolver-spec.md` | Original design spec |
