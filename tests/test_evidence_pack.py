"""Bundle golden vectors, invalid fixtures, and atomic-write tests."""

from pathlib import Path

import json
import pytest

import spiritwriter.evidence.pack as pack_module
from spiritwriter.evidence import (
    EVIDENCE_NAME,
    MANIFEST_NAME,
    SOURCES_NAME,
    EvidencePack,
    EvidenceUnit,
    EvidenceValidationError,
    Locator,
    SourceSnapshot,
    canonical_json_bytes,
    sha256_hex,
)

SOURCE_CONTENT = b"# Fugaku\nLocated in Kobe.\n"
SOURCE_ID = "48ffcaf52fa30c7a357ce28532895359da4f8758b6ed96bc2149e6c785c1619e"
EVIDENCE_ID = "d2704a38cb8126836d0945fc8421424928b95d73147224c658c17f091efc3eb7"
PACK_ID = "e15b5a89b5a449da54a7ef834fcbeccc3370139a53720a1b56a71b511bf92085"
GOLDEN_MANIFEST = (
    b'{"contract_version":"1","files":{"evidence.jsonl":{"record_count":1,'
    b'"sha256":"f5c9e1a3fa51d79da51a97a9e30b852143a088f3f233dbd50d1dfc0372bcdfed"},'
    b'"sources.jsonl":{"record_count":1,"sha256":"7ed47b30e1e0babcefddec840b50ab31'
    b'ce624b5ffbdf4ac48e75c93b24dc6a05"}},"pack_id":"e15b5a89b5a449da54a7ef834fc'
    b'beccc3370139a53720a1b56a71b511bf92085"}'
)
GOLDEN_SOURCES_JSONL = (
    b'{"byte_length":26,"content_ref":"cas:example","logical_ref":"docs/fugaku.md",'
    b'"media_type":"text/markdown","raw_sha256":"02745eff3d74b9d82eb8226c3f230b9a'
    b'43bff943522e650e99c50c44ae5c233f","revision":"git:abc123","source_id":"'
    b'48ffcaf52fa30c7a357ce28532895359da4f8758b6ed96bc2149e6c785c1619e"}\n'
)
GOLDEN_EVIDENCE_JSONL = (
    b'{"evidence_id":"d2704a38cb8126836d0945fc8421424928b95d73147224c658c17f091efc3eb7",'
    b'"kind":"paragraph","locator":{"scheme":"markdown.block","value":{"heading":'
    b'["Fugaku"],"ordinal":1},"version":"1"},"normalized_excerpt":"Located in Kobe.",'
    b'"normalized_sha256":"4d97b17f2caacea684b28cf74e29ff3e6394fab5680e62c8bc9308f3a76cd511",'
    b'"payload":{"language":"en","ordinal":1},"source_id":"48ffcaf52fa30c7a357ce285'
    b'32895359da4f8758b6ed96bc2149e6c785c1619e"}\n'
)


def make_source(
    *, logical_ref: str = "docs/fugaku.md", content: bytes = SOURCE_CONTENT
) -> SourceSnapshot:
    return SourceSnapshot.from_content(
        logical_ref=logical_ref,
        revision="git:abc123",
        media_type="text/markdown",
        content_ref=f"cas:{logical_ref}",
        content=content,
    )


def make_unit(source_id: str = SOURCE_ID, excerpt: str = "Located in Kobe.") -> EvidenceUnit:
    return EvidenceUnit(
        source_id=source_id,
        kind="paragraph",
        locator=Locator(
            "markdown.block", "1", {"heading": ["Fugaku"], "ordinal": 1}
        ),
        normalized_excerpt=excerpt,
        normalized_sha256=sha256_hex(excerpt.encode("utf-8")),
        payload={"language": "en", "ordinal": 1},
    )


def make_pack() -> EvidencePack:
    source = SourceSnapshot.from_content(
        logical_ref="docs/fugaku.md",
        revision="git:abc123",
        media_type="text/markdown",
        content_ref="cas:example",
        content=SOURCE_CONTENT,
    )
    return EvidencePack(sources=[source], units=[make_unit(source.source_id)])


def test_pack_golden_identity_and_exact_serialization():
    pack = make_pack()
    assert pack.pack_id == PACK_ID
    assert pack.manifest_json_bytes == GOLDEN_MANIFEST
    assert pack.sources_jsonl_bytes == GOLDEN_SOURCES_JSONL
    assert pack.evidence_jsonl_bytes == GOLDEN_EVIDENCE_JSONL
    assert pack.verify().valid
    assert pack.verify().issues == ()


def test_pack_order_is_deterministic_regardless_of_input_order():
    first = make_source(logical_ref="a.md", content=b"a")
    second = make_source(logical_ref="b.md", content=b"b")
    first_unit = make_unit(first.source_id, "first")
    second_unit = make_unit(second.source_id, "second")
    forward = EvidencePack(
        sources=[first, second], units=[first_unit, second_unit]
    )
    reverse = EvidencePack(
        sources=[second, first], units=[second_unit, first_unit]
    )
    assert forward.pack_id == reverse.pack_id
    assert forward.sources_jsonl_bytes == reverse.sources_jsonl_bytes
    assert forward.evidence_jsonl_bytes == reverse.evidence_jsonl_bytes


@pytest.mark.parametrize(
    "sources, units, match",
    [
        (lambda source: [source, source], lambda source: [], "duplicate source_id"),
        (
            lambda source: [source],
            lambda source: [make_unit(source.source_id), make_unit(source.source_id)],
            "duplicate evidence_id",
        ),
        (
            lambda source: [source],
            lambda source: [make_unit("f" * 64)],
            "unknown source",
        ),
    ],
)
def test_invalid_pack_fixtures_fail_closed(sources, units, match):
    source = make_pack().sources[0]
    with pytest.raises(EvidenceValidationError, match=match):
        EvidencePack(sources=sources(source), units=units(source))


def test_atomic_write_round_trip_and_refuses_overwrite(tmp_path):
    pack = make_pack()
    destination = tmp_path / "pack"
    assert pack.write_atomic(destination) == destination
    assert (destination / MANIFEST_NAME).read_bytes() == GOLDEN_MANIFEST
    assert (destination / SOURCES_NAME).read_bytes() == GOLDEN_SOURCES_JSONL
    assert (destination / EVIDENCE_NAME).read_bytes() == GOLDEN_EVIDENCE_JSONL

    loaded = EvidencePack.from_directory(destination)
    assert loaded == pack
    assert loaded.pack_id == PACK_ID
    with pytest.raises(FileExistsError):
        pack.write_atomic(destination)


def test_atomic_write_cleans_temporary_directory_on_failure(tmp_path, monkeypatch):
    pack = make_pack()
    destination = tmp_path / "pack"
    real_write = pack_module._write_synced
    call_count = 0

    def fail_after_first_write(path: Path, data: bytes) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("fixture write failure")
        real_write(path, data)

    monkeypatch.setattr(pack_module, "_write_synced", fail_after_first_write)
    with pytest.raises(OSError, match="fixture write failure"):
        pack.write_atomic(destination)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_atomic_write_does_not_replace_destination_created_during_race(
    tmp_path, monkeypatch
):
    pack = make_pack()
    destination = tmp_path / "pack"
    real_rename = pack_module._rename_directory_noreplace

    def create_destination_then_rename(source: Path, target: Path) -> None:
        target.mkdir()
        real_rename(source, target)

    monkeypatch.setattr(
        pack_module, "_rename_directory_noreplace", create_destination_then_rename
    )
    with pytest.raises(FileExistsError):
        pack.write_atomic(destination)
    assert destination.is_dir()
    assert list(destination.iterdir()) == []
    assert [entry for entry in tmp_path.iterdir() if entry != destination] == []


def test_boolean_manifest_count_fixture_fails_closed(tmp_path):
    destination = tmp_path / "pack"
    make_pack().write_atomic(destination)
    manifest = json.loads((destination / MANIFEST_NAME).read_bytes())
    manifest["files"][SOURCES_NAME]["record_count"] = True
    manifest["files"][EVIDENCE_NAME]["record_count"] = True
    (destination / MANIFEST_NAME).write_bytes(canonical_json_bytes(manifest))
    with pytest.raises(EvidenceValidationError, match="record_count"):
        EvidencePack.from_directory(destination)


@pytest.mark.parametrize(
    "filename, replacement",
    [
        (SOURCES_NAME, b'{"not":"the source"}\n'),
        (EVIDENCE_NAME, GOLDEN_EVIDENCE_JSONL.rstrip(b"\n")),
        (MANIFEST_NAME, GOLDEN_MANIFEST + b"\n"),
    ],
)
def test_invalid_serialized_pack_fixtures_report_failure(
    tmp_path, filename, replacement
):
    destination = tmp_path / "pack"
    make_pack().write_atomic(destination)
    (destination / filename).write_bytes(replacement)
    report = EvidencePack.verify_directory(destination)
    assert not report.valid
    assert report.pack_id is None
    assert report.issues[0].code == "invalid_pack"
    with pytest.raises(EvidenceValidationError):
        report.raise_for_errors()


def test_serialized_symlink_fixture_fails_closed(tmp_path):
    destination = tmp_path / "pack"
    make_pack().write_atomic(destination)
    source_path = destination / SOURCES_NAME
    external = tmp_path / "external.jsonl"
    external.write_bytes(source_path.read_bytes())
    source_path.unlink()
    source_path.symlink_to(external)
    with pytest.raises(EvidenceValidationError, match="non-regular"):
        EvidencePack.from_directory(destination)


def test_extra_pack_entry_fixture_fails_closed(tmp_path):
    destination = tmp_path / "pack"
    make_pack().write_atomic(destination)
    (destination / "unauthenticated.txt").write_text("extra")
    with pytest.raises(EvidenceValidationError, match="unexpected"):
        EvidencePack.from_directory(destination)
