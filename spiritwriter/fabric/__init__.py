"""spiritwriter.fabric — The foundational infrastructure layer.

Storage, encryption, distribution, access control, and provenance —
the "fabric" through which all agent memory flows.

Content-addressed memory shards with DHT-ready distribution,
provenance tracking, and scoped entitlements.

Note on chain-verification naming: two different "chains" live in this
package. ``verify_trace_chain`` (alias of ``verify_chain`` from the
emitter module) walks a hash-linked trace event log. ``verify_cap_chain``
walks a signed capability delegation chain. They serve different
purposes; pick the name that matches your chain type. The plain
``verify_chain`` is preserved for back-compat with existing callers.
"""

from spiritwriter.fabric.shard import (
    MemoryShard,
    ShardAtom,
    ShardRef,
    generate_signing_keypair,
    pubkey_thumbprint,
)
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.emitter import (
    TraceEmitter,
    verify_chain,
    verify_chain as verify_trace_chain,
    events_by_cap,
    events_by_signer,
    events_by_role,
    events_under_chain,
)
from spiritwriter.fabric.crypto import (
    EncryptedShard, DecryptionError,
    generate_job_key, encrypt_shard, decrypt_shard,
    serialize_key, deserialize_key,
)
from spiritwriter.fabric.entitlement import (
    EntitlementToken, Capability,
    create_entitlement, validate_capability, validate_scope,
    is_expired, get_shard_key, serialize_token, deserialize_token,
    Caveat, CaveatType, KNOWN_CAVEAT_TYPES, UnknownCaveatError,
    validate_caveat, validate_caveats,
    verify_cap_chain, authorize_chain, issue_delegated,
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
    # Pre-resolution normalization helpers (the registry does NOT
    # auto-apply normalizers; pre-process candidates with these before
    # calling resolve()/upsert()). See docs/entity-resolution.md.
    first_initial,
    strip_punctuation,
    apply_normalizers,
    pipeline,
)

__all__ = [
    "MemoryShard",
    "ShardAtom",
    "ShardRef",
    "generate_signing_keypair",
    "pubkey_thumbprint",
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
    "Caveat",
    "CaveatType",
    "KNOWN_CAVEAT_TYPES",
    "UnknownCaveatError",
    "validate_caveat",
    "validate_caveats",
    "verify_cap_chain",
    "authorize_chain",
    "issue_delegated",
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
    "first_initial",
    "strip_punctuation",
    "apply_normalizers",
    "pipeline",
    "verify_chain",
    "verify_trace_chain",
    "events_by_cap",
    "events_by_signer",
    "events_by_role",
    "events_under_chain",
    "render_trace",
]
