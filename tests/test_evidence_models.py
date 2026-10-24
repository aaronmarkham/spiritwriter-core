"""Record identity, immutability, and strict-load tests."""

from dataclasses import FrozenInstanceError

import pytest

from spiritwriter.evidence import (
    MAX_SAFE_INTEGER,
    EvidenceUnit,
    EvidenceValidationError,
    Locator,
    LocatorResolver,
    ProtocolValidationError,
    SourceAdapter,
    SourceSnapshot,
    canonical_json_bytes,
    require_locator_resolver,
    require_source_adapter,
    sha256_hex,
)

SOURCE_CONTENT = b"# Fugaku\nLocated in Kobe.\n"
SOURCE_ID = "48ffcaf52fa30c7a357ce28532895359da4f8758b6ed96bc2149e6c785c1619e"
EVIDENCE_ID = "d2704a38cb8126836d0945fc8421424928b95d73147224c658c17f091efc3eb7"
SOURCE_JSON = (
    b'{"byte_length":26,"content_ref":"cas:example","logical_ref":"docs/fugaku.md",'
    b'"media_type":"text/markdown","raw_sha256":"02745eff3d74b9d82eb8226c3f230b9a'
    b'43bff943522e650e99c50c44ae5c233f","revision":"git:abc123","source_id":"'
    b'48ffcaf52fa30c7a357ce28532895359da4f8758b6ed96bc2149e6c785c1619e"}'
)
EVIDENCE_JSON = (
    b'{"evidence_id":"d2704a38cb8126836d0945fc8421424928b95d73147224c658c17f091efc3eb7",'
    b'"kind":"paragraph","locator":{"scheme":"markdown.block","value":{"heading":'
    b'["Fugaku"],"ordinal":1},"version":"1"},"normalized_excerpt":"Located in Kobe.",'
    b'"normalized_sha256":"4d97b17f2caacea684b28cf74e29ff3e6394fab5680e62c8bc9308f3a76cd511",'
    b'"payload":{"language":"en","ordinal":1},"source_id":"48ffcaf52fa30c7a357ce285'
    b'32895359da4f8758b6ed96bc2149e6c785c1619e"}'
)


def make_source() -> SourceSnapshot:
    return SourceSnapshot.from_content(
        logical_ref="docs/fugaku.md",
        revision="git:abc123",
        media_type="text/markdown",
        content_ref="cas:example",
        content=SOURCE_CONTENT,
    )


def make_unit(source_id: str = SOURCE_ID) -> EvidenceUnit:
    excerpt = "Located in Kobe."
    return EvidenceUnit(
        source_id=source_id,
        kind="paragraph",
        locator=Locator(
            scheme="markdown.block",
            version="1",
            value={"heading": ["Fugaku"], "ordinal": 1},
        ),
        normalized_excerpt=excerpt,
        normalized_sha256=sha256_hex(excerpt.encode("utf-8")),
        payload={"language": "en", "ordinal": 1},
    )


def test_source_snapshot_golden_identity_and_wire_bytes():
    source = make_source()
    assert source.source_id == SOURCE_ID
    assert canonical_json_bytes(source.to_dict()) == SOURCE_JSON
    assert SourceSnapshot.from_dict(source.to_dict()) == source
    source.verify_content(SOURCE_CONTENT)


def test_evidence_unit_golden_identity_and_wire_bytes():
    unit = make_unit()
    assert unit.evidence_id == EVIDENCE_ID
    assert canonical_json_bytes(unit.to_dict()) == EVIDENCE_JSON
    assert EvidenceUnit.from_dict(unit.to_dict()) == unit


def test_records_and_nested_json_are_immutable_from_callers():
    source = make_source()
    with pytest.raises(FrozenInstanceError):
        source.logical_ref = "changed"

    locator_value = {"path": ["before"]}
    payload = {"nested": ["before"]}
    unit = EvidenceUnit(
        source_id=source.source_id,
        kind="paragraph",
        locator=Locator("test", "1", locator_value),
        normalized_excerpt="text",
        normalized_sha256=sha256_hex(b"text"),
        payload=payload,
    )
    identity = unit.evidence_id
    locator_value["path"][0] = "after"
    payload["nested"][0] = "after"
    assert unit.locator.value == {"path": ["before"]}
    assert unit.payload == {"nested": ["before"]}

    detached = unit.payload
    detached["nested"][0] = "again"
    assert unit.payload == {"nested": ["before"]}
    assert unit.evidence_id == identity


@pytest.mark.parametrize(
    "field, replacement, match",
    [
        ("source_id", "0" * 64, "source_id mismatch"),
        ("raw_sha256", "not-a-digest", "raw_sha256"),
        ("byte_length", -1, "byte_length"),
    ],
)
def test_invalid_source_fixtures_fail_closed(field, replacement, match):
    record = make_source().to_dict()
    record[field] = replacement
    with pytest.raises(EvidenceValidationError, match=match):
        SourceSnapshot.from_dict(record)


def test_source_unknown_field_fixture_fails_closed():
    record = make_source().to_dict()
    record["policy"] = "must stay outside the kernel"
    with pytest.raises(EvidenceValidationError, match="unknown fields"):
        SourceSnapshot.from_dict(record)


def test_model_string_and_integer_portability_fixtures_fail_closed():
    source_record = make_source().to_dict()
    source_record["logical_ref"] = "bad\ud800ref"
    with pytest.raises(EvidenceValidationError, match="Unicode surrogate"):
        SourceSnapshot.from_dict(source_record)

    source_record = make_source().to_dict()
    source_record["byte_length"] = MAX_SAFE_INTEGER + 1
    with pytest.raises(EvidenceValidationError, match="byte_length"):
        SourceSnapshot.from_dict(source_record)

    with pytest.raises(EvidenceValidationError, match="Unicode surrogate"):
        Locator("bad\ud800scheme", "1", {})
    with pytest.raises(EvidenceValidationError, match="Unicode surrogate"):
        EvidenceUnit(
            source_id=SOURCE_ID,
            kind="bad\ud800kind",
            locator=Locator("test", "1", {}),
            normalized_excerpt="text",
            normalized_sha256=sha256_hex(b"text"),
            payload={},
        )
    with pytest.raises(EvidenceValidationError, match="Unicode surrogate"):
        EvidenceUnit(
            source_id=SOURCE_ID,
            kind="text",
            locator=Locator("test", "1", {}),
            normalized_excerpt="bad\ud800excerpt",
            normalized_sha256="0" * 64,
            payload={},
        )


def test_non_string_loader_field_name_fails_with_validation_error():
    record = make_source().to_dict()
    record[1] = "bad key"
    with pytest.raises(EvidenceValidationError, match="non-string field name"):
        SourceSnapshot.from_dict(record)


@pytest.mark.parametrize(
    "mutation, match",
    [
        (lambda record: record.update(evidence_id="0" * 64), "evidence_id mismatch"),
        (lambda record: record.update(normalized_excerpt="tampered"), "normalized_sha256"),
        (lambda record: record.update(raw_span_sha256="BAD"), "raw_span_sha256"),
        (lambda record: record.update(raw_span_sha256=None), "must be omitted"),
        (lambda record: record.update(payload={"score": 0.5}), "floats are not allowed"),
    ],
)
def test_invalid_evidence_fixtures_fail_closed(mutation, match):
    record = make_unit().to_dict()
    mutation(record)
    with pytest.raises((EvidenceValidationError, ValueError), match=match):
        EvidenceUnit.from_dict(record)


def test_source_content_integrity_mismatch_fails_closed():
    source = make_source()
    with pytest.raises(EvidenceValidationError, match="byte_length"):
        source.verify_content(b"short")
    with pytest.raises(EvidenceValidationError, match="raw_sha256"):
        source.verify_content(b"# Fugaku\nLocated in Rome.\n")


class ExampleAdapter:
    adapter_id = "example@1"

    def extract(self, source, content):
        return [make_unit(source.source_id)]


class ExampleResolver:
    locator_scheme = "markdown.block"
    locator_version = "1"

    def resolve(self, source, locator, content):
        return "Located in Kobe."


def test_protocols_are_runtime_checkable_and_structural():
    adapter = ExampleAdapter()
    resolver = ExampleResolver()
    assert isinstance(adapter, SourceAdapter)
    assert isinstance(resolver, LocatorResolver)
    assert require_source_adapter(adapter) is adapter
    assert require_locator_resolver(resolver) is resolver


@pytest.mark.parametrize(
    "candidate, validator, match",
    [
        (type("BadAdapterId", (), {"adapter_id": 1, "extract": lambda: None})(),
         require_source_adapter, "adapter_id"),
        (type("BadAdapterMethod", (), {"adapter_id": "x", "extract": "no"})(),
         require_source_adapter, "extract"),
        (type("BadResolverScheme", (), {
            "locator_scheme": 1, "locator_version": "1", "resolve": lambda: None
         })(), require_locator_resolver, "locator_scheme"),
        (type("BadResolverMethod", (), {
            "locator_scheme": "x", "locator_version": "1", "resolve": "no"
         })(), require_locator_resolver, "resolve"),
    ],
)
def test_runtime_protocol_validators_reject_invalid_members(candidate, validator, match):
    with pytest.raises(ProtocolValidationError, match=match):
        validator(candidate)
