"""Spiritwriter trace & memory shard system.

Content-addressed memory shards with DHT-ready distribution,
provenance tracking, and scoped entitlements.
"""

from spiritwriter.trace.shard import MemoryShard, ShardAtom, ShardRef
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.emitter import TraceEmitter
from spiritwriter.trace.crypto import (
    EncryptedShard, DecryptionError,
    generate_job_key, encrypt_shard, decrypt_shard,
    serialize_key, deserialize_key,
)
from spiritwriter.trace.entitlement import (
    EntitlementToken, Capability,
    create_entitlement, validate_capability, validate_scope,
    is_expired, get_shard_key, serialize_token, deserialize_token,
)

__all__ = [
    "MemoryShard",
    "ShardAtom",
    "ShardRef",
    "ShardStore",
    "TraceEmitter",
    "EncryptedShard",
    "DecryptionError",
    "generate_job_key",
    "encrypt_shard",
    "decrypt_shard",
    "serialize_key",
    "deserialize_key",
    "EntitlementToken",
    "Capability",
    "create_entitlement",
    "validate_capability",
    "validate_scope",
    "is_expired",
    "get_shard_key",
    "serialize_token",
    "deserialize_token",
]
