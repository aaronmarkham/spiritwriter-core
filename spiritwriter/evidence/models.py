"""Immutable, content-addressed records for the generic evidence kernel."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Optional

from .canonical import (
    MAX_SAFE_INTEGER,
    CanonicalJsonError,
    JsonValue,
    canonical_json_bytes,
    domain_digest,
    sha256_hex,
    validate_json_value,
)

SOURCE_DOMAIN = "spiritwriter.evidence.source.v1"
EVIDENCE_DOMAIN = "spiritwriter.evidence.unit.v1"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class EvidenceValidationError(ValueError):
    """An evidence record violates its structural or integrity contract."""


def _require_string(name: str, value: object, *, allow_empty: bool) -> str:
    if type(value) is not str or (not allow_empty and not value):
        qualifier = "a string" if allow_empty else "a non-empty string"
        raise EvidenceValidationError(f"{name} must be {qualifier}")
    try:
        validate_json_value(value, path=name)
    except CanonicalJsonError as error:
        raise EvidenceValidationError(str(error)) from error
    return value


def _require_nonempty_string(name: str, value: object) -> str:
    return _require_string(name, value, allow_empty=False)


def _require_sha256(name: str, value: object) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise EvidenceValidationError(
            f"{name} must be a 64-character lowercase SHA-256 digest"
        )
    return value


def _require_exact_fields(
    record_name: str,
    value: object,
    required: set[str],
    optional: Optional[set[str]] = None,
) -> dict[str, JsonValue]:
    if type(value) is not dict:
        raise EvidenceValidationError(f"{record_name} must be a JSON object")
    invalid_keys = [key for key in value if type(key) is not str]
    if invalid_keys:
        raise EvidenceValidationError(f"{record_name} contains a non-string field name")
    optional = optional or set()
    actual = set(value)
    missing = required - actual
    unknown = actual - required - optional
    if missing:
        raise EvidenceValidationError(
            f"{record_name} is missing fields: {', '.join(sorted(missing))}"
        )
    if unknown:
        raise EvidenceValidationError(
            f"{record_name} has unknown fields: {', '.join(sorted(unknown))}"
        )
    return value


def _freeze_json(value: JsonValue) -> bytes:
    """Validate and snapshot JSON so caller mutation cannot change identity."""

    try:
        return canonical_json_bytes(value)
    except CanonicalJsonError as error:
        raise EvidenceValidationError(str(error)) from error


def _thaw_json(data: bytes) -> JsonValue:
    # data was produced by canonical_json_bytes, so default parsing cannot
    # introduce floats or duplicate keys.
    return json.loads(data.decode("utf-8"))


@dataclass(frozen=True)
class SourceSnapshot:
    """Identity and integrity metadata for one immutable source revision."""

    logical_ref: str
    revision: str
    media_type: str
    byte_length: int
    raw_sha256: str
    content_ref: str

    def __post_init__(self) -> None:
        _require_nonempty_string("logical_ref", self.logical_ref)
        _require_nonempty_string("revision", self.revision)
        _require_nonempty_string("media_type", self.media_type)
        if (
            type(self.byte_length) is not int
            or self.byte_length < 0
            or self.byte_length > MAX_SAFE_INTEGER
        ):
            raise EvidenceValidationError(
                f"byte_length must be an integer from 0 through {MAX_SAFE_INTEGER}"
            )
        _require_sha256("raw_sha256", self.raw_sha256)
        _require_nonempty_string("content_ref", self.content_ref)

    def _identity_dict(self) -> dict[str, JsonValue]:
        return {
            "byte_length": self.byte_length,
            "content_ref": self.content_ref,
            "logical_ref": self.logical_ref,
            "media_type": self.media_type,
            "raw_sha256": self.raw_sha256,
            "revision": self.revision,
        }

    @property
    def source_id(self) -> str:
        """Content address for the complete snapshot record."""

        return domain_digest(SOURCE_DOMAIN, self._identity_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {"source_id": self.source_id, **self._identity_dict()}

    @classmethod
    def from_dict(cls, value: object) -> "SourceSnapshot":
        record = _require_exact_fields(
            "SourceSnapshot",
            value,
            {
                "source_id",
                "logical_ref",
                "revision",
                "media_type",
                "byte_length",
                "raw_sha256",
                "content_ref",
            },
        )
        snapshot = cls(
            logical_ref=record["logical_ref"],
            revision=record["revision"],
            media_type=record["media_type"],
            byte_length=record["byte_length"],
            raw_sha256=record["raw_sha256"],
            content_ref=record["content_ref"],
        )
        if record["source_id"] != snapshot.source_id:
            raise EvidenceValidationError("SourceSnapshot source_id mismatch")
        return snapshot

    @classmethod
    def from_content(
        cls,
        *,
        logical_ref: str,
        revision: str,
        media_type: str,
        content_ref: str,
        content: bytes,
    ) -> "SourceSnapshot":
        """Construct a snapshot while computing byte integrity fields."""

        if type(content) is not bytes:
            raise EvidenceValidationError("content must be bytes")
        return cls(
            logical_ref=logical_ref,
            revision=revision,
            media_type=media_type,
            byte_length=len(content),
            raw_sha256=sha256_hex(content),
            content_ref=content_ref,
        )

    def verify_content(self, content: bytes) -> None:
        """Raise if bytes do not match the snapshot."""

        if type(content) is not bytes:
            raise EvidenceValidationError("content must be bytes")
        if len(content) != self.byte_length:
            raise EvidenceValidationError("source byte_length mismatch")
        if sha256_hex(content) != self.raw_sha256:
            raise EvidenceValidationError("source raw_sha256 mismatch")


@dataclass(frozen=True, init=False)
class Locator:
    """A versioned, source-native address with an immutable JSON value."""

    scheme: str
    version: str
    _value_bytes: bytes = field(repr=False)

    def __init__(self, scheme: str, version: str, value: JsonValue) -> None:
        object.__setattr__(self, "scheme", _require_nonempty_string("scheme", scheme))
        object.__setattr__(self, "version", _require_nonempty_string("version", version))
        object.__setattr__(self, "_value_bytes", _freeze_json(value))

    @property
    def value(self) -> JsonValue:
        """Return a detached JSON value, preserving locator immutability."""

        return _thaw_json(self._value_bytes)

    def to_dict(self) -> dict[str, JsonValue]:
        return {"scheme": self.scheme, "value": self.value, "version": self.version}

    @classmethod
    def from_dict(cls, value: object) -> "Locator":
        record = _require_exact_fields(
            "Locator", value, {"scheme", "version", "value"}
        )
        return cls(
            scheme=record["scheme"],
            version=record["version"],
            value=record["value"],
        )


@dataclass(frozen=True, init=False)
class EvidenceUnit:
    """One exact, source-linked evidence excerpt with typed JSON payload.

    Adapter and normalization profile identifiers participate in the evidence
    identity, preventing semantically different extraction rules from sharing
    an ID even when they currently produce the same excerpt.
    """

    source_id: str
    kind: str
    adapter_id: str
    normalization_profile: str
    locator: Locator
    normalized_excerpt: str
    normalized_sha256: str
    raw_span_sha256: Optional[str]
    _payload_bytes: bytes = field(repr=False)

    def __init__(
        self,
        *,
        source_id: str,
        kind: str,
        adapter_id: str,
        normalization_profile: str,
        locator: Locator,
        normalized_excerpt: str,
        normalized_sha256: str,
        payload: JsonValue,
        raw_span_sha256: Optional[str] = None,
    ) -> None:
        object.__setattr__(self, "source_id", _require_sha256("source_id", source_id))
        object.__setattr__(self, "kind", _require_nonempty_string("kind", kind))
        object.__setattr__(
            self, "adapter_id", _require_nonempty_string("adapter_id", adapter_id)
        )
        object.__setattr__(
            self,
            "normalization_profile",
            _require_nonempty_string("normalization_profile", normalization_profile),
        )
        if type(locator) is not Locator:
            raise EvidenceValidationError("locator must be a Locator")
        object.__setattr__(self, "locator", locator)
        excerpt = _require_string(
            "normalized_excerpt", normalized_excerpt, allow_empty=True
        )
        object.__setattr__(self, "normalized_excerpt", excerpt)
        digest = _require_sha256("normalized_sha256", normalized_sha256)
        expected = sha256_hex(excerpt.encode("utf-8"))
        if digest != expected:
            raise EvidenceValidationError("normalized_sha256 does not match normalized_excerpt")
        object.__setattr__(self, "normalized_sha256", digest)
        if raw_span_sha256 is not None:
            _require_sha256("raw_span_sha256", raw_span_sha256)
        object.__setattr__(self, "raw_span_sha256", raw_span_sha256)
        object.__setattr__(self, "_payload_bytes", _freeze_json(payload))

    @property
    def payload(self) -> JsonValue:
        """Return a detached JSON payload, preserving unit immutability."""

        return _thaw_json(self._payload_bytes)

    def _identity_dict(self) -> dict[str, JsonValue]:
        record: dict[str, JsonValue] = {
            "adapter_id": self.adapter_id,
            "kind": self.kind,
            "locator": self.locator.to_dict(),
            "normalization_profile": self.normalization_profile,
            "normalized_excerpt": self.normalized_excerpt,
            "normalized_sha256": self.normalized_sha256,
            "payload": self.payload,
            "source_id": self.source_id,
        }
        if self.raw_span_sha256 is not None:
            record["raw_span_sha256"] = self.raw_span_sha256
        return record

    @property
    def evidence_id(self) -> str:
        """Content address for the complete evidence unit."""

        return domain_digest(EVIDENCE_DOMAIN, self._identity_dict())

    def to_dict(self) -> dict[str, JsonValue]:
        return {"evidence_id": self.evidence_id, **self._identity_dict()}

    @classmethod
    def from_dict(cls, value: object) -> "EvidenceUnit":
        record = _require_exact_fields(
            "EvidenceUnit",
            value,
            {
                "evidence_id",
                "source_id",
                "kind",
                "adapter_id",
                "normalization_profile",
                "locator",
                "normalized_excerpt",
                "normalized_sha256",
                "payload",
            },
            {"raw_span_sha256"},
        )
        if "raw_span_sha256" in record and record["raw_span_sha256"] is None:
            raise EvidenceValidationError(
                "raw_span_sha256 must be omitted or contain a SHA-256 digest"
            )
        unit = cls(
            source_id=record["source_id"],
            kind=record["kind"],
            adapter_id=record["adapter_id"],
            normalization_profile=record["normalization_profile"],
            locator=Locator.from_dict(record["locator"]),
            normalized_excerpt=record["normalized_excerpt"],
            normalized_sha256=record["normalized_sha256"],
            raw_span_sha256=record.get("raw_span_sha256"),
            payload=record["payload"],
        )
        if record["evidence_id"] != unit.evidence_id:
            raise EvidenceValidationError("EvidenceUnit evidence_id mismatch")
        return unit
