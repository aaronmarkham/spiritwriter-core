"""Memory shard format — the core unit of distributable agent memory.

A shard is a content-addressed bundle of knowledge atoms with:
- Provenance (who created it, from what trace)
- Scope (entitlement boundary for access control)
- Decay metadata (TTL class, freshness)
- Self-describing schema for hydration

Shards are the unit of storage in local cache and DHT.
Agents receive shard references (pointers), not full content.
Hydration is pull-based: agent resolves refs → fetches shards → injects context.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


class DecayClass(str, Enum):
    """How long a shard or atom should live."""
    PERMANENT = "permanent"    # Never expires (decisions, identities, architecture)
    STABLE = "stable"          # 90 days, refreshed on access (project details, relationships)
    ACTIVE = "active"          # 14 days, refreshed on access (current tasks, sprint goals)
    SESSION = "session"        # 24 hours (debugging context, temp state)
    CHECKPOINT = "checkpoint"  # 4 hours (pre-flight state saves)


class AtomKind(str, Enum):
    """What kind of knowledge this atom represents."""
    FACT = "fact"              # Structured entity/key/value
    DECISION = "decision"     # Choice + rationale
    CONVENTION = "convention"  # Always/never rules
    PREFERENCE = "preference"  # User preferences
    ENTITY = "entity"         # Named entity info
    CONTEXT = "context"       # Freeform contextual knowledge
    CHECKPOINT = "checkpoint"  # Pre-flight state snapshot
    INSTRUCTION = "instruction"  # How to do things (skills, workflows, commands)


def _canonical_json(obj: Any) -> bytes:
    """Deterministic JSON for content addressing.

    Two properties matter wherever these bytes are compared rather than
    just hashed (see ``fabric.orbit``, which orders by them):

    * Ordering is lexicographic over the encoded bytes, so numbers sort
      as text — ``10`` precedes ``9``. Zero-pad, or use ISO-8601 for
      dates, when a value-ordered result is wanted.
    * JSON coerces dict keys to strings, so ``{1: "a"}`` and
      ``{"1": "a"}`` encode identically and share a content address.
    """
    return json.dumps(
        obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    """SHA-256 hex digest."""
    return hashlib.sha256(data).hexdigest()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_signing_keypair() -> tuple[bytes, bytes]:
    """Generate an Ed25519 keypair for shard signing.

    Returns ``(private_key_seed, public_key)`` — both 32 bytes raw.
    The seed is the canonical Ed25519 private key form; pass it to
    :meth:`MemoryShard.sign`. Store with restricted permissions.

    Uses cryptography (already a hard dep) rather than PyNaCl so signed
    shards work without the optional ``[sealed]`` extra. Keys are
    interchangeable with PyNaCl-generated Ed25519 keys at the byte level.
    """
    sk = Ed25519PrivateKey.generate()
    sk_bytes = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pk_bytes = sk.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return sk_bytes, pk_bytes


def pubkey_thumbprint(pubkey_bytes: bytes) -> str:
    """sha256 hex digest of an Ed25519 public key.

    Used as the shard's ``created_by`` field to identify the signer
    without embedding the full key. Reproducible cross-language from
    raw key bytes — matches the project's hex-everywhere convention.
    """
    return hashlib.sha256(pubkey_bytes).hexdigest()


@dataclass
class ShardAtom:
    """A single knowledge atom within a shard.

    Atoms are the smallest unit of retrievable knowledge.
    Structured atoms have entity/key/value for exact lookup.
    All atoms have text for embedding/FTS.
    """
    text: str
    kind: AtomKind = AtomKind.CONTEXT
    entity: str | None = None
    key: str | None = None
    value: str | None = None
    confidence: float = 1.0
    source_ref: str | None = None  # trace event_id or doc reference

    @property
    def content_hash(self) -> str:
        """Content address of this atom."""
        return _sha256(_canonical_json({
            "text": self.text,
            "kind": self.kind.value,
            "entity": self.entity,
            "key": self.key,
            "value": self.value,
        }))

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "text": self.text,
            "kind": self.kind.value,
        }
        if self.entity is not None:
            d["entity"] = self.entity
        if self.key is not None:
            d["key"] = self.key
        if self.value is not None:
            d["value"] = self.value
        if self.confidence != 1.0:
            d["confidence"] = self.confidence
        if self.source_ref is not None:
            d["source_ref"] = self.source_ref
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShardAtom:
        return cls(
            text=d["text"],
            kind=AtomKind(d.get("kind", "context")),
            entity=d.get("entity"),
            key=d.get("key"),
            value=d.get("value"),
            confidence=d.get("confidence", 1.0),
            source_ref=d.get("source_ref"),
        )


@dataclass
class ShardRef:
    """A lightweight pointer to a shard.

    This is what gets passed to sub-agents instead of full context.
    The agent resolves the ref via local cache or DHT to hydrate.
    """
    shard_id: str              # sha256 content address
    scope: str                 # entitlement boundary (e.g., "project:csp")
    label: str | None = None   # human-readable hint (e.g., "CSP project context")
    origin: str | None = None  # agent that created it

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"shard_id": self.shard_id, "scope": self.scope}
        if self.label:
            d["label"] = self.label
        if self.origin:
            d["origin"] = self.origin
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ShardRef:
        return cls(
            shard_id=d["shard_id"],
            scope=d["scope"],
            label=d.get("label"),
            origin=d.get("origin"),
        )


@dataclass
class MemoryShard:
    """A content-addressed bundle of knowledge atoms.

    The fundamental unit of distributable agent memory.
    Immutable once created — content changes produce a new shard.
    """
    atoms: list[ShardAtom]
    scope: str                           # entitlement boundary
    origin: str                          # creating agent id
    decay_class: DecayClass = DecayClass.STABLE
    created_at: str = field(default_factory=_now_iso)
    trace_ref: str | None = None         # link to trace chain
    parent_shard_id: str | None = None   # if this shard supersedes another
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    last_checked: str | None = None      # ISO timestamp — last time content was verified/polled
    check_count: int = 0                 # number of verification/poll cycles completed
    # Authorship / authorization (optional — unsigned shards still work)
    signature: str | None = None         # hex Ed25519 signature over signing_payload()
    created_by: str | None = None        # sha256 hex thumbprint of signer pubkey
    cap_id: str | None = None            # reference to the capability that authorized this

    @property
    def shard_id(self) -> str:
        """Content address — deterministic hash of atoms + scope + origin."""
        payload = _canonical_json({
            "atoms": [a.to_dict() for a in self.atoms],
            "scope": self.scope,
            "origin": self.origin,
        })
        return _sha256(payload)

    @property
    def ref(self) -> ShardRef:
        """Create a lightweight reference to this shard."""
        label = self.tags[0] if self.tags else None
        return ShardRef(
            shard_id=self.shard_id,
            scope=self.scope,
            label=label,
            origin=self.origin,
        )

    @property
    def token_estimate(self) -> int:
        """Rough token count for hydration cost estimation."""
        total_chars = sum(len(a.text) for a in self.atoms)
        # Add overhead for structured fields
        for a in self.atoms:
            if a.entity:
                total_chars += len(a.entity) + 10
            if a.key:
                total_chars += len(a.key) + 10
            if a.value:
                total_chars += len(a.value) + 10
        return total_chars // 4  # rough char-to-token ratio

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage/transport."""
        d: dict[str, Any] = {
            "shard_id": self.shard_id,
            "atoms": [a.to_dict() for a in self.atoms],
            "scope": self.scope,
            "origin": self.origin,
            "decay_class": self.decay_class.value,
            "created_at": self.created_at,
        }
        if self.trace_ref:
            d["trace_ref"] = self.trace_ref
        if self.parent_shard_id:
            d["parent_shard_id"] = self.parent_shard_id
        if self.tags:
            d["tags"] = self.tags
        if self.meta:
            d["meta"] = self.meta
        if self.last_checked is not None:
            d["last_checked"] = self.last_checked
        if self.check_count:
            d["check_count"] = self.check_count
        if self.cap_id is not None:
            d["cap_id"] = self.cap_id
        if self.created_by is not None:
            d["created_by"] = self.created_by
        if self.signature is not None:
            d["signature"] = self.signature
        return d

    def to_json(self) -> str:
        """Canonical JSON for storage."""
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MemoryShard:
        shard = cls(
            atoms=[ShardAtom.from_dict(a) for a in d["atoms"]],
            scope=d["scope"],
            origin=d["origin"],
            decay_class=DecayClass(d.get("decay_class", "stable")),
            created_at=d.get("created_at", _now_iso()),
            trace_ref=d.get("trace_ref"),
            parent_shard_id=d.get("parent_shard_id"),
            tags=d.get("tags", []),
            meta=d.get("meta", {}),
            last_checked=d.get("last_checked"),
            check_count=d.get("check_count", 0),
            signature=d.get("signature"),
            created_by=d.get("created_by"),
            cap_id=d.get("cap_id"),
        )
        # Verify content address if provided
        stored_id = d.get("shard_id")
        if stored_id and stored_id != shard.shard_id:
            raise ValueError(
                f"Shard content mismatch: stored={stored_id}, "
                f"computed={shard.shard_id}"
            )
        return shard

    @classmethod
    def from_json(cls, raw: str) -> MemoryShard:
        return cls.from_dict(json.loads(raw))

    # === Signing / Verification (Ed25519) ===

    def signing_payload(self) -> bytes:
        """Canonical bytes covered by the signature.

        Includes every envelope field except `signature` itself and the
        operational counters (`last_checked`, `check_count`) that change
        as a shard is polled. Excluding those means polling doesn't
        invalidate the signature.

        shard_id stays in the signed payload — it's recomputed in
        to_dict() from {atoms, scope, origin}, so the signature ends
        up binding the content address to the envelope.
        """
        d = self.to_dict()
        d.pop("signature", None)
        d.pop("last_checked", None)
        d.pop("check_count", None)
        return _canonical_json(d)

    def sign(
        self,
        private_key_bytes: bytes,
        *,
        pubkey_bytes: bytes | None = None,
    ) -> None:
        """Sign this shard in place with an Ed25519 private key.

        Sets `signature` (hex Ed25519) and `created_by` (sha256 hex of
        the public key). If `created_by` was already set, validates that
        it matches the signing key's pubkey thumbprint.

        Args:
            private_key_bytes: 32-byte Ed25519 seed (raw private key).
            pubkey_bytes: optional 32-byte raw public key. Derived from
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
        thumbprint = pubkey_thumbprint(pk)
        if self.created_by is not None and self.created_by != thumbprint:
            raise ValueError(
                f"created_by mismatch: shard claims {self.created_by[:12]}…, "
                f"signing key thumbprint is {thumbprint[:12]}…"
            )
        self.created_by = thumbprint
        self.signature = sk.sign(self.signing_payload()).hex()

    def verify(self, pubkey_bytes: bytes) -> bool:
        """Verify the shard's signature against a public key.

        Returns True on success.

        Raises:
            ValueError: shard is unsigned, or the provided pubkey
                doesn't match the shard's `created_by` thumbprint.
            cryptography.exceptions.InvalidSignature: signature is bad.
        """
        if self.signature is None:
            raise ValueError("Shard has no signature to verify")
        if self.created_by is not None:
            thumbprint = pubkey_thumbprint(pubkey_bytes)
            if thumbprint != self.created_by:
                raise ValueError(
                    f"Pubkey thumbprint {thumbprint[:12]}… does not match "
                    f"created_by {self.created_by[:12]}…"
                )
        pk = Ed25519PublicKey.from_public_bytes(pubkey_bytes)
        pk.verify(bytes.fromhex(self.signature), self.signing_payload())
        return True

    def get_atom(self, key: str) -> ShardAtom | None:
        """Find the first atom with a matching key."""
        return next((a for a in self.atoms if a.key == key), None)

    def hydrate_context(self) -> str:
        """Render this shard as injectable agent context.

        Full XML-tagged format with atom kind labels. Use this when
        the consuming agent parses structured context.

        For token-constrained scenarios, use hydrate_compact() instead.
        """
        lines = []
        for atom in self.atoms:
            if atom.kind == AtomKind.INSTRUCTION:
                # Instructions render as actionable steps
                if atom.key:
                    lines.append(f"- **{atom.key}**: {atom.text}")
                else:
                    lines.append(f"- {atom.text}")
            elif atom.entity and atom.key and atom.value:
                lines.append(f"- [{atom.kind.value}] {atom.entity}.{atom.key} = {atom.value}")
            elif atom.entity and atom.key:
                lines.append(f"- [{atom.kind.value}] {atom.entity}.{atom.key}: {atom.text}")
            else:
                lines.append(f"- [{atom.kind.value}] {atom.text}")
        scope_label = self.tags[0] if self.tags else self.scope
        return f"<shard scope=\"{self.scope}\" label=\"{scope_label}\">\n" + "\n".join(lines) + "\n</shard>"

    def hydrate_compact(self) -> str:
        """Render this shard as minimal key=value pairs.

        No XML wrapper, no atom kind labels. Just the facts.
        Roughly 40-60% fewer characters than hydrate_context(),
        depending on atom structure.

        Use this in token-constrained contexts where every token
        counts (large context assembly, budget-limited agents).
        """
        lines = []
        for atom in self.atoms:
            if atom.entity and atom.key and atom.value:
                lines.append(f"{atom.entity}.{atom.key}={atom.value}")
            elif atom.key and atom.value:
                lines.append(f"{atom.key}={atom.value}")
            elif atom.key:
                lines.append(f"{atom.key}: {atom.text}")
            else:
                # Freeform text — entity (if any) is intentionally dropped
                # since there's no key to anchor it. The text alone is the content.
                lines.append(atom.text)
        return "\n".join(lines)
