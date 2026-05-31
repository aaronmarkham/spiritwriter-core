"""IPFS backend — Kubo HTTP API client for shard distribution.

First implementation of the NetworkResolver protocol.
Talks to a local Kubo node via its REST API (default http://127.0.0.1:5001).

By default, requires the Kubo node to be on a private swarm (swarm key
stored in spiritwriter secrets). This prevents accidental publication of
shards to the public IPFS network.

Requires: requests (optional dependency)
    pip install 'spiritwriter-core[network]'
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

from spiritwriter.fabric.crypto import EncryptedShard
from spiritwriter.fabric.network import (
    IntegrityError,
    NetworkTimeout,
    NetworkUnavailable,
    ShardLocation,
    ShardManifest,
    SwarmMismatchError,
)
from spiritwriter.fabric.shard import MemoryShard

logger = logging.getLogger(__name__)

# Register the IPFS swarm key in spiritwriter secrets
IPFS_SWARM_KEY_NAME = "IPFS_SWARM_KEY"

try:
    from spiritwriter.secrets.keychain import register_keys
    register_keys({IPFS_SWARM_KEY_NAME: "IPFS private swarm key (hex-encoded 32 bytes)"})
except ImportError:
    pass  # secrets module not available — swarm key must come from env


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
    require_private_swarm: bool = True  # Refuse to operate on public IPFS

    @classmethod
    def from_env(cls) -> IPFSConfig:
        """Build config from environment variables.

        Useful for Docker/container deployments where the Kubo API
        is at a service hostname (e.g., http://frio-ipfs:5001).

        Env vars:
            IPFS_API_URL          — Kubo API (default: http://127.0.0.1:5001)
            IPFS_GATEWAY_URL      — Read-only gateway (default: http://127.0.0.1:8080)
            IPFS_TIMEOUT          — Timeout in seconds (default: 30)
            IPFS_PIN_BY_DEFAULT   — "0" to disable auto-pin (default: "1")
            IPFS_REQUIRE_PRIVATE_SWARM — "0" to allow public IPFS (default: "1")
        """
        return cls(
            api_url=os.environ.get("IPFS_API_URL", "http://127.0.0.1:5001"),
            gateway_url=os.environ.get("IPFS_GATEWAY_URL", "http://127.0.0.1:8080"),
            timeout_seconds=int(os.environ.get("IPFS_TIMEOUT", "30")),
            pin_by_default=os.environ.get("IPFS_PIN_BY_DEFAULT", "1") != "0",
            require_private_swarm=os.environ.get("IPFS_REQUIRE_PRIVATE_SWARM", "1") != "0",
        )


class IPFSBackend:
    """NetworkResolver implementation using Kubo's HTTP API.

    Publishes shards to IPFS and resolves them by CID.
    Maintains a local cid_map.json mapping shard_id -> CID.

    By default, validates that the Kubo node is on a private swarm
    (swarm key from spiritwriter secrets). Set require_private_swarm=False
    in IPFSConfig to allow public IPFS — but be aware that plaintext
    shards will be readable by anyone who discovers the CID.
    """

    def __init__(self, store_root: str | Path, config: IPFSConfig | None = None):
        _require_requests()
        self._config = config or IPFSConfig()
        self._store_root = Path(store_root)
        self._cid_map_path = self._store_root / "cid_map.json"
        self._cid_map: dict[str, str] = self._load_cid_map()
        self._swarm_verified = False

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

    # === Swarm Verification ===

    def _get_swarm_key(self) -> str | None:
        """Retrieve the expected swarm key from spiritwriter secrets."""
        try:
            from spiritwriter.secrets.keychain import get_api_key
            return get_api_key(IPFS_SWARM_KEY_NAME)
        except ImportError:
            return None

    def _verify_private_swarm(self) -> None:
        """Verify the Kubo node is on our private swarm.

        Checks that:
        1. An IPFS_SWARM_KEY is configured in spiritwriter secrets
        2. The node has no public bootstrap peers (private swarm indicator)

        Raises SwarmMismatchError if the node appears to be on the public network.
        """
        if self._swarm_verified or not self._config.require_private_swarm:
            return

        swarm_key = self._get_swarm_key()
        if not swarm_key:
            raise SwarmMismatchError(
                "require_private_swarm is True but no IPFS_SWARM_KEY found. "
                "Set the swarm key: spiritwriter secrets set IPFS_SWARM_KEY"
            )

        # Check that the node isn't connected to public bootstrap peers.
        # A private-swarm Kubo node with a swarm.key file will refuse
        # connections from peers not sharing the same key, so having
        # zero or only known-private peers is the expected state.
        try:
            resp = self._api_raw("bootstrap/list")
            bootstrap = resp.json().get("Peers") or []
            # Kubo's default public bootstrappers contain "Qm" legacy peer IDs
            # from the public IPFS network. A properly configured private node
            # should have these removed.
            public_bootstrappers = [
                p for p in bootstrap
                if "/dnsaddr/bootstrap.libp2p.io/" in p
                or "/ip4/104." in p  # default Kubo public bootstrap IPs
            ]
            if public_bootstrappers:
                raise SwarmMismatchError(
                    f"Kubo node has {len(public_bootstrappers)} public bootstrap peer(s). "
                    "This looks like a public IPFS node, not a private swarm. "
                    "Remove public bootstrappers or set require_private_swarm=False."
                )
        except (NetworkUnavailable, NetworkTimeout) as e:
            raise SwarmMismatchError(f"Cannot verify swarm: {e}") from e

        self._swarm_verified = True
        logger.info("Private swarm verified — IPFS node is not on public network")

    def _require_swarm(self) -> None:
        """Gate for publish operations — ensures swarm is verified."""
        if self._config.require_private_swarm and not self._swarm_verified:
            self._verify_private_swarm()

    # === Kubo HTTP Helpers ===

    def _api_raw(self, endpoint: str, **kwargs: Any) -> requests.Response:
        """Make a POST request to the Kubo API (no swarm check)."""
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

    def _api(self, endpoint: str, **kwargs: Any) -> requests.Response:
        """Make a POST request to the Kubo API (verifies swarm first)."""
        self._require_swarm()
        return self._api_raw(endpoint, **kwargs)

    def _add_bytes_raw(self, data: bytes) -> str:
        """Add raw bytes to IPFS (no swarm check). Returns the CID."""
        resp = self._api_raw("add", files={"file": ("shard.json", data, "application/octet-stream")})
        result = resp.json()
        return result["Hash"]

    def _add_bytes(self, data: bytes) -> str:
        """Add raw bytes to IPFS (verifies swarm first). Returns the CID."""
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

    def publish_public(self, shard: MemoryShard, *, confirm_public: bool = False) -> ShardLocation:
        """Publish a PLAINTEXT shard to the PUBLIC IPFS network.

        Use this only for intentionally public content. Bypasses the
        private swarm requirement. The shard is published as plaintext and
        will be permanently readable by anyone who discovers the CID.

        A ``MemoryShard`` is always plaintext; encrypted or sealed content
        uses :meth:`publish_encrypted` / :meth:`publish_sealed` instead.
        Because publishing plaintext to public IPFS is irreversible and
        almost always a mistake, callers must opt in explicitly.

        Raises:
            ValueError: if ``confirm_public`` is not set to ``True``.
        """
        if not confirm_public:
            raise ValueError(
                "publish_public() exposes plaintext on the public IPFS network "
                "permanently. Pass confirm_public=True to acknowledge this, or use "
                "publish_encrypted()/publish_sealed() to keep the content private."
            )
        shard_id = shard.shard_id
        existing_cid = self._cid_map.get(f"public:{shard_id}")
        if existing_cid:
            return ShardLocation(shard_id=shard_id, cid=existing_cid, local=True, pinned=True)

        payload = shard.to_json().encode("utf-8")
        cid = self._add_bytes_raw(payload)

        if self._config.pin_by_default:
            try:
                self._api_raw("pin/add", params={"arg": cid})
            except (NetworkUnavailable, NetworkTimeout):
                pass

        self._cid_map[f"public:{shard_id}"] = cid
        self._save_cid_map()

        logger.warning("Shard %s published to PUBLIC IPFS (CID: %s)", shard_id[:16], cid)
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

        from spiritwriter.fabric.sealed import SealedShard
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
        """Check if the Kubo node is reachable and on the correct swarm.

        When require_private_swarm is True, also verifies the node
        is on our private swarm (no public bootstrappers, swarm key set).
        """
        try:
            self._api_raw("id")
        except (NetworkUnavailable, NetworkTimeout):
            return False

        if self._config.require_private_swarm:
            try:
                self._verify_private_swarm()
            except SwarmMismatchError:
                return False

        return True
