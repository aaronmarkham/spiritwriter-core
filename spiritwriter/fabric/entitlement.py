"""Entitlement tokens for scoped shard access.

An entitlement grants a sub-agent the keys and permissions to
decrypt specific shards and perform specific actions.

Tokens may also carry typed caveats (``expires_at``, ``scope_limit``,
``max_delegation_depth``) and form **delegation chains** via
``parent_cap_id`` + Ed25519 signatures. A leaf token's authority is
the intersection of every caveat in its chain. See ``verify_cap_chain``
and ``authorize_chain``.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from spiritwriter.fabric.crypto import serialize_key, deserialize_key
from spiritwriter.fabric.shard import _canonical_json, _now_iso, pubkey_thumbprint


class Capability:
    SHARD_READ = "shard:read"
    SHARD_WRITE = "shard:write"
    KB_CREATE = "kb:create"
    KB_PRODUCE = "kb:produce"
    UPLOAD_YOUTUBE = "upload:youtube"
    WEB_SEARCH = "web:search"
    WEB_FETCH = "web:fetch"
    EXEC_RUN = "exec:run"


# === Caveats ============================================================
#
# Caveats are typed restrictions attached to a token. They compose by
# intersection across a delegation chain: the leaf's effective authority
# is whatever every cap in its chain permits.
#
# Verifiers MUST fail closed on unknown caveat types — silently ignoring
# an unrecognized restriction would let attackers strip caveats by
# claiming a non-existent type.


class CaveatType:
    """Recognized caveat type names.

    Closed set in v0. New types require coordinated rollout across
    issuers and verifiers — adding a type that older verifiers don't
    know about will cause them to reject otherwise-valid tokens (which
    is the safe failure mode).
    """
    EXPIRES_AT = "expires_at"                  # ISO 8601 timestamp; reject if now >= value
    SCOPE_LIMIT = "scope_limit"                # fnmatch pattern; reject if scope doesn't match
    MAX_DELEGATION_DEPTH = "max_delegation_depth"  # int; reject if levels-below > value


KNOWN_CAVEAT_TYPES = frozenset({
    CaveatType.EXPIRES_AT,
    CaveatType.SCOPE_LIMIT,
    CaveatType.MAX_DELEGATION_DEPTH,
})


class UnknownCaveatError(ValueError):
    """A caveat type the verifier doesn't recognize. Fail closed."""


@dataclass(frozen=True)
class Caveat:
    """A typed restriction attached to a capability.

    `value` is type-specific:
      - expires_at: ISO 8601 string (lexically-comparable, UTC)
      - scope_limit: fnmatch pattern, e.g. "sw:article:run-abc:*"
      - max_delegation_depth: non-negative int
    """
    type: str
    value: Any

    def to_dict(self) -> dict:
        return {"type": self.type, "value": self.value}

    @classmethod
    def from_dict(cls, d: dict) -> Caveat:
        return cls(type=d["type"], value=d["value"])


def _require_z_utc(value: object, *, label: str) -> str:
    """Validate an ISO 8601 timestamp is in canonical Z-suffixed UTC form.

    Lex comparison between e.g. "2099-01-01T00:00:00+00:00" and a
    Z-suffixed "now" string is **wrong** ('+' < 'Z' in ASCII), which
    would silently make a future-dated cap evaluate as expired. We
    require the Z form everywhere to keep the lex-sort invariant safe.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"{label} must be an ISO 8601 string, got {type(value).__name__}"
        )
    if not value.endswith("Z"):
        raise ValueError(
            f"{label} must use trailing 'Z' UTC form for safe lex comparison; "
            f"got {value!r}"
        )
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid ISO 8601 timestamp: {value!r}") from exc
    return value


def validate_caveat(
    caveat: Caveat,
    *,
    scope: str | None = None,
    delegation_depth: int = 0,
    now_iso: str | None = None,
) -> bool:
    """Validate one caveat against a request context.

    Args:
        caveat: the caveat to check.
        scope: scope being requested. ``None`` skips scope_limit checks.
        delegation_depth: number of links *below* this cap in the chain
            (the leaf has 0 below it). Used by max_delegation_depth.
        now_iso: current time as ISO 8601 (must use trailing ``Z`` UTC form).
            Defaults to wall clock, which is always in Z form.

    Returns:
        True if the caveat permits the operation, False otherwise.

    Raises:
        UnknownCaveatError: caveat type is not recognized.
        ValueError: an EXPIRES_AT caveat (or now_iso) is not in canonical
            Z-suffixed UTC form. Fail closed: a non-Z timestamp would
            lex-compare incorrectly and silently mis-evaluate expiry.
    """
    if caveat.type not in KNOWN_CAVEAT_TYPES:
        raise UnknownCaveatError(f"Unknown caveat type: {caveat.type!r}")

    if caveat.type == CaveatType.EXPIRES_AT:
        _require_z_utc(caveat.value, label="expires_at caveat value")
        now = now_iso or _now_iso()
        _require_z_utc(now, label="now_iso")
        return now < caveat.value  # ISO 8601 with Z sorts lexically

    if caveat.type == CaveatType.SCOPE_LIMIT:
        if scope is None:
            return True
        return fnmatch.fnmatch(scope, caveat.value)

    if caveat.type == CaveatType.MAX_DELEGATION_DEPTH:
        return delegation_depth <= caveat.value

    return False  # unreachable; here so future types fail closed if added without handling


def validate_caveats(
    caveats: list[Caveat],
    *,
    scope: str | None = None,
    delegation_depth: int = 0,
    now_iso: str | None = None,
) -> bool:
    """All caveats must pass. Empty list trivially permits."""
    return all(
        validate_caveat(
            c, scope=scope, delegation_depth=delegation_depth, now_iso=now_iso
        )
        for c in caveats
    )


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
    # === Delegation chain + signing (optional; legacy tokens still work) ===
    subject_pubkey: bytes | None = None    # 32-byte Ed25519 pubkey of the holder
    issuer_pubkey: bytes | None = None     # 32-byte Ed25519 pubkey of the granter
    parent_cap_id: str | None = None       # cap_id of the parent in the chain; None = root
    caveats: list[Caveat] = field(default_factory=list)
    signature: str | None = None           # hex Ed25519 over signing_payload()

    # ---- Signing / chain identity ----

    def _signing_dict(self) -> dict:
        """Fields covered by the signature.

        Includes everything that defines the cap's authority and chain
        position. Excludes ``signature`` itself (recursive), the AES
        ``shard_keys`` payload (delegate-specific bookkeeping that may
        be added/rotated post-issuance), and ``trace_parent`` (telemetry
        attachment, not authority).
        """
        d: dict = {
            "token_id": self.token_id,
            "granted_to": self.granted_to,
            "granted_by": self.granted_by,
            "scopes": list(self.scopes),
            "capabilities": list(self.capabilities),
            "secrets": list(self.secrets),
            "budget_usd": self.budget_usd,
            "created_at": self.created_at,
            "caveats": [c.to_dict() for c in self.caveats],
        }
        if self.expires_at is not None:
            d["expires_at"] = self.expires_at
        if self.constraints:
            d["constraints"] = self.constraints
        if self.subject_pubkey is not None:
            d["subject_pubkey"] = self.subject_pubkey.hex()
        if self.issuer_pubkey is not None:
            d["issuer_pubkey"] = self.issuer_pubkey.hex()
        if self.parent_cap_id is not None:
            d["parent_cap_id"] = self.parent_cap_id
        return d

    def signing_payload(self) -> bytes:
        """Canonical bytes covered by the signature."""
        return _canonical_json(self._signing_dict())

    @property
    def cap_id(self) -> str:
        """Content address — sha256 hex of the signing payload.

        Stable across signers (Ed25519 is deterministic, so a given
        signing payload + key yields exactly one signature; cap_id
        binds the issuer claim because issuer_pubkey is in the payload).
        """
        return hashlib.sha256(self.signing_payload()).hexdigest()

    def sign(
        self,
        private_key_bytes: bytes,
        *,
        pubkey_bytes: bytes | None = None,
    ) -> None:
        """Sign this token in place with an Ed25519 private key.

        Sets ``signature``. If ``issuer_pubkey`` was unset, populates it
        from the signing key. If preset, validates that it matches.

        Args:
            private_key_bytes: 32-byte Ed25519 seed.
            pubkey_bytes: optional 32-byte raw pubkey; derived from
                the private key if omitted.
        """
        sk = Ed25519PrivateKey.from_private_bytes(private_key_bytes)
        derived_pk = sk.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        if pubkey_bytes is not None and pubkey_bytes != derived_pk:
            raise ValueError("Provided pubkey does not match private key")
        pk = pubkey_bytes or derived_pk
        if self.issuer_pubkey is None:
            self.issuer_pubkey = pk
        elif self.issuer_pubkey != pk:
            raise ValueError(
                f"issuer_pubkey mismatch: token claims "
                f"{pubkey_thumbprint(self.issuer_pubkey)[:12]}…, "
                f"signing key is {pubkey_thumbprint(pk)[:12]}…"
            )
        self.signature = sk.sign(self.signing_payload()).hex()

    def verify(self) -> bool:
        """Verify this token's own signature against ``issuer_pubkey``.

        Does NOT walk the chain — use :func:`verify_cap_chain` for that.
        Returns True on success.

        Raises:
            ValueError: token is unsigned or has no issuer_pubkey.
            cryptography.exceptions.InvalidSignature: signature is bad.
        """
        if self.signature is None:
            raise ValueError("Token has no signature to verify")
        if self.issuer_pubkey is None:
            raise ValueError("Token has no issuer_pubkey to verify against")
        pk = Ed25519PublicKey.from_public_bytes(self.issuer_pubkey)
        pk.verify(bytes.fromhex(self.signature), self.signing_payload())
        return True


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
    """JSON-serialize a token. Bytes fields (pubkeys) become hex strings."""
    d: dict = {
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
    }
    if token.subject_pubkey is not None:
        d["subject_pubkey"] = token.subject_pubkey.hex()
    if token.issuer_pubkey is not None:
        d["issuer_pubkey"] = token.issuer_pubkey.hex()
    if token.parent_cap_id is not None:
        d["parent_cap_id"] = token.parent_cap_id
    if token.caveats:
        d["caveats"] = [c.to_dict() for c in token.caveats]
    if token.signature is not None:
        d["signature"] = token.signature
    return json.dumps(d, ensure_ascii=False)


def deserialize_token(s: str) -> EntitlementToken:
    """Deserialize a token. Old-format tokens (without delegation fields) work."""
    d = json.loads(s)
    return EntitlementToken(
        token_id=d["token_id"],
        granted_to=d["granted_to"],
        granted_by=d["granted_by"],
        shard_keys=d["shard_keys"],
        scopes=d["scopes"],
        capabilities=d["capabilities"],
        secrets=d["secrets"],
        budget_usd=d["budget_usd"],
        created_at=d["created_at"],
        expires_at=d.get("expires_at"),
        trace_parent=d.get("trace_parent"),
        constraints=d.get("constraints", {}),
        subject_pubkey=bytes.fromhex(d["subject_pubkey"]) if "subject_pubkey" in d else None,
        issuer_pubkey=bytes.fromhex(d["issuer_pubkey"]) if "issuer_pubkey" in d else None,
        parent_cap_id=d.get("parent_cap_id"),
        caveats=[Caveat.from_dict(c) for c in d.get("caveats", [])],
        signature=d.get("signature"),
    )


# === Chain verification + delegation =====================================


def _max_delegation_depth(caveats: list[Caveat]) -> int | None:
    """Return the cap's max_delegation_depth caveat value, or None if absent."""
    for c in caveats:
        if c.type == CaveatType.MAX_DELEGATION_DEPTH:
            return c.value
    return None


def verify_cap_chain(
    chain: list[EntitlementToken],
    *,
    root_pubkeys: list[bytes],
) -> bool:
    """Verify a capability delegation chain from root (chain[0]) to leaf (chain[-1]).

    Confirms:
      - Every cap has both ``subject_pubkey`` and ``issuer_pubkey`` set.
      - Every link's signature verifies against its ``issuer_pubkey``.
      - Each link's ``issuer_pubkey`` equals the parent's ``subject_pubkey``.
      - Each link's ``parent_cap_id`` equals the parent's ``cap_id``.
      - The root's ``issuer_pubkey`` is in ``root_pubkeys``.
      - The root has ``parent_cap_id is None``.

    Does NOT validate caveats — that's :func:`authorize_chain`, which
    needs the request context (scope, time).

    Returns True on success; raises ValueError or InvalidSignature on failure.
    """
    if not chain:
        raise ValueError("Empty chain")

    # Pre-flight: every cap must carry a subject pubkey. Even on a
    # single-link [root] chain, a root without subject_pubkey is in a
    # confusing state — it can't issue children and can't have produced
    # shards attributed to it. Reject up front.
    for i, cap in enumerate(chain):
        if cap.subject_pubkey is None:
            raise ValueError(f"Cap at position {i} has no subject_pubkey")

    root = chain[0]
    if root.parent_cap_id is not None:
        raise ValueError("Root cap must have parent_cap_id=None")
    if root.issuer_pubkey is None:
        raise ValueError("Root cap has no issuer_pubkey")
    if not any(root.issuer_pubkey == rk for rk in root_pubkeys):
        raise ValueError(
            f"Root issuer {pubkey_thumbprint(root.issuer_pubkey)[:12]}… "
            f"is not in trust roots"
        )
    root.verify()

    for i in range(1, len(chain)):
        parent = chain[i - 1]
        link = chain[i]
        if link.issuer_pubkey != parent.subject_pubkey:
            raise ValueError(
                f"Chain broken at position {i}: issuer "
                f"{pubkey_thumbprint(link.issuer_pubkey)[:12] if link.issuer_pubkey else 'None'}… "
                f"!= parent subject "
                f"{pubkey_thumbprint(parent.subject_pubkey)[:12]}…"
            )
        if link.parent_cap_id != parent.cap_id:
            raise ValueError(
                f"Chain broken at position {i}: parent_cap_id "
                f"{(link.parent_cap_id or '')[:12]}… "
                f"!= parent.cap_id {parent.cap_id[:12]}…"
            )
        link.verify()

    return True


def authorize_chain(
    chain: list[EntitlementToken],
    *,
    scope: str | None = None,
    now_iso: str | None = None,
) -> bool:
    """Check that the *intersection* of every cap's caveats permits the operation.

    Each cap is checked with its own ``delegation_depth`` (the count of
    descendants below it in the chain). Use after :func:`verify_cap_chain`.

    Args:
        chain: non-empty list of caps from root (index 0) to leaf.
        scope: requested scope. Passing ``None`` causes ``scope_limit``
            caveats to vacuously pass — only do this when the operation
            genuinely has no scope (e.g., a generic capability check).
            For shard writes, always pass the shard's scope.
        now_iso: current time as ISO 8601. Defaults to wall clock.

    Returns True if all caveats on all caps pass; False if any cap denies.

    Raises:
        ValueError: chain is empty.
        UnknownCaveatError: any caveat type is unrecognized (fail closed).
    """
    if not chain:
        raise ValueError("Empty chain")
    n = len(chain)
    for level, token in enumerate(chain):
        depth_below = n - 1 - level
        if not validate_caveats(
            token.caveats,
            scope=scope,
            delegation_depth=depth_below,
            now_iso=now_iso,
        ):
            return False
    return True


def issue_delegated(
    parent: EntitlementToken,
    parent_private_key: bytes,
    *,
    subject_pubkey: bytes,
    granted_to: str,
    scopes: list[str] | None = None,
    capabilities: list[str] | None = None,
    secrets: list[str] | None = None,
    shard_keys: dict[str, bytes] | None = None,
    budget_usd: float = 0.0,
    expires_at: str | None = None,
    caveats: list[Caveat] | None = None,
) -> EntitlementToken:
    """Issue a child cap delegated from ``parent``, signed by parent's key.

    Validates that the parent permits further delegation (its
    ``max_delegation_depth`` is unset or > 0) and that the child's
    explicit max_delegation_depth, if provided, doesn't exceed
    ``parent.max - 1``. Auto-decrements depth if the parent has one
    and the child doesn't override it.

    Caveats from the parent are NOT auto-inherited — the parent's
    caveats still apply via chain intersection at authorize time. This
    keeps each cap's caveats focused on what *that level* added.

    .. warning::

        Callers SHOULD run :func:`verify_cap_chain` on the parent's full
        chain before issuing a child. This function trusts ``parent``
        as supplied — a manually constructed or tampered parent token
        will still produce a syntactically valid child, but the child
        will fail chain verification downstream. Verify first.
    """
    parent_max = _max_delegation_depth(parent.caveats)
    if parent_max is not None and parent_max <= 0:
        raise ValueError(
            "Parent cap does not permit further delegation "
            "(max_delegation_depth is 0)"
        )

    child_caveats = list(caveats or [])
    explicit_max = _max_delegation_depth(child_caveats)
    if parent_max is not None:
        derived_max = parent_max - 1
        if explicit_max is None:
            child_caveats.append(
                Caveat(CaveatType.MAX_DELEGATION_DEPTH, derived_max)
            )
        elif explicit_max > derived_max:
            raise ValueError(
                f"Child max_delegation_depth ({explicit_max}) exceeds "
                f"parent's allowance ({derived_max})"
            )

    child = EntitlementToken(
        token_id=str(uuid.uuid4()),
        granted_to=granted_to,
        granted_by=parent.granted_to,
        shard_keys={sid: serialize_key(k) for sid, k in (shard_keys or {}).items()},
        scopes=scopes if scopes is not None else list(parent.scopes),
        capabilities=capabilities if capabilities is not None else list(parent.capabilities),
        secrets=secrets if secrets is not None else list(parent.secrets),
        budget_usd=budget_usd,
        created_at=_now_iso(),
        expires_at=expires_at,
        subject_pubkey=subject_pubkey,
        issuer_pubkey=parent.subject_pubkey,
        parent_cap_id=parent.cap_id,
        caveats=child_caveats,
    )
    child.sign(parent_private_key)
    return child
