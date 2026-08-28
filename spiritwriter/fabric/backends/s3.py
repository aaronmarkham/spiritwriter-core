"""S3 backend — Amazon S3 object store client for shard distribution.

An implementation of the NetworkResolver protocol backed by an S3 bucket,
the S3 analog of the IPFS/Kubo backend. Shards persist as objects in a
bucket instead of on the local disk or IPFS. Intended for AWS-hosted
runtimes that already have S3 access and don't want to run a Kubo node.

Storage layout mirrors ShardStore's git-object key scheme, under a
configurable bucket and optional key prefix::

    {prefix}/shards/{shard_id[:2]}/{shard_id[2:]}.json          # plaintext
    {prefix}/shards/{shard_id[:2]}/{shard_id[2:]}.enc.json      # encrypted
    {prefix}/shards/{shard_id[:2]}/{shard_id[2:]}.sealed.json   # sealed
    {prefix}/manifests/{manifest_id}.json                       # manifests

CID mapping
    The NetworkResolver protocol is CID-centric (it was designed for IPFS,
    where a CID is the content address). S3 has no CID; instead the object
    key *is* the address. This backend therefore maps the protocol's ``cid``
    slot directly to the S3 object key. The mapping is deterministic — the
    key is derived from the shard_id and the layout above — so, unlike the
    IPFS backend, this backend needs no persisted ``cid_map.json``:
    ``resolve(shard_id)`` recomputes the key and does a ``get_object``.
    ``resolve_by_cid(cid)`` treats ``cid`` as the key and returns raw bytes.

Requires: boto3 (optional dependency)
    pip install 'spiritwriter[s3]'
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

try:
    import boto3
    HAS_BOTO3 = True
except ImportError:
    HAS_BOTO3 = False

from spiritwriter.fabric.crypto import EncryptedShard
from spiritwriter.fabric.network import (
    IntegrityError,
    NetworkUnavailable,
    ShardLocation,
    ShardManifest,
)
from spiritwriter.fabric.shard import MemoryShard

logger = logging.getLogger(__name__)

# S3 error codes that mean "the object/bucket isn't there" rather than a
# transport failure. head_object returns "404"; get_object returns
# "NoSuchKey"; a missing bucket surfaces as "NoSuchBucket"/"404".
_NOT_FOUND_CODES = frozenset({"NoSuchKey", "NoSuchBucket", "404", "NotFound"})


def _require_boto3() -> None:
    if not HAS_BOTO3:
        raise ImportError(
            "S3 backend requires boto3. "
            "Install with: pip install 'spiritwriter[s3]'"
        )


@dataclass
class S3Config:
    """Configuration for the S3 backend."""
    bucket: str = ""
    prefix: str = ""              # optional key namespace, e.g. "spiritwriter"
    region: str | None = None     # AWS region (else boto3 resolves it)
    endpoint_url: str | None = None  # override for S3-compatible stores / tests

    @classmethod
    def from_env(cls) -> S3Config:
        """Build config from environment variables.

        Useful for container deployments where the bucket and region come
        from the task/pod environment.

        Env vars:
            SPIRITWRITER_S3_BUCKET    — target bucket (required)
            SPIRITWRITER_S3_PREFIX    — key prefix (default: "")
            SPIRITWRITER_S3_REGION    — AWS region (default: boto3 default)
            SPIRITWRITER_S3_ENDPOINT  — endpoint URL override (default: none)
        """
        return cls(
            bucket=os.environ.get("SPIRITWRITER_S3_BUCKET", ""),
            prefix=os.environ.get("SPIRITWRITER_S3_PREFIX", ""),
            region=os.environ.get("SPIRITWRITER_S3_REGION") or None,
            endpoint_url=os.environ.get("SPIRITWRITER_S3_ENDPOINT") or None,
        )


class S3Backend:
    """NetworkResolver implementation backed by an Amazon S3 bucket.

    Publishes shards as objects in a bucket and resolves them by shard_id.
    Object keys mirror ShardStore's git-object layout, so a bucket can be
    browsed with the same mental model as a local store.

    Unlike the IPFS backend, keys are deterministic functions of the
    shard_id, so no local ``cid_map`` is maintained — resolution recomputes
    the key. The protocol's ``cid`` is the S3 object key (see module
    docstring).

    Testability: pass an explicit boto3-compatible ``client`` to inject a
    stub/mock; otherwise a client is constructed from the config. boto3 is
    an optional dependency, so the core library stays dependency-light.
    """

    def __init__(
        self,
        bucket: str | None = None,
        *,
        prefix: str = "",
        region: str | None = None,
        endpoint_url: str | None = None,
        client: Any = None,
        config: S3Config | None = None,
    ):
        if config is not None:
            self._config = config
        else:
            self._config = S3Config(
                bucket=bucket or "",
                prefix=prefix,
                region=region,
                endpoint_url=endpoint_url,
            )

        if not self._config.bucket:
            raise ValueError("S3Backend requires a bucket (pass bucket= or config=)")

        if client is not None:
            self._client = client
        else:
            _require_boto3()
            self._client = boto3.client(
                "s3",
                region_name=self._config.region,
                endpoint_url=self._config.endpoint_url,
            )

        self._bucket = self._config.bucket
        self._prefix = self._config.prefix.strip("/")

    # === Key Layout ===

    def _key(self, shard_id: str, suffix: str = ".json") -> str:
        """Git-style object key: [prefix/]shards/ab/cd1234...json"""
        rel = f"shards/{shard_id[:2]}/{shard_id[2:]}{suffix}"
        return f"{self._prefix}/{rel}" if self._prefix else rel

    def _manifest_key(self, manifest_id: str) -> str:
        rel = f"manifests/{manifest_id}.json"
        return f"{self._prefix}/{rel}" if self._prefix else rel

    def key_for(self, shard_id: str) -> str:
        """Public helper: the S3 key (== cid) for a plaintext shard_id."""
        return self._key(shard_id)

    # === S3 Helpers ===

    @staticmethod
    def _error_code(exc: Exception) -> str | None:
        """Extract the S3 error code from a botocore ClientError (or lookalike)."""
        code = getattr(exc, "response", None)
        if isinstance(code, dict):
            return code.get("Error", {}).get("Code")
        return None

    def _is_not_found(self, exc: Exception) -> bool:
        """True if the exception means the object/bucket doesn't exist."""
        # boto3 clients expose typed exceptions, e.g. client.exceptions.NoSuchKey
        no_such_key = getattr(getattr(self._client, "exceptions", None), "NoSuchKey", None)
        if no_such_key is not None and isinstance(exc, no_such_key):
            return True
        if self._error_code(exc) in _NOT_FOUND_CODES:
            return True
        return type(exc).__name__ in _NOT_FOUND_CODES

    def _put_bytes(self, key: str, data: bytes) -> None:
        """Write bytes to a key. Wraps transport errors as NetworkUnavailable."""
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType="application/json",
            )
        except Exception as e:  # noqa: BLE001 — normalize to protocol error
            raise NetworkUnavailable(f"S3 put_object failed for {key}: {e}") from e

    def _get_bytes(self, key: str) -> bytes | None:
        """Read bytes from a key. Returns None if absent.

        Raises NetworkUnavailable on transport failures.
        """
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except Exception as e:  # noqa: BLE001 — inspect, then normalize
            if self._is_not_found(e):
                return None
            raise NetworkUnavailable(f"S3 get_object failed for {key}: {e}") from e

    # === NetworkResolver Implementation ===

    def publish(self, shard: MemoryShard) -> ShardLocation:
        """Publish a plaintext shard to S3. Idempotent (content-addressed)."""
        shard_id = shard.shard_id
        key = self._key(shard_id)
        self._put_bytes(key, shard.to_json().encode("utf-8"))
        # cid == the S3 object key; pinned is meaningful in S3's durability
        # model (objects persist until deleted), so we report pinned=True.
        return ShardLocation(shard_id=shard_id, cid=key, local=True, pinned=True)

    def publish_sealed(self, sealed: Any) -> ShardLocation:
        """Publish a sealed shard to S3. Object bytes are opaque to S3."""
        shard_id = sealed.shard_id
        key = self._key(shard_id, ".sealed.json")
        self._put_bytes(key, sealed.to_json().encode("utf-8"))
        return ShardLocation(shard_id=shard_id, cid=key, local=True, pinned=True)

    def publish_encrypted(self, encrypted: EncryptedShard) -> ShardLocation:
        """Publish an encrypted shard to S3."""
        shard_id = encrypted.shard_id
        key = self._key(shard_id, ".enc.json")
        self._put_bytes(key, encrypted.to_json().encode("utf-8"))
        return ShardLocation(shard_id=shard_id, cid=key, local=True, pinned=True)

    def resolve(self, shard_id: str) -> MemoryShard | None:
        """Fetch a plaintext shard from S3 by shard_id. None if absent."""
        try:
            data = self._get_bytes(self._key(shard_id))
        except NetworkUnavailable:
            return None
        if data is None:
            return None

        shard = MemoryShard.from_json(data.decode("utf-8"))
        if shard.shard_id != shard_id:
            raise IntegrityError(
                f"Shard ID mismatch: expected {shard_id}, got {shard.shard_id}"
            )
        return shard

    def resolve_sealed(self, shard_id: str) -> Any:
        """Fetch a sealed shard from S3. None if absent."""
        try:
            data = self._get_bytes(self._key(shard_id, ".sealed.json"))
        except NetworkUnavailable:
            return None
        if data is None:
            return None

        from spiritwriter.fabric.sealed import SealedShard
        sealed = SealedShard.from_json(data.decode("utf-8"))
        if sealed.shard_id != shard_id:
            raise IntegrityError(
                f"Sealed shard ID mismatch: expected {shard_id}, got {sealed.shard_id}"
            )
        return sealed

    def resolve_encrypted(self, shard_id: str) -> EncryptedShard | None:
        """Fetch an encrypted shard from S3. None if absent."""
        try:
            data = self._get_bytes(self._key(shard_id, ".enc.json"))
        except NetworkUnavailable:
            return None
        if data is None:
            return None

        encrypted = EncryptedShard.from_json(data.decode("utf-8"))
        if encrypted.shard_id != shard_id:
            raise IntegrityError(
                f"Encrypted shard ID mismatch: expected {shard_id}, got {encrypted.shard_id}"
            )
        return encrypted

    def resolve_by_cid(self, cid: str) -> bytes:
        """Fetch raw bytes by CID. For S3, ``cid`` is the object key.

        Caller handles deserialization. Raises NetworkUnavailable if the
        key does not exist or S3 is unreachable.
        """
        data = self._get_bytes(cid)
        if data is None:
            raise NetworkUnavailable(f"No S3 object at key {cid}")
        return data

    def pin(self, cid: str) -> bool:
        """No-op pin.

        S3 has no pinning concept: objects persist until explicitly deleted
        (durability is the bucket's job, not a per-object pin). The method
        exists to satisfy the NetworkResolver protocol; it verifies nothing
        and simply reports success, matching the "already durable" semantics
        of an S3 object.
        """
        return True

    def unpin(self, cid: str) -> bool:
        """No-op unpin.

        Symmetric with :meth:`pin`. Notably this does NOT delete the object —
        unpinning on IPFS only marks a CID as garbage-collectable, and the
        S3 analog of that (removing durability protection) is a no-op. Use an
        explicit S3 delete/lifecycle rule to actually remove objects.
        """
        return True

    def publish_manifest(self, manifest: ShardManifest) -> str:
        """Publish a manifest to S3. Returns the manifest's S3 key (its cid)."""
        key = self._manifest_key(manifest.manifest_id)
        self._put_bytes(key, manifest.to_json().encode("utf-8"))
        return key

    def resolve_manifest(self, cid: str) -> ShardManifest | None:
        """Fetch and parse a manifest by CID (S3 key). None if absent."""
        try:
            data = self._get_bytes(cid)
        except NetworkUnavailable:
            return None
        if data is None:
            return None
        return ShardManifest.from_json(data.decode("utf-8"))

    def is_available(self) -> bool:
        """Check the bucket is reachable via a lightweight head_bucket.

        Returns False on any failure (missing bucket, no credentials,
        network error) rather than raising.
        """
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return True
        except Exception:  # noqa: BLE001 — availability probe never raises
            return False
