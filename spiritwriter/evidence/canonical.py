"""Portable canonical JSON and domain-separated digests for evidence records.

The codec intentionally accepts a smaller language than general JSON:
``null``, booleans, interoperable integers, Unicode strings, arrays, and
objects whose keys are strings. Floats and non-finite numeric values are never
accepted. This profile is independent of the legacy fabric shard codec.
"""

from __future__ import annotations

import hashlib
import json
from typing import Dict, List, Union

JsonScalar = Union[None, bool, int, str]
JsonValue = Union[JsonScalar, List["JsonValue"], Dict[str, "JsonValue"]]

# IEEE-754's exactly representable integer range. Restricting the wire format
# to this range keeps vectors portable to JSON implementations that represent
# every number as a double.
MIN_SAFE_INTEGER = -(2**53 - 1)
MAX_SAFE_INTEGER = 2**53 - 1


class CanonicalJsonError(ValueError):
    """The value or bytes do not conform to the evidence JSON profile."""


def _validate_string(value: str, path: str) -> None:
    for character in value:
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise CanonicalJsonError(
                f"{path} contains a Unicode surrogate (U+{codepoint:04X})"
            )


def validate_json_value(value: object, *, path: str = "$") -> None:
    """Validate a value against the portable evidence JSON subset.

    Types are checked exactly. In particular, custom ``int``/``str``
    subclasses and tuples are rejected rather than being silently coerced.
    """

    value_type = type(value)
    if value is None or value_type is bool:
        return
    if value_type is int:
        if value < MIN_SAFE_INTEGER or value > MAX_SAFE_INTEGER:
            raise CanonicalJsonError(
                f"{path} integer is outside the interoperable range "
                f"[{MIN_SAFE_INTEGER}, {MAX_SAFE_INTEGER}]"
            )
        return
    if value_type is str:
        _validate_string(value, path)
        return
    if value_type is list:
        for index, item in enumerate(value):
            validate_json_value(item, path=f"{path}[{index}]")
        return
    if value_type is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise CanonicalJsonError(f"{path} contains a non-string object key")
            _validate_string(key, f"{path} object key")
            validate_json_value(item, path=f"{path}[{key!r}]")
        return
    raise CanonicalJsonError(
        f"{path} has unsupported type {value_type.__name__}; floats are not allowed"
    )


def _encode_string(value: str) -> str:
    # json.dumps has the required JSON escaping behavior. ensure_ascii=False
    # preserves Unicode scalar values as UTF-8 instead of implementation-
    # dependent ASCII escape choices.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _encode(value: JsonValue) -> str:
    if value is None:
        return "null"
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is str:
        return _encode_string(value)
    if type(value) is list:
        return "[" + ",".join(_encode(item) for item in value) + "]"
    # validate_json_value guarantees an exact dict with string keys. Unicode
    # scalar/code-point order is deterministic and UTF-8 lexical order agrees
    # with it, which gives non-Python implementations a simple sort rule.
    return "{" + ",".join(
        _encode_string(key) + ":" + _encode(value[key])
        for key in sorted(value)
    ) + "}"


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Return the unique UTF-8 encoding of a portable JSON value."""

    validate_json_value(value)
    return _encode(value).encode("utf-8")


def _reject_float(value: str) -> None:
    raise CanonicalJsonError(f"floating-point JSON number is not allowed: {value}")


def _reject_constant(value: str) -> None:
    raise CanonicalJsonError(f"non-finite JSON number is not allowed: {value}")


def _object_without_duplicate_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalJsonError(f"duplicate JSON object key: {key!r}")
        result[key] = value
    return result


def parse_canonical_json_bytes(data: bytes) -> JsonValue:
    """Parse canonical bytes, rejecting valid-but-noncanonical JSON."""

    if type(data) is not bytes:
        raise TypeError("data must be bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CanonicalJsonError("canonical JSON must be valid UTF-8") from error
    try:
        value = json.loads(
            text,
            parse_float=_reject_float,
            parse_constant=_reject_constant,
            object_pairs_hook=_object_without_duplicate_keys,
        )
    except CanonicalJsonError:
        raise
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise CanonicalJsonError("invalid JSON") from error
    validate_json_value(value)
    if canonical_json_bytes(value) != data:
        raise CanonicalJsonError("JSON bytes are not in canonical form")
    return value


def sha256_hex(data: bytes) -> str:
    """Return the lowercase SHA-256 digest of bytes."""

    if type(data) is not bytes:
        raise TypeError("data must be bytes")
    return hashlib.sha256(data).hexdigest()


def domain_digest(domain: str, value: JsonValue) -> str:
    """Digest a canonical JSON value under an explicit domain.

    The preimage is ``UTF8(domain) || NUL || canonical_json_bytes(value)``.
    Domains must be non-empty printable ASCII without NUL, making the framing
    reproducible in any language and keeping identities from different record
    types separate.
    """

    if type(domain) is not str or not domain:
        raise CanonicalJsonError("digest domain must be a non-empty string")
    if "\x00" in domain or any(ord(character) < 0x21 or ord(character) > 0x7E for character in domain):
        raise CanonicalJsonError("digest domain must contain printable ASCII only")
    preimage = domain.encode("ascii") + b"\x00" + canonical_json_bytes(value)
    return sha256_hex(preimage)
