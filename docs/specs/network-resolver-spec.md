# Network Resolver Spec — IPFS-Backed Shard Distribution

**Status**: Draft
**Scope**: spiritwriter (generic, not Frio-specific)
**Depends on**: `spiritwriter.fabric.store.ShardStore`, `spiritwriter.fabric.shard.MemoryShard`

## Problem

ShardStore is entirely local file-based. The architecture has always intended DHT/network resolution ("shard not found — might be on DHT") but no implementation exists. Projects like Frio need to:

1. Publish shards so remote consumers can fetch them (e.g., browser extensions, other nodes)
2. Resolve shards that aren't in the local store by falling back to the network
3. Do this without coupling IPFS specifics into every downstream project

## Design Principles

- **Local-first**: ShardStore remains the L1 cache. Network is L2 fallback.
- **Transport-agnostic interface**: The resolver interface doesn't assume IPFS. First implementation uses IPFS/Kubo, but the abstraction should allow other backends.
- **Opt-in**: Projects that don't need network resolution keep using ShardStore as-is. No new required dependencies.
- **Content-addressed alignment**: Shard IDs are already SHA-256 hashes. IPFS CIDs are a different encoding of the same concept. The resolver handles the mapping.
- **Encryption-aware**: Sealed/encrypted shards can be published. The network layer moves opaque bytes — it never needs to decrypt.

## Architecture

```
ShardStore (local files)           NetworkResolver
  put() / get() / has()     ←→      publish() / resolve() / pin()
                                         |
                                    IPFSBackend (Kubo HTTP API)
                                         |
                                    http://localhost:5001/api/v0/...
```

### Module layout

```
spiritwriter/fabric/
  store.py          # Existing — unchanged except optional resolver injection
  network.py        # NEW — NetworkResolver protocol + CID mapping
  backends/
    __init__.py
    ipfs.py         # NEW — Kubo HTTP API client (IPFSBackend)
```

## Core Types

### CID Mapping

Shard IDs are `sha256:<hex>`. IPFS CIDs are typically base58btc-encoded multihash. The resolver maps between them.

```python
@dataclass
class ShardLocation:
    """Where a shard can be found."""
    shard_id: str                    # sha256:abcd1234...
    cid: str | None                  # IPFS CID (if published)
    local: bool                      # True if in local ShardStore
    pinned: bool                     # True if pinned on IPFS node
```

### Manifest

For publishing collections of shards (e.g., "all active jobs", "all skills"), a manifest is a JSON document listing shard IDs and their CIDs, itself published to IPFS.

```python
@dataclass
class ShardManifest:
    """A published index of shard locations."""
    manifest_id: str                 # Content-addressed ID of this manifest
    scope: str                       # e.g., "frio:jobs", "frio:skills"
    entries: list[ShardLocation]
    published_at: str                # ISO timestamp
    publisher_id: str                # Node/agent identity
```

## NetworkResolver Protocol

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class NetworkResolver(Protocol):
    """Interface for network-backed shard resolution."""

    def publish(self, shard: MemoryShard) -> ShardLocation:
        """Publish a shard to the network. Returns location with CID."""
        ...

    def publish_sealed(self, sealed: SealedShard) -> ShardLocation:
        """Publish a sealed (encrypted) shard. Network sees opaque bytes."""
        ...

    def publish_encrypted(self, encrypted: EncryptedShard) -> ShardLocation:
        """Publish an encrypted shard."""
        ...

    def resolve(self, shard_id: str) -> MemoryShard | None:
        """Fetch a plaintext shard from the network by shard_id."""
        ...

    def resolve_sealed(self, shard_id: str) -> SealedShard | None:
        """Fetch a sealed shard from the network."""
        ...

    def resolve_encrypted(self, shard_id: str) -> EncryptedShard | None:
        """Fetch an encrypted shard from the network."""
        ...

    def resolve_by_cid(self, cid: str) -> bytes:
        """Fetch raw bytes by CID. Caller handles deserialization."""
        ...

    def pin(self, cid: str) -> bool:
        """Pin a CID on the local IPFS node (prevent garbage collection)."""
        ...

    def unpin(self, cid: str) -> bool:
        """Unpin a CID."""
        ...

    def publish_manifest(self, manifest: ShardManifest) -> str:
        """Publish a manifest document. Returns CID of the manifest."""
        ...

    def resolve_manifest(self, cid: str) -> ShardManifest | None:
        """Fetch and parse a manifest by CID."""
        ...

    def is_available(self) -> bool:
        """Check if the network backend is reachable."""
        ...
```

## IPFSBackend Implementation

First (and for now, only) implementation of `NetworkResolver`.

### Dependencies

- `requests` (already a spiritwriter dependency via Frio, but should be optional)
- No IPFS-specific libraries — just HTTP calls to Kubo's REST API

### Installation

```
pip install 'spiritwriter[network]'
```

Optional extra in `pyproject.toml`:
```toml
[project.optional-dependencies]
network = ["requests>=2.28"]
```

### Configuration

```python
@dataclass
class IPFSConfig:
    api_url: str = "http://127.0.0.1:5001"   # Kubo API
    gateway_url: str = "http://127.0.0.1:8080" # Read-only gateway
    timeout_seconds: int = 30
    pin_by_default: bool = True                # Pin on publish
```

### Kubo API Endpoints Used

| Operation | Kubo Endpoint | Method |
|-----------|--------------|--------|
| Add/publish | `/api/v0/add` | POST (multipart) |
| Fetch by CID | `/api/v0/cat?arg={cid}` | POST |
| Pin | `/api/v0/pin/add?arg={cid}` | POST |
| Unpin | `/api/v0/pin/rm?arg={cid}` | POST |
| Check node | `/api/v0/id` | POST |

### CID ↔ Shard ID Mapping

The shard's canonical JSON (from `shard.to_json()`) is what gets published to IPFS. The resulting CID is stored in a local mapping file alongside the ShardStore:

```
store_root/
  shards/...
  index.json
  refs/...
  cid_map.json          # NEW: { "sha256:abcd...": "QmXyz...", ... }
```

This avoids re-publishing shards that are already on IPFS, and allows `resolve()` to look up the CID for a known shard_id.

### Publish Flow

```
shard.to_json()
  → canonical JSON bytes
  → POST /api/v0/add (multipart form, Content-Type: application/octet-stream)
  → response: { "Hash": "QmXyz...", "Size": "1234" }
  → store CID in cid_map.json
  → optionally pin
  → return ShardLocation(shard_id, cid, local=True, pinned=True)
```

For sealed/encrypted shards, the process is the same but the payload is the serialized sealed/encrypted envelope (JSON with opaque `sealed_payload` or `encrypted_payload` bytes, base64-encoded).

### Resolve Flow

```
resolve(shard_id):
  1. Check local ShardStore → return if found
  2. Check cid_map.json for known CID
  3. If CID known: POST /api/v0/cat?arg={cid} → deserialize → verify shard_id matches → cache in local store → return
  4. If CID unknown: return None (caller may try other discovery)
```

### Error Handling

- Kubo not running → `is_available()` returns False, all operations raise `NetworkUnavailable`
- CID not found on network → return None (not an error — shard may not be published)
- Timeout → raise `NetworkTimeout`
- Corrupted data (shard_id mismatch after fetch) → raise `IntegrityError`, do not cache

```python
class NetworkUnavailable(Exception): ...
class NetworkTimeout(Exception): ...
class IntegrityError(Exception): ...
```

## ShardStore Integration

ShardStore gets an optional `resolver` parameter. When set, `get()` falls back to the network on local miss.

```python
class ShardStore:
    def __init__(self, root: Path, resolver: NetworkResolver | None = None):
        self._resolver = resolver
        ...

    def get(self, shard_id: str) -> MemoryShard | None:
        # L1: local file
        shard = self._get_local(shard_id)
        if shard:
            return shard

        # L2: network fallback
        if self._resolver:
            shard = self._resolver.resolve(shard_id)
            if shard:
                self.put(shard)  # cache locally
                return shard

        return None
```

Similar pattern for `get_sealed()` and `get_encrypted()`.

**Important**: `put()` does NOT auto-publish. Publishing is explicit via `resolver.publish()`. This keeps the local-first model — you choose what goes to the network.

## Manifest Publishing

Manifests solve the discovery problem: "how does a consumer know which shard IDs to ask for?"

A coordinator publishes a manifest listing available shards, pins it, and shares the manifest CID via a known endpoint (API, DNS TXT, IPNS, etc.).

```python
# Coordinator side (e.g., Frio orchestrator)
manifest = ShardManifest(
    manifest_id="",  # computed on publish
    scope="frio:jobs:active",
    entries=[resolver.publish(job_shard) for job_shard in active_jobs],
    published_at=now_iso(),
    publisher_id="nl1",
)
manifest_cid = resolver.publish_manifest(manifest)
# Share manifest_cid via API endpoint, IPNS, etc.

# Consumer side (e.g., Frio extension, another node)
manifest = resolver.resolve_manifest(manifest_cid)
for entry in manifest.entries:
    shard = resolver.resolve_by_cid(entry.cid)
    # process shard...
```

## Testing Strategy

### Unit Tests (no IPFS needed)

- CID map read/write
- Shard serialization round-trip (to_json → publish payload → from_json)
- ShardStore fallback logic (mock resolver)
- Manifest serialization
- Error cases (integrity mismatch, missing CID)

### Integration Tests (requires local Kubo)

- Publish plaintext shard → resolve by CID → verify content
- Publish sealed shard → resolve → verify opaque payload intact
- Pin/unpin lifecycle
- Manifest publish → resolve → iterate entries
- `is_available()` with running/stopped Kubo
- ShardStore with resolver: put locally → get remotely from another store instance

Mark integration tests with `@pytest.mark.ipfs` so they can be skipped in CI without a Kubo node.

## Out of Scope (for this spec)

- **IPNS**: Mutable names for manifest CIDs. Useful but adds complexity. Can be a follow-up.
- **Peer discovery / DHT bootstrapping**: Handled by Kubo itself when nodes are configured with bootstrap peers.
- **Replication policies**: Which shards to replicate where. Project-specific logic (Frio decides, not spiritwriter).
- **Pubsub**: Real-time shard update notifications. Future feature.
- **Non-IPFS backends**: The Protocol allows them but we only build IPFSBackend now.

## Implementation Order

1. `spiritwriter/fabric/network.py` — Protocol, types (ShardLocation, ShardManifest), exceptions
2. `spiritwriter/fabric/backends/ipfs.py` — IPFSBackend (Kubo HTTP client)
3. CID map persistence in ShardStore directory
4. ShardStore integration (optional resolver parameter, fallback in get/get_sealed/get_encrypted)
5. Manifest publish/resolve
6. Tests (unit first, integration with local Kubo)
7. Update `pyproject.toml` with `[network]` optional extra
8. Update CLAUDE.md and skills/shards/SKILL.md to document the network layer
