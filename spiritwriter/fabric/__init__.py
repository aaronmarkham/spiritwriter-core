"""spiritwriter.fabric — The foundational infrastructure layer.

Storage, encryption, distribution, access control, and provenance —
the "fabric" through which all agent memory flows.

Content-addressed memory shards with DHT-ready distribution,
provenance tracking, and scoped entitlements.
"""

from spiritwriter.fabric.shard import MemoryShard, ShardAtom, ShardRef
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.emitter import TraceEmitter, verify_chain
from spiritwriter.fabric.crypto import (
    EncryptedShard, DecryptionError,
    generate_job_key, encrypt_shard, decrypt_shard,
    serialize_key, deserialize_key,
)
from spiritwriter.fabric.entitlement import (
    EntitlementToken, Capability,
    create_entitlement, validate_capability, validate_scope,
    is_expired, get_shard_key, serialize_token, deserialize_token,
)
from spiritwriter.fabric.jobs import (
    JobSpec, PackagedJob, package_job,
)
from spiritwriter.fabric.runner import (
    JobRunnerError, JobContext, BudgetTracker,
    parse_job_block, hydrate_job, create_result_shard,
)
from spiritwriter.fabric.network import (
    NetworkResolver, ShardLocation, ShardManifest,
    NetworkUnavailable, NetworkTimeout, IntegrityError,
    SwarmMismatchError,
)
from spiritwriter.fabric.visualize import render_trace
from spiritwriter.fabric.canonicalize import (
    EntitySenseSig,
    ResolutionTier,
    ResolutionResult,
    CanonicalSchema,
    CanonicalRegistry,
    canonicalize_batch,
    normalize_name,
    normalize_date,
    age_to_bucket,
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
    "JobSpec",
    "PackagedJob",
    "package_job",
    "JobRunnerError",
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
    "normalize_date",
    "age_to_bucket",
    "fuzzy_score",
    "verify_chain",
    "render_trace",
]
