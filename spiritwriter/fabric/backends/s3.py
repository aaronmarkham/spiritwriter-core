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

# S3 error codes that mean a specific OBJECT isn't there — a genuine
# "not found" that a resolve* may legitimately turn into None. get_object
# returns "NoSuchKey"; head_object returns "404"/"NotFound". These must NOT
# include bucket-level errors (see below): a missing OBJECT is a normal empty
# result, but a missing BUCKET is a misconfiguration that must fail loudly.
_OBJECT_NOT_FOUND_CODES = frozenset({"NoSuchKey", "404", "NotFound"})

# S3 error codes that mean the BUCKET is missing or misconfigured — a
# configuration error, never a "not found" result. A typo'd/absent
# SPIRITWRITER_S3_BUCKET surfaces here; if we treated it as "not found" the
# whole store would read as permanently empty instead of erroring.
_CONFIG_ERROR_CODES = frozenset({"NoSuchBucket", "InvalidBucketName"})


class S3ConfigurationError(Exception):
    """Raised when the S3 bucket is missing or misconfigured.

    Distinct from :class:`~spiritwriter.fabric.network.NetworkUnavailable`
    (a transient transport failure) and from an object-not-found (which
    returns ``None``). A misconfigured bucket is a permanent, operator-fixable
    error — it must fail loudly rather than masquerade as an empty store.
    """


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

    def _join(self, rel: str) -> str:
        """Prepend the (optional) key prefix to a relative key."""
        return f"{self._prefix}/{rel}" if self._prefix else rel

    def _key(self, shard_id: str, suffix: str = ".json") -> str:
        """Git-style object key: [prefix/]shards/ab/cd1234...json"""
        return self._join(f"shards/{shard_id[:2]}/{shard_id[2:]}{suffix}")

    def _manifest_key(self, manifest_id: str) -> str:
        return self._join(f"manifests/{manifest_id}.json")

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

    def _is_object_not_found(self, exc: Exception) -> bool:
        """True if the exception means a specific OBJECT doesn't exist.

        This is the only condition a resolve* may turn into ``None``. A
        missing BUCKET is deliberately excluded (see :meth:`_is_config_error`).
        """
        # boto3 clients expose typed exceptions, e.g. client.exceptions.NoSuchKey
        no_such_key = getattr(getattr(self._client, "exceptions", None), "NoSuchKey", None)
        if no_such_key is not None and isinstance(exc, no_such_key):
            return True
        if self._error_code(exc) in _OBJECT_NOT_FOUND_CODES:
            return True
        return type(exc).__name__ in _OBJECT_NOT_FOUND_CODES

    def _is_config_error(self, exc: Exception) -> bool:
        """True if the exception means the BUCKET is missing/misconfigured."""
        if self._error_code(exc) in _CONFIG_ERROR_CODES:
            return True
        return type(exc).__name__ in _CONFIG_ERROR_CODES

    def _raise_normalized(self, exc: Exception, op: str, key: str) -> None:
        """Re-raise an S3 exception as the right protocol-level error.

        Config errors (missing/misconfigured bucket) become a loud
        :class:`S3ConfigurationError`; everything else that isn't an
        object-not-found becomes :class:`NetworkUnavailable`.
        """
        if self._is_config_error(exc):
            raise S3ConfigurationError(
                f"S3 bucket {self._bucket!r} is missing or misconfigured "
                f"({op} {key}): {exc}"
            ) from exc
        raise NetworkUnavailable(f"S3 {op} failed for {key}: {exc}") from exc

    def _put_bytes(self, key: str, data: bytes) -> None:
        """Write bytes to a key.

        Wraps a missing/misconfigured bucket as S3ConfigurationError and any
        other transport error as NetworkUnavailable — a publish never fails
        silently.
        """
        try:
            self._client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=data,
                ContentType="application/json",
            )
        except Exception as e:  # noqa: BLE001 — normalize to protocol error
            self._raise_normalized(e, "put_object", key)

    def _get_bytes(self, key: str) -> bytes | None:
        """Read bytes from a key. Returns None ONLY if the object is absent.

        A genuine object-not-found (NoSuchKey / 404) returns None. A missing
        or misconfigured bucket raises S3ConfigurationError; any other error
        (AccessDenied, throttling, connection reset, ...) raises
        NetworkUnavailable. Callers on the resolve* path must let these
        propagate — returning None on an error would report a partial/empty
        result as a success (see resolve* below).
        """
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].read()
        except Exception as e:  # noqa: BLE001 — inspect, then normalize
            if self._is_object_not_found(e):
                return None
            self._raise_normalized(e, "get_object", key)
            return None  # unreachable — _raise_normalized always raises

    # === Shared publish/resolve helpers ===

    def _publish(self, shard_id: str, suffix: str, json_bytes: bytes) -> ShardLocation:
        """Write a serialized shard to its content-addressed key.

        Single code path for publish / publish_sealed / publish_encrypted —
        they differ only by key suffix and the object's serialization.

        ``local=False``: an S3 object is REMOTE (an L2 store), so
        ``ShardLocation.local`` — documented in network.py as "True if in
        local ShardStore" — must be False, or a local-first resolver checking
        ``loc.local`` would wrongly skip the network fallback. This
        INTENTIONALLY diverges from ipfs.py, which sets ``local=True`` here
        and has the same bug (tracked for IPFS-parity follow-up).

        ``pinned=True``: meaningful in S3's durability model (objects persist
        until explicitly deleted).
        """
        key = self._key(shard_id, suffix)
        self._put_bytes(key, json_bytes)
        return ShardLocation(shard_id=shard_id, cid=key, local=False, pinned=True)

    def _resolve_typed(self, shard_id: str, suffix: str, cls: Any, label: str) -> Any:
        """Fetch + parse + integrity-check a typed shard by shard_id.

        Single code path for resolve / resolve_sealed / resolve_encrypted.
        Returns None ONLY on a genuine object-not-found; every other error
        (S3ConfigurationError, NetworkUnavailable, ...) PROPAGATES from
        ``_get_bytes`` — a resolve must never mask a config/transport failure
        as "shard missing", because a caller (e.g. an async Lambda worker)
        would then report success on empty/partial data.
        """
        data = self._get_bytes(self._key(shard_id, suffix))
        if data is None:
            return None
        obj = cls.from_json(data.decode("utf-8"))
        if obj.shard_id != shard_id:
            raise IntegrityError(
                f"{label} mismatch: expected {shard_id}, got {obj.shard_id}"
            )
        return obj

    # === NetworkResolver Implementation ===

    def publish(self, shard: MemoryShard) -> ShardLocation:
        """Publish a plaintext shard to S3. Idempotent (content-addressed)."""
        return self._publish(shard.shard_id, ".json", shard.to_json().encode("utf-8"))

    def publish_sealed(self, sealed: Any) -> ShardLocation:
        """Publish a sealed shard to S3. Object bytes are opaque to S3."""
        return self._publish(
            sealed.shard_id, ".sealed.json", sealed.to_json().encode("utf-8")
        )

    def publish_encrypted(self, encrypted: EncryptedShard) -> ShardLocation:
        """Publish an encrypted shard to S3."""
        return self._publish(
            encrypted.shard_id, ".enc.json", encrypted.to_json().encode("utf-8")
        )

    def resolve(self, shard_id: str) -> MemoryShard | None:
        """Fetch a plaintext shard from S3 by shard_id. None only if absent.

        A config/transport error propagates (does NOT return None).
        """
        return self._resolve_typed(shard_id, ".json", MemoryShard, "Shard ID")

    def resolve_sealed(self, shard_id: str) -> Any:
        """Fetch a sealed shard from S3. None only if absent."""
        from spiritwriter.fabric.sealed import SealedShard
        return self._resolve_typed(
            shard_id, ".sealed.json", SealedShard, "Sealed shard ID"
        )

    def resolve_encrypted(self, shard_id: str) -> EncryptedShard | None:
        """Fetch an encrypted shard from S3. None only if absent."""
        return self._resolve_typed(
            shard_id, ".enc.json", EncryptedShard, "Encrypted shard ID"
        )

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
        """Fetch and parse a manifest by CID (S3 key). None only if absent.

        A config/transport error propagates (does NOT return None), matching
        resolve*. After parsing, the manifest's content address is verified
        against the requested key and an IntegrityError is raised on mismatch
        — mirroring resolve()'s shard_id check. This is a provenance library
        and the manifest feeds the receipt/lineage path, so an unverified
        manifest is exactly where the integrity guarantee must not have a hole.
        """
        data = self._get_bytes(cid)
        if data is None:
            return None
        manifest = ShardManifest.from_json(data.decode("utf-8"))
        # The manifest key embeds its content address; recompute it from the
        # parsed manifest_id and confirm it matches the requested key.
        expected_key = self._manifest_key(manifest.manifest_id)
        if expected_key != cid:
            raise IntegrityError(
                f"Manifest ID mismatch: key {cid} does not match "
                f"content address {manifest.manifest_id} (expected key {expected_key})"
            )
        return manifest

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
