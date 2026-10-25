"""Bundle golden vectors, invalid fixtures, and atomic-write tests."""

from collections.abc import Mapping
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
    PublicationCapabilityError,
    PublicationDurabilityError,
    ResolvedSpan,
    SourceSnapshot,
    canonical_json_bytes,
    sha256_hex,
)

SOURCE_CONTENT = b"# Fugaku\nLocated in Kobe.\n"
SOURCE_ID = "48ffcaf52fa30c7a357ce28532895359da4f8758b6ed96bc2149e6c785c1619e"
EVIDENCE_ID = "30e527cafa1db2d794a1b3e2706b89154a1c9289b3dbc7b286a219310fbd4eca"
PACK_ID = "818870f176e6d479d633f298c881031d73763e6cd23c7cee244b9df0b04876b4"
GOLDEN_MANIFEST = (
    b'{"contract_version":"1","files":{"evidence.jsonl":{"record_count":1,'
    b'"sha256":"f3fce8c4b4184c281b7267268aa371193f86e3cad58f3886f97a14668e10789f"},'
    b'"sources.jsonl":{"record_count":1,"sha256":"7ed47b30e1e0babcefddec840b50ab31'
    b'ce624b5ffbdf4ac48e75c93b24dc6a05"}},"pack_id":"818870f176e6d479d633f298c881'
    b'031d73763e6cd23c7cee244b9df0b04876b4"}'
)
GOLDEN_SOURCES_JSONL = (
    b'{"byte_length":26,"content_ref":"cas:example","logical_ref":"docs/fugaku.md",'
    b'"media_type":"text/markdown","raw_sha256":"02745eff3d74b9d82eb8226c3f230b9a'
    b'43bff943522e650e99c50c44ae5c233f","revision":"git:abc123","source_id":"'
    b'48ffcaf52fa30c7a357ce28532895359da4f8758b6ed96bc2149e6c785c1619e"}\n'
)
GOLDEN_EVIDENCE_JSONL = (
    b'{"adapter_id":"markdown@1","evidence_id":"30e527cafa1db2d794a1b3e2706b89154a1c'
    b'9289b3dbc7b286a219310fbd4eca","kind":"paragraph","locator":{"scheme":"markdown.'
    b'block","value":{"heading":["Fugaku"],"ordinal":1},"version":"1"},"normalization_'
    b'profile":"text.v1","normalized_excerpt":"Located in Kobe.","normalized_sha256":"'
    b'4d97b17f2caacea684b28cf74e29ff3e6394fab5680e62c8bc9308f3a76cd511","payload"'
    b':{"language":"en","ordinal":1},"raw_span_sha256":"4d97b17f2caacea684b28cf74e29ff'
    b'3e6394fab5680e62c8bc9308f3a76cd511","source_id":"48ffcaf52fa30c7a357ce285328'
    b'95359da4f8758b6ed96bc2149e6c785c1619e"}\n'
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


def make_unit(
    source_id: str = SOURCE_ID,
    excerpt: str = "Located in Kobe.",
    *,
    adapter_id: str = "markdown@1",
    normalization_profile: str = "text.v1",
    include_raw_hash: bool = True,
) -> EvidenceUnit:
    return EvidenceUnit(
        source_id=source_id,
        kind="paragraph",
        adapter_id=adapter_id,
        normalization_profile=normalization_profile,
        locator=Locator(
            "markdown.block", "1", {"heading": ["Fugaku"], "ordinal": 1}
        ),
        normalized_excerpt=excerpt,
        normalized_sha256=sha256_hex(excerpt.encode("utf-8")),
        payload={"language": "en", "ordinal": 1},
        raw_span_sha256=(
            sha256_hex(excerpt.encode("utf-8")) if include_raw_hash else None
        ),
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


class ExampleResolver:
    locator_scheme = "markdown.block"
    locator_version = "1"

    def __init__(self, *, start_byte: int = 9, end_byte: int = 25) -> None:
        self.start_byte = start_byte
        self.end_byte = end_byte

    def resolve(self, source, locator, content):
        return ResolvedSpan(
            start_byte=self.start_byte,
            end_byte=self.end_byte,
        )


class ExampleNormalizer:
    normalization_profile = "text.v1"

    def __init__(self, normalized_excerpt=None) -> None:
        self.normalized_excerpt = normalized_excerpt

    def normalize(self, raw_span):
        if self.normalized_excerpt is not None:
            return self.normalized_excerpt
        return raw_span.decode("utf-8")


class ThrowingMetadataResolver:
    @property
    def locator_scheme(self):
        raise RuntimeError("fixture metadata failure")

    locator_version = "1"
    normalization_profile = "text.v1"

    def resolve(self, source, locator, content):
        raise AssertionError("must not be called")


class StatefulMetadataResolver(ExampleResolver):
    def __init__(self) -> None:
        super().__init__()
        self.scheme_reads = 0

    @property
    def locator_scheme(self):
        self.scheme_reads += 1
        if self.scheme_reads > 1:
            raise RuntimeError("locator_scheme was read more than once")
        return "markdown.block"


class ExplodingMapping(Mapping):
    def __getitem__(self, key):
        raise RuntimeError("fixture mapping failure")

    def __iter__(self):
        raise RuntimeError("fixture mapping failure")

    def __len__(self):
        return 1


def exploding_candidates():
    yield ExampleResolver()
    raise RuntimeError("fixture iterable failure")


def test_pack_golden_identity_and_exact_serialization():
    pack = make_pack()
    assert pack.pack_id == PACK_ID
    assert pack.manifest_json_bytes == GOLDEN_MANIFEST
    assert pack.sources_jsonl_bytes == GOLDEN_SOURCES_JSONL
    assert pack.evidence_jsonl_bytes == GOLDEN_EVIDENCE_JSONL
    assert pack.verify_structure().valid
    assert pack.verify_structure().issues == ()
    # The compatibility alias is explicitly structural-only.
    assert pack.verify() == pack.verify_structure()


def test_source_aware_verification_replays_locator_and_raw_span():
    pack = make_pack()
    report = pack.verify_with_sources(
        {pack.sources[0].source_id: SOURCE_CONTENT},
        [ExampleResolver()],
        [ExampleNormalizer()],
    )
    assert report.valid
    assert report.issues == ()


def test_source_aware_verification_snapshots_resolver_metadata_once():
    pack = make_pack()
    resolver = StatefulMetadataResolver()
    report = pack.verify_with_sources(
        {pack.sources[0].source_id: SOURCE_CONTENT},
        [resolver],
        [ExampleNormalizer()],
    )
    assert report.valid
    assert resolver.scheme_reads == 1


def test_source_aware_verification_reports_resolver_metadata_errors():
    pack = make_pack()
    report = pack.verify_with_sources(
        {pack.sources[0].source_id: SOURCE_CONTENT},
        [ThrowingMetadataResolver()],
        [ExampleNormalizer()],
    )
    assert not report.valid
    assert report.issues[0].code == "invalid_resolver"
    assert "fixture metadata failure" in report.issues[0].message


def test_source_aware_verification_contains_input_container_errors():
    pack = make_pack()
    mapping_report = pack.verify_with_sources(
        ExplodingMapping(),
        [ExampleResolver()],
        [ExampleNormalizer()],
    )
    assert [issue.code for issue in mapping_report.issues] == [
        "invalid_source_contents"
    ]

    resolver_report = pack.verify_with_sources(
        {pack.sources[0].source_id: SOURCE_CONTENT},
        exploding_candidates(),
        [ExampleNormalizer()],
    )
    assert [issue.code for issue in resolver_report.issues] == ["invalid_resolvers"]

    normalizer_report = pack.verify_with_sources(
        {pack.sources[0].source_id: SOURCE_CONTENT},
        [ExampleResolver()],
        exploding_candidates(),
    )
    assert [issue.code for issue in normalizer_report.issues] == [
        "invalid_normalizers"
    ]


@pytest.mark.parametrize(
    "contents, resolver, normalizer, expected_code",
    [
        ({}, ExampleResolver(), ExampleNormalizer(), "missing_source_content"),
        (
            {SOURCE_ID: b"# Fugaku\nLocated in Rome.\n"},
            ExampleResolver(),
            ExampleNormalizer(),
            "source_content_mismatch",
        ),
        (
            {SOURCE_ID: SOURCE_CONTENT},
            None,
            ExampleNormalizer(),
            "missing_resolver",
        ),
        (
            {SOURCE_ID: SOURCE_CONTENT},
            ExampleResolver(),
            ExampleNormalizer("Kobe"),
            "normalized_excerpt_mismatch",
        ),
        (
            {SOURCE_ID: SOURCE_CONTENT},
            ExampleResolver(end_byte=21),
            ExampleNormalizer(),
            "raw_span_hash_mismatch",
        ),
        (
            {SOURCE_ID: SOURCE_CONTENT},
            ExampleResolver(end_byte=100),
            ExampleNormalizer(),
            "resolved_span_out_of_bounds",
        ),
        ({SOURCE_ID: SOURCE_CONTENT}, ExampleResolver(), None, "missing_normalizer"),
    ],
)
def test_source_aware_verification_fails_closed(
    contents, resolver, normalizer, expected_code
):
    pack = make_pack()
    resolvers = [] if resolver is None else [resolver]
    normalizers = [] if normalizer is None else [normalizer]
    report = pack.verify_with_sources(contents, resolvers, normalizers)
    assert not report.valid
    assert expected_code in {issue.code for issue in report.issues}


def test_source_aware_verification_requires_raw_span_hash():
    source = make_source()
    pack = EvidencePack(
        sources=[source],
        units=[make_unit(source.source_id, include_raw_hash=False)],
    )
    assert pack.verify_structure().valid
    report = pack.verify_with_sources(
        {source.source_id: SOURCE_CONTENT},
        [ExampleResolver()],
        [ExampleNormalizer()],
    )
    assert not report.valid
    assert [issue.code for issue in report.issues] == ["missing_raw_span_hash"]


def test_extraction_profiles_participate_in_evidence_and_pack_identity():
    source = make_source()
    baseline = EvidencePack(
        sources=[source],
        units=[make_unit(source.source_id)],
    )
    changed_adapter = EvidencePack(
        sources=[source],
        units=[make_unit(source.source_id, adapter_id="markdown@2")],
    )
    changed_normalization = EvidencePack(
        sources=[source],
        units=[make_unit(source.source_id, normalization_profile="text.v2")],
    )
    assert len(
        {
            baseline.units[0].evidence_id,
            changed_adapter.units[0].evidence_id,
            changed_normalization.units[0].evidence_id,
        }
    ) == 3
    assert len(
        {baseline.pack_id, changed_adapter.pack_id, changed_normalization.pack_id}
    ) == 3


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
    assert EvidencePack.verify_directory_structure(destination).valid
    assert EvidencePack.verify_directory_with_sources(
        destination,
        {pack.sources[0].source_id: SOURCE_CONTENT},
        [ExampleResolver()],
        [ExampleNormalizer()],
    ).valid
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


def test_atomic_write_reports_post_rename_durability_uncertainty(
    tmp_path, monkeypatch
):
    pack = make_pack()
    destination = tmp_path / "pack"
    real_fsync_directory = pack_module._fsync_directory
    call_count = 0

    def fail_parent_fsync(path: Path) -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise OSError("fixture parent fsync failure")
        real_fsync_directory(path)

    monkeypatch.setattr(pack_module, "_fsync_directory", fail_parent_fsync)
    with pytest.raises(PublicationDurabilityError) as captured:
        pack.write_atomic(destination)
    assert captured.value.destination == destination
    assert captured.value.published is True
    assert captured.value.durability_confirmed is False
    assert EvidencePack.from_directory(destination) == pack
    assert list(tmp_path.iterdir()) == [destination]


def test_atomic_write_classifies_error_after_committed_rename(
    tmp_path, monkeypatch
):
    pack = make_pack()
    destination = tmp_path / "pack"
    real_rename = pack_module._rename_directory_noreplace

    def rename_then_fail(source: Path, target: Path) -> None:
        real_rename(source, target)
        raise OSError("fixture error after rename")

    monkeypatch.setattr(
        pack_module,
        "_rename_directory_noreplace",
        rename_then_fail,
    )
    with pytest.raises(PublicationDurabilityError) as captured:
        pack.write_atomic(destination)
    assert captured.value.published is True
    assert EvidencePack.from_directory(destination) == pack


def test_atomic_write_requires_existing_real_parent(tmp_path):
    destination = tmp_path / "missing" / "pack"
    with pytest.raises(FileNotFoundError, match="parent must be an existing"):
        make_pack().write_atomic(destination)
    assert not destination.parent.exists()


def test_atomic_write_exposes_no_replace_capability_failure(tmp_path, monkeypatch):
    pack = make_pack()
    destination = tmp_path / "pack"

    def unsupported(source: Path, target: Path) -> None:
        raise PublicationCapabilityError("fixture unsupported filesystem")

    monkeypatch.setattr(pack_module, "_rename_directory_noreplace", unsupported)
    with pytest.raises(PublicationCapabilityError, match="unsupported filesystem"):
        pack.write_atomic(destination)
    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_windows_publication_uses_write_through_no_replace_move(
    tmp_path, monkeypatch
):
    calls = []

    class FakeMoveFileEx:
        def __call__(self, source, target, flags):
            calls.append((source, target, flags))
            return 1

    class FakeKernel32:
        MoveFileExW = FakeMoveFileEx()

    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    monkeypatch.setattr(pack_module.sys, "platform", "win32")
    monkeypatch.setattr(pack_module.os, "name", "nt")
    monkeypatch.setattr(
        pack_module.ctypes,
        "WinDLL",
        lambda name, use_last_error: FakeKernel32(),
        raising=False,
    )
    pack_module._rename_directory_noreplace(source, target)
    assert calls == [(str(source), str(target), 0x00000008)]


def test_unsupported_platform_has_explicit_publication_capability_error(
    tmp_path, monkeypatch
):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    monkeypatch.setattr(pack_module.sys, "platform", "unsupported-os")
    monkeypatch.setattr(pack_module.os, "name", "posix")
    with pytest.raises(PublicationCapabilityError, match="unsupported-os"):
        pack_module._rename_directory_noreplace(source, target)
    assert source.is_dir()
    assert not target.exists()


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


def test_oversized_and_deep_manifest_fixtures_report_invalid_pack(tmp_path):
    for name, manifest_bytes in (
        ("oversized", b"x" * (pack_module.MAX_MANIFEST_BYTES + 1)),
        ("deep", b"[" * 65 + b"0" + b"]" * 65),
    ):
        destination = tmp_path / name
        destination.mkdir()
        (destination / MANIFEST_NAME).write_bytes(manifest_bytes)
        (destination / SOURCES_NAME).write_bytes(b"")
        (destination / EVIDENCE_NAME).write_bytes(b"")
        report = EvidencePack.verify_directory_structure(destination)
        assert not report.valid
        assert report.issues[0].code == "invalid_pack"


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
