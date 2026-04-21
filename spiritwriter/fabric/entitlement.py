"""Entitlement tokens for scoped shard access.

An entitlement grants a sub-agent the keys and permissions to
decrypt specific shards and perform specific actions.
"""

from __future__ import annotations

import fnmatch
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from spiritwriter.fabric.crypto import serialize_key, deserialize_key
from spiritwriter.fabric.shard import _now_iso


class Capability:
    SHARD_READ = "shard:read"
    SHARD_WRITE = "shard:write"
    KB_CREATE = "kb:create"
    KB_PRODUCE = "kb:produce"
    UPLOAD_YOUTUBE = "upload:youtube"
    WEB_SEARCH = "web:search"
    WEB_FETCH = "web:fetch"
    EXEC_RUN = "exec:run"


@dataclass
class EntitlementToken:
    token_id: str
    granted_to: str
    granted_by: str
    shard_keys: dict[str, str]
    scopes: list[str]
    capabilities: list[str]
    secrets: list[str]
    budget_usd: float
    created_at: str
    expires_at: str | None = None
    trace_parent: str | None = None
    constraints: dict = field(default_factory=dict)


def create_entitlement(
    granted_to: str,
    granted_by: str,
    shard_keys: dict[str, bytes],
    scopes: list[str],
    capabilities: list[str],
    secrets: list[str],
    budget_usd: float,
    expires_at: str | None = None,
    constraints: dict | None = None,
) -> EntitlementToken:
    return EntitlementToken(
        token_id=str(uuid.uuid4()),
        granted_to=granted_to,
        granted_by=granted_by,
        shard_keys={sid: serialize_key(k) for sid, k in shard_keys.items()},
        scopes=scopes,
        capabilities=capabilities,
        secrets=secrets,
        budget_usd=budget_usd,
        created_at=_now_iso(),
        expires_at=expires_at,
        constraints=constraints or {},
    )


def validate_capability(token: EntitlementToken, action: str) -> bool:
    return action in token.capabilities


def validate_scope(token: EntitlementToken, scope: str) -> bool:
    return any(fnmatch.fnmatch(scope, pattern) for pattern in token.scopes)


def validate_budget(token: EntitlementToken, spent: float) -> bool:
    return spent <= token.budget_usd


def is_expired(token: EntitlementToken) -> bool:
    if token.expires_at is None:
        return False
    expires = datetime.fromisoformat(token.expires_at)
    return datetime.now(timezone.utc) >= expires


def get_shard_key(token: EntitlementToken, shard_id: str) -> bytes:
    if shard_id not in token.shard_keys:
        raise KeyError(f"No entitlement for shard {shard_id}")
    return deserialize_key(token.shard_keys[shard_id])


def serialize_token(token: EntitlementToken) -> str:
    return json.dumps({
        "token_id": token.token_id,
        "granted_to": token.granted_to,
        "granted_by": token.granted_by,
        "shard_keys": token.shard_keys,
        "scopes": token.scopes,
        "capabilities": token.capabilities,
        "secrets": token.secrets,
        "budget_usd": token.budget_usd,
        "created_at": token.created_at,
        "expires_at": token.expires_at,
        "trace_parent": token.trace_parent,
        "constraints": token.constraints,
    }, ensure_ascii=False)


def deserialize_token(s: str) -> EntitlementToken:
    d = json.loads(s)
    return EntitlementToken(**d)
