"""Network resolver — transport-agnostic shard distribution.

Defines the NetworkResolver protocol and supporting types for
publishing/resolving shards over the network (IPFS, etc.).

Local-first: ShardStore is L1, network is L2 fallback.
Opt-in: projects that don't need network resolution ignore this module.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from spiritwriter.fabric.crypto import EncryptedShard
    from spiritwriter.fabric.sealed import SealedShard
    from spiritwriter.fabric.shard import MemoryShard


# === Exceptions ===

class NetworkUnavailable(Exception):
    """Raised when the network backend is unreachable."""


class NetworkTimeout(Exception):
    """Raised when a network operation times out."""


class IntegrityError(Exception):
    """Raised when fetched data fails content-address verification."""


class SwarmMismatchError(Exception):
    """Raised when the IPFS node is not on the expected private swarm."""


# === Data Types ===

@dataclass
class ShardLocation:
    """Where a shard can be found."""
    shard_id: str           # sha256:abcd1234... (hex digest from shard_id)
    cid: str | None = None  # IPFS CID (if published)
    local: bool = False     # True if in local ShardStore
    pinned: bool = False    # True if pinned on IPFS node

    def to_dict(self) -> dict:
        d: dict = {"shard_id": self.shard_id}
        if self.cid is not None:
            d["cid"] = self.cid
        if self.local:
            d["local"] = self.local
        if self.pinned:
            d["pinned"] = self.pinned
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ShardLocation:
        return cls(
            shard_id=d["shard_id"],
            cid=d.get("cid"),
            local=d.get("local", False),
            pinned=d.get("pinned", False),
        )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@dataclass
class ShardManifest:
    """A published index of shard locations.

    Solves the discovery problem: consumers fetch the manifest by CID
    to learn which shard IDs are available, then resolve each entry.
    """
    scope: str                          # e.g., "frio:jobs", "frio:skills"
    entries: list[ShardLocation]
    published_at: str = field(default_factory=_now_iso)
    publisher_id: str = ""              # Node/agent identity

    @property
    def manifest_id(self) -> str:
        """Content address of this manifest."""
        payload = json.dumps(
            {
                "scope": self.scope,
                "entries": [e.to_dict() for e in self.entries],
                "publisher_id": self.publisher_id,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def to_dict(self) -> dict:
        return {
            "manifest_id": self.manifest_id,
            "scope": self.scope,
            "entries": [e.to_dict() for e in self.entries],
            "published_at": self.published_at,
            "publisher_id": self.publisher_id,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> ShardManifest:
        return cls(
            scope=d["scope"],
            entries=[ShardLocation.from_dict(e) for e in d["entries"]],
            published_at=d.get("published_at", _now_iso()),
            publisher_id=d.get("publisher_id", ""),
        )

    @classmethod
    def from_json(cls, raw: str) -> ShardManifest:
        return cls.from_dict(json.loads(raw))


# === Protocol ===

@runtime_checkable
class NetworkResolver(Protocol):
    """Interface for network-backed shard resolution.

    Transport-agnostic: first implementation is IPFS/Kubo,
    but the protocol allows other backends.
    """

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
