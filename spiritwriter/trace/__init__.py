"""Spiritwriter trace & memory shard system.

Content-addressed memory shards with DHT-ready distribution,
provenance tracking, and scoped entitlements.
"""

from spiritwriter.trace.shard import MemoryShard, ShardAtom, ShardRef
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.emitter import TraceEmitter, verify_chain
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
from spiritwriter.trace.studio_job import (
    StudioJobSpec, PackagedJob, package_job,
)
from spiritwriter.trace.studio_runner import (
    StudioRunnerError, JobContext, BudgetTracker,
    parse_job_block, hydrate_job, create_result_shard,
)
from spiritwriter.trace.network import (
    NetworkResolver, ShardLocation, ShardManifest,
    NetworkUnavailable, NetworkTimeout, IntegrityError,
    SwarmMismatchError,
)
from spiritwriter.trace.visualize import render_trace
from spiritwriter.trace.canonicalize import (
    EntitySenseSig,
    ResolutionTier,
    ResolutionResult,
    CanonicalSchema,
    CanonicalRegistry,
    canonicalize_batch,
    normalize_name,
    fuzzy_score,
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
    "StudioJobSpec",
    "PackagedJob",
    "package_job",
    "StudioRunnerError",
    "JobContext",
    "BudgetTracker",
    "parse_job_block",
    "hydrate_job",
    "create_result_shard",
    "NetworkResolver",
    "ShardLocation",
    "ShardManifest",
    "NetworkUnavailable",
    "NetworkTimeout",
    "IntegrityError",
    "SwarmMismatchError",
    "EntitySenseSig",
    "ResolutionTier",
    "ResolutionResult",
    "CanonicalSchema",
    "CanonicalRegistry",
    "canonicalize_batch",
    "normalize_name",
    "fuzzy_score",
    "verify_chain",
    "render_trace",
]
