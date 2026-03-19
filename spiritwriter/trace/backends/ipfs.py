"""IPFS backend — Kubo HTTP API client for shard distribution.

First implementation of the NetworkResolver protocol.
Talks to a local Kubo node via its REST API (default http://127.0.0.1:5001).

Requires: requests (optional dependency)
    pip install 'spiritwriter-core[network]'
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from spiritwriter.trace.crypto import EncryptedShard
from spiritwriter.trace.network import (
    IntegrityError,
    NetworkTimeout,
    NetworkUnavailable,
    ShardLocation,
    ShardManifest,
)
from spiritwriter.trace.shard import MemoryShard


def _require_requests() -> None:
    if not HAS_REQUESTS:
        raise ImportError(
            "IPFS backend requires requests. "
            "Install with: pip install 'spiritwriter-core[network]'"
        )


@dataclass
class IPFSConfig:
    """Configuration for the Kubo HTTP API."""
    api_url: str = "http://127.0.0.1:5001"
    gateway_url: str = "http://127.0.0.1:8080"
    timeout_seconds: int = 30
    pin_by_default: bool = True


class IPFSBackend:
    """NetworkResolver implementation using Kubo's HTTP API.

    Publishes shards to IPFS and resolves them by CID.
    Maintains a local cid_map.json mapping shard_id -> CID.
    """

    def __init__(self, store_root: str | Path, config: IPFSConfig | None = None):
        _require_requests()
        self._config = config or IPFSConfig()
        self._store_root = Path(store_root)
        self._cid_map_path = self._store_root / "cid_map.json"
        self._cid_map: dict[str, str] = self._load_cid_map()

    # === CID Map Persistence ===

    def _load_cid_map(self) -> dict[str, str]:
        if self._cid_map_path.exists():
            return json.loads(self._cid_map_path.read_text(encoding="utf-8"))
        return {}

    def _save_cid_map(self) -> None:
        self._cid_map_path.parent.mkdir(parents=True, exist_ok=True)
        self._cid_map_path.write_text(
            json.dumps(self._cid_map, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_cid(self, shard_id: str) -> str | None:
        """Look up a known CID for a shard_id."""
        return self._cid_map.get(shard_id)

    # === Kubo HTTP Helpers ===

    def _api(self, endpoint: str, **kwargs: Any) -> requests.Response:
        """Make a POST request to the Kubo API."""
        url = f"{self._config.api_url}/api/v0/{endpoint}"
        try:
            resp = requests.post(url, timeout=self._config.timeout_seconds, **kwargs)
            resp.raise_for_status()
            return resp
        except requests.ConnectionError as e:
            raise NetworkUnavailable(f"Kubo not reachable at {self._config.api_url}: {e}") from e
        except requests.Timeout as e:
            raise NetworkTimeout(f"Kubo request timed out after {self._config.timeout_seconds}s") from e
        except requests.HTTPError as e:
            raise NetworkUnavailable(f"Kubo API error: {e}") from e

    def _add_bytes(self, data: bytes) -> str:
        """Add raw bytes to IPFS. Returns the CID."""
        resp = self._api("add", files={"file": ("shard.json", data, "application/octet-stream")})
        result = resp.json()
        return result["Hash"]

    def _cat_cid(self, cid: str) -> bytes:
        """Fetch raw bytes from IPFS by CID."""
        resp = self._api("cat", params={"arg": cid})
        return resp.content

    # === NetworkResolver Implementation ===

    def publish(self, shard: MemoryShard) -> ShardLocation:
        """Publish a plaintext shard to IPFS."""
        shard_id = shard.shard_id
        # Skip if already published
        existing_cid = self._cid_map.get(shard_id)
        if existing_cid:
            return ShardLocation(shard_id=shard_id, cid=existing_cid, local=True, pinned=True)

        payload = shard.to_json().encode("utf-8")
        cid = self._add_bytes(payload)

        if self._config.pin_by_default:
            self.pin(cid)

        self._cid_map[shard_id] = cid
        self._save_cid_map()

        return ShardLocation(shard_id=shard_id, cid=cid, local=True, pinned=self._config.pin_by_default)

    def publish_sealed(self, sealed: Any) -> ShardLocation:
        """Publish a sealed shard to IPFS. Network sees opaque bytes."""
        shard_id = sealed.shard_id
        existing_cid = self._cid_map.get(shard_id)
        if existing_cid:
            return ShardLocation(shard_id=shard_id, cid=existing_cid, local=True, pinned=True)

        payload = sealed.to_json().encode("utf-8")
        cid = self._add_bytes(payload)

        if self._config.pin_by_default:
            self.pin(cid)

        # Use a prefixed key so sealed and plaintext don't collide
        map_key = f"sealed:{shard_id}"
        self._cid_map[map_key] = cid
        self._save_cid_map()

        return ShardLocation(shard_id=shard_id, cid=cid, local=True, pinned=self._config.pin_by_default)

    def publish_encrypted(self, encrypted: EncryptedShard) -> ShardLocation:
        """Publish an encrypted shard to IPFS."""
        shard_id = encrypted.shard_id
        existing_cid = self._cid_map.get(f"encrypted:{shard_id}")
        if existing_cid:
            return ShardLocation(shard_id=shard_id, cid=existing_cid, local=True, pinned=True)

        payload = encrypted.to_json().encode("utf-8")
        cid = self._add_bytes(payload)

        if self._config.pin_by_default:
            self.pin(cid)

        map_key = f"encrypted:{shard_id}"
        self._cid_map[map_key] = cid
        self._save_cid_map()

        return ShardLocation(shard_id=shard_id, cid=cid, local=True, pinned=self._config.pin_by_default)

    def resolve(self, shard_id: str) -> MemoryShard | None:
        """Fetch a plaintext shard from IPFS by shard_id."""
        cid = self._cid_map.get(shard_id)
        if not cid:
            return None

        try:
            data = self._cat_cid(cid)
        except (NetworkUnavailable, NetworkTimeout):
            return None

        shard = MemoryShard.from_json(data.decode("utf-8"))
        if shard.shard_id != shard_id:
            raise IntegrityError(
                f"Shard ID mismatch: expected {shard_id}, got {shard.shard_id}"
            )
        return shard

    def resolve_sealed(self, shard_id: str) -> Any:
        """Fetch a sealed shard from IPFS."""
        cid = self._cid_map.get(f"sealed:{shard_id}")
        if not cid:
            return None

        try:
            data = self._cat_cid(cid)
        except (NetworkUnavailable, NetworkTimeout):
            return None

        from spiritwriter.trace.sealed import SealedShard
        sealed = SealedShard.from_json(data.decode("utf-8"))
        if sealed.shard_id != shard_id:
            raise IntegrityError(
                f"Sealed shard ID mismatch: expected {shard_id}, got {sealed.shard_id}"
            )
        return sealed

    def resolve_encrypted(self, shard_id: str) -> EncryptedShard | None:
        """Fetch an encrypted shard from IPFS."""
        cid = self._cid_map.get(f"encrypted:{shard_id}")
        if not cid:
            return None

        try:
            data = self._cat_cid(cid)
        except (NetworkUnavailable, NetworkTimeout):
            return None

        encrypted = EncryptedShard.from_json(data.decode("utf-8"))
        if encrypted.shard_id != shard_id:
            raise IntegrityError(
                f"Encrypted shard ID mismatch: expected {shard_id}, got {encrypted.shard_id}"
            )
        return encrypted

    def resolve_by_cid(self, cid: str) -> bytes:
        """Fetch raw bytes by CID. Caller handles deserialization."""
        return self._cat_cid(cid)

    def pin(self, cid: str) -> bool:
        """Pin a CID on the local IPFS node."""
        try:
            self._api("pin/add", params={"arg": cid})
            return True
        except (NetworkUnavailable, NetworkTimeout):
            return False

    def unpin(self, cid: str) -> bool:
        """Unpin a CID."""
        try:
            self._api("pin/rm", params={"arg": cid})
            return True
        except (NetworkUnavailable, NetworkTimeout):
            return False

    def publish_manifest(self, manifest: ShardManifest) -> str:
        """Publish a manifest to IPFS. Returns the manifest CID."""
        payload = manifest.to_json().encode("utf-8")
        cid = self._add_bytes(payload)

        if self._config.pin_by_default:
            self.pin(cid)

        # Store manifest CID for lookup
        self._cid_map[f"manifest:{manifest.manifest_id}"] = cid
        self._save_cid_map()

        return cid

    def resolve_manifest(self, cid: str) -> ShardManifest | None:
        """Fetch and parse a manifest by CID."""
        try:
            data = self._cat_cid(cid)
        except (NetworkUnavailable, NetworkTimeout):
            return None

        return ShardManifest.from_json(data.decode("utf-8"))

    def is_available(self) -> bool:
        """Check if the Kubo node is reachable."""
        try:
            self._api("id")
            return True
        except (NetworkUnavailable, NetworkTimeout):
            return False
