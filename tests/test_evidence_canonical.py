"""Golden vectors and invalid fixtures for the evidence JSON profile."""

from decimal import Decimal

import pytest

from spiritwriter.evidence import (
    MAX_JSON_DEPTH,
    MAX_SAFE_INTEGER,
    MIN_SAFE_INTEGER,
    CanonicalJsonError,
    canonical_json_bytes,
    domain_digest,
    parse_canonical_json_bytes,
)


# Exact bytes are the compatibility contract. These are constants rather than
# values generated from another serializer inside the test.
GOLDEN_CANONICAL_VECTORS = [
    (None, b"null"),
    (True, b"true"),
    (MIN_SAFE_INTEGER, b"-9007199254740991"),
    (MAX_SAFE_INTEGER, b"9007199254740991"),
    (
        "\x00\b\t\n\f\r\x1f\"\\/é\u2028",
        b'"\\u0000\\b\\t\\n\\f\\r\\u001f' b'\\"\\\\/' b'\xc3\xa9\xe2\x80\xa8"',
    ),
    (
        {
            "z": "雪",
            "a": [None, True, False, 0, -7, 9007199254740991],
            "escaped": "line\n\"quote\"",
        },
        b'{"a":[null,true,false,0,-7,9007199254740991],'
        b'"escaped":"line\\n\\"quote\\"","z":"\xe9\x9b\xaa"}',
    ),
    (
        {"\U00010000": 6, "aa": 4, "\ue000": 5, "a\x00": 3, "a": 2, "\r": 1},
        b'{"\\r":1,"a":2,"a\\u0000":3,"aa":4,'
        b'"\xee\x80\x80":5,"\xf0\x90\x80\x80":6}',
    ),
    (
        {"é": "precomposed", "e\u0301": "decomposed"},
        b'{"e\xcc\x81":"decomposed","\xc3\xa9":"precomposed"}',
    ),
]


@pytest.mark.parametrize("value, expected", GOLDEN_CANONICAL_VECTORS)
def test_canonical_json_golden_vectors(value, expected):
    assert canonical_json_bytes(value) == expected
    assert parse_canonical_json_bytes(expected) == value


def test_domain_digest_golden_vector():
    assert domain_digest("example.domain.v1", {"a": 1, "b": "two"}) == (
        "91394377940cb45489245d278b2703caf12ec111c116f6b6f9a59086c66f004b"
    )


INVALID_VALUE_FIXTURES = [
    1.0,
    float("nan"),
    float("inf"),
    Decimal("1"),
    ("array",),
    b"bytes",
    {1: "non-string key"},
    9007199254740992,
    -9007199254740992,
    "\ud800",
]


@pytest.mark.parametrize("value", INVALID_VALUE_FIXTURES)
def test_invalid_value_fixtures_fail_closed(value):
    with pytest.raises(CanonicalJsonError):
        canonical_json_bytes(value)


INVALID_BYTE_FIXTURES = [
    b'{"b":2,"a":1}',  # keys are not sorted
    b'{"a": 1}',  # insignificant whitespace is forbidden
    b'{"a":1}\n',  # trailing bytes are forbidden
    b'{"a":1,"a":2}',  # duplicate keys are forbidden
    b'{"a":1.0}',
    b'{"a":NaN}',
    b'"\\u96ea"',  # non-ASCII scalar must be encoded directly as UTF-8
    b'"\\u000a"',  # controls with a short escape must use that escape
    b'"\xff"',
]


@pytest.mark.parametrize("data", INVALID_BYTE_FIXTURES)
def test_invalid_canonical_byte_fixtures_fail_closed(data):
    with pytest.raises(CanonicalJsonError):
        parse_canonical_json_bytes(data)


@pytest.mark.parametrize(
    "data",
    [
        b"9007199254740992",
        b"-9007199254740992",
        b"9" * 100_000,
        b"-" + b"9" * 100_000,
    ],
)
def test_out_of_range_integer_text_is_rejected_before_bigint_conversion(data):
    with pytest.raises(CanonicalJsonError, match="interoperable range"):
        parse_canonical_json_bytes(data)


def test_container_depth_boundary_and_cycles_are_portable():
    at_limit = 0
    for _ in range(MAX_JSON_DEPTH):
        at_limit = [at_limit]
    encoded = canonical_json_bytes(at_limit)
    assert parse_canonical_json_bytes(encoded) == at_limit

    too_deep = [at_limit]
    with pytest.raises(CanonicalJsonError, match="maximum JSON container depth"):
        canonical_json_bytes(too_deep)
    serialized_too_deep = (
        b"[" * (MAX_JSON_DEPTH + 1)
        + b"0"
        + b"]" * (MAX_JSON_DEPTH + 1)
    )
    with pytest.raises(CanonicalJsonError, match="maximum JSON container depth"):
        parse_canonical_json_bytes(serialized_too_deep)

    cyclic = []
    cyclic.append(cyclic)
    with pytest.raises(CanonicalJsonError, match="reference cycle"):
        canonical_json_bytes(cyclic)


def test_domain_visible_ascii_boundaries():
    assert domain_digest("!", None)
    assert domain_digest("~", None)


@pytest.mark.parametrize("domain", ["", "contains space", "nul\x00byte", "snow-雪"])
def test_invalid_domain_fixtures_fail_closed(domain):
    with pytest.raises(CanonicalJsonError):
        domain_digest(domain, {"ok": True})
