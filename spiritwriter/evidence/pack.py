"""Deterministic evidence bundles with strict verification and atomic output."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from .canonical import (
    MAX_SAFE_INTEGER,
    JsonValue,
    canonical_json_bytes,
    domain_digest,
    parse_canonical_json_bytes,
    sha256_hex,
)
from .models import EvidenceUnit, EvidenceValidationError, SourceSnapshot

PACK_DOMAIN = "spiritwriter.evidence.pack.v1"
CONTRACT_VERSION = "1"
MANIFEST_NAME = "manifest.json"
SOURCES_NAME = "sources.jsonl"
EVIDENCE_NAME = "evidence.jsonl"


@dataclass(frozen=True)
class VerificationIssue:
    """One deterministic verification failure."""

    code: str
    message: str
    record_id: Optional[str] = None


@dataclass(frozen=True)
class VerificationReport:
    """Result of checking a pack or serialized pack directory."""

    valid: bool
    pack_id: Optional[str]
    source_count: int
    evidence_count: int
    issues: tuple[VerificationIssue, ...] = field(default_factory=tuple)

    def raise_for_errors(self) -> None:
        """Raise one validation error containing every reported issue."""

        if not self.valid:
            detail = "; ".join(f"{issue.code}: {issue.message}" for issue in self.issues)
            raise EvidenceValidationError(detail)


def _canonical_jsonl(records: Iterable[dict[str, JsonValue]]) -> bytes:
    encoded = [canonical_json_bytes(record) for record in records]
    return b"" if not encoded else b"\n".join(encoded) + b"\n"


def _parse_canonical_jsonl(data: bytes, record_name: str) -> list[dict[str, JsonValue]]:
    if type(data) is not bytes:
        raise EvidenceValidationError(f"{record_name} JSONL must be bytes")
    if not data:
        return []
    if not data.endswith(b"\n"):
        raise EvidenceValidationError(f"{record_name} JSONL must end with LF")
    records: list[dict[str, JsonValue]] = []
    for line_number, line in enumerate(data[:-1].split(b"\n"), start=1):
        if not line:
            raise EvidenceValidationError(
                f"{record_name} JSONL contains a blank line at {line_number}"
            )
        try:
            value = parse_canonical_json_bytes(line)
        except ValueError as error:
            raise EvidenceValidationError(
                f"invalid {record_name} JSONL line {line_number}: {error}"
            ) from error
        if type(value) is not dict:
            raise EvidenceValidationError(
                f"{record_name} JSONL line {line_number} must be an object"
            )
        records.append(value)
    return records


def _write_synced(path: Path, data: bytes) -> None:
    with path.open("xb") as file_handle:
        file_handle.write(data)
        file_handle.flush()
        os.fsync(file_handle.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # Windows does not allow opening a directory through os.open.
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _raise_rename_error(source: Path, target: Path) -> None:
    error_number = ctypes.get_errno()
    if error_number in (errno.EEXIST, errno.ENOTEMPTY):
        raise FileExistsError(error_number, os.strerror(error_number), str(target))
    raise OSError(error_number, os.strerror(error_number), f"{source} -> {target}")


def _rename_directory_noreplace(source: Path, target: Path) -> None:
    """Atomically rename a directory while refusing an existing target."""

    if sys.platform == "darwin":
        renamex_np = ctypes.CDLL(None, use_errno=True).renamex_np
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        # RENAME_EXCL from <stdio.h>: fail if the destination exists.
        if renamex_np(os.fsencode(source), os.fsencode(target), 0x00000004) != 0:
            _raise_rename_error(source, target)
        return
    if sys.platform.startswith("linux"):
        libc = ctypes.CDLL(None, use_errno=True)
        renameat2 = getattr(libc, "renameat2", None)
        if renameat2 is None:
            raise OSError(errno.ENOTSUP, "atomic no-replace rename is unavailable")
        renameat2.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        renameat2.restype = ctypes.c_int
        # AT_FDCWD and RENAME_NOREPLACE from renameat2(2).
        if renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
            _raise_rename_error(source, target)
        return
    if os.name == "nt":
        # os.rename uses MoveFileEx without MOVEFILE_REPLACE_EXISTING on Windows.
        os.rename(source, target)
        return
    raise OSError(errno.ENOTSUP, "atomic no-replace directory rename is unavailable")


@dataclass(frozen=True, init=False)
class EvidencePack:
    """A validated, deterministically serialized collection of evidence."""

    sources: tuple[SourceSnapshot, ...]
    units: tuple[EvidenceUnit, ...]

    def __init__(
        self,
        *,
        sources: Iterable[SourceSnapshot],
        units: Iterable[EvidenceUnit],
    ) -> None:
        source_values = tuple(sources)
        unit_values = tuple(units)
        for source in source_values:
            if type(source) is not SourceSnapshot:
                raise EvidenceValidationError("sources must contain SourceSnapshot records")
        for unit in unit_values:
            if type(unit) is not EvidenceUnit:
                raise EvidenceValidationError("units must contain EvidenceUnit records")

        source_ids = [source.source_id for source in source_values]
        duplicate_sources = sorted(
            source_id for source_id, count in Counter(source_ids).items() if count > 1
        )
        if duplicate_sources:
            raise EvidenceValidationError(
                f"duplicate source_id: {', '.join(duplicate_sources)}"
            )

        evidence_ids = [unit.evidence_id for unit in unit_values]
        duplicate_units = sorted(
            evidence_id
            for evidence_id, count in Counter(evidence_ids).items()
            if count > 1
        )
        if duplicate_units:
            raise EvidenceValidationError(
                f"duplicate evidence_id: {', '.join(duplicate_units)}"
            )

        known_sources = set(source_ids)
        dangling = sorted(
            f"{unit.evidence_id}->{unit.source_id}"
            for unit in unit_values
            if unit.source_id not in known_sources
        )
        if dangling:
            raise EvidenceValidationError(
                f"evidence references unknown source: {', '.join(dangling)}"
            )

        object.__setattr__(
            self, "sources", tuple(sorted(source_values, key=lambda source: source.source_id))
        )
        object.__setattr__(
            self, "units", tuple(sorted(unit_values, key=lambda unit: unit.evidence_id))
        )

    @property
    def sources_jsonl_bytes(self) -> bytes:
        return _canonical_jsonl(source.to_dict() for source in self.sources)

    @property
    def evidence_jsonl_bytes(self) -> bytes:
        return _canonical_jsonl(unit.to_dict() for unit in self.units)

    def _manifest_identity(self) -> dict[str, JsonValue]:
        sources_bytes = self.sources_jsonl_bytes
        evidence_bytes = self.evidence_jsonl_bytes
        return {
            "contract_version": CONTRACT_VERSION,
            "files": {
                EVIDENCE_NAME: {
                    "record_count": len(self.units),
                    "sha256": sha256_hex(evidence_bytes),
                },
                SOURCES_NAME: {
                    "record_count": len(self.sources),
                    "sha256": sha256_hex(sources_bytes),
                },
            },
        }

    @property
    def pack_id(self) -> str:
        """Content address of the canonical manifest identity."""

        return domain_digest(PACK_DOMAIN, self._manifest_identity())

    def manifest_dict(self) -> dict[str, JsonValue]:
        return {"pack_id": self.pack_id, **self._manifest_identity()}

    @property
    def manifest_json_bytes(self) -> bytes:
        return canonical_json_bytes(self.manifest_dict())

    def verify(self) -> VerificationReport:
        """Recheck IDs, references, hashes, ordering, and serialization."""

        issues: list[VerificationIssue] = []
        source_ids: set[str] = set()
        for source in self.sources:
            if source.source_id in source_ids:
                issues.append(
                    VerificationIssue("duplicate_source", "duplicate source ID", source.source_id)
                )
            source_ids.add(source.source_id)
            try:
                SourceSnapshot.from_dict(source.to_dict())
            except EvidenceValidationError as error:
                issues.append(
                    VerificationIssue("invalid_source", str(error), source.source_id)
                )

        evidence_ids: set[str] = set()
        for unit in self.units:
            if unit.evidence_id in evidence_ids:
                issues.append(
                    VerificationIssue("duplicate_evidence", "duplicate evidence ID", unit.evidence_id)
                )
            evidence_ids.add(unit.evidence_id)
            if unit.source_id not in source_ids:
                issues.append(
                    VerificationIssue(
                        "unknown_source",
                        f"unknown source ID {unit.source_id}",
                        unit.evidence_id,
                    )
                )
            try:
                EvidenceUnit.from_dict(unit.to_dict())
            except EvidenceValidationError as error:
                issues.append(
                    VerificationIssue("invalid_evidence", str(error), unit.evidence_id)
                )

        if list(self.sources) != sorted(self.sources, key=lambda item: item.source_id):
            issues.append(VerificationIssue("source_order", "sources are not ID-sorted"))
        if list(self.units) != sorted(self.units, key=lambda item: item.evidence_id):
            issues.append(VerificationIssue("evidence_order", "evidence units are not ID-sorted"))

        return VerificationReport(
            valid=not issues,
            pack_id=self.pack_id,
            source_count=len(self.sources),
            evidence_count=len(self.units),
            issues=tuple(issues),
        )

    def write_atomic(self, destination: Path | str) -> Path:
        """Publish a complete new pack directory with one atomic rename.

        Existing destinations are never replaced. This avoids exposing a mix
        of files from different pack versions and makes the operation safe to
        retry under a new destination name.
        """

        target = Path(destination)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"evidence pack destination already exists: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
        )
        try:
            _write_synced(temporary / MANIFEST_NAME, self.manifest_json_bytes)
            _write_synced(temporary / SOURCES_NAME, self.sources_jsonl_bytes)
            _write_synced(temporary / EVIDENCE_NAME, self.evidence_jsonl_bytes)
            _fsync_directory(temporary)
            _rename_directory_noreplace(temporary, target)
            _fsync_directory(target.parent)
        except BaseException:
            if temporary.exists():
                shutil.rmtree(temporary)
            raise
        return target

    @classmethod
    def from_directory(cls, directory: Path | str) -> "EvidencePack":
        """Load a pack only if every byte and record verifies strictly."""

        root = Path(directory)
        if root.is_symlink() or not root.is_dir():
            raise EvidenceValidationError("evidence pack path must be a real directory")
        expected_names = {MANIFEST_NAME, SOURCES_NAME, EVIDENCE_NAME}
        actual_names = {entry.name for entry in root.iterdir()}
        if actual_names != expected_names:
            missing = sorted(expected_names - actual_names)
            extra = sorted(actual_names - expected_names)
            details = []
            if missing:
                details.append(f"missing: {', '.join(missing)}")
            if extra:
                details.append(f"unexpected: {', '.join(extra)}")
            raise EvidenceValidationError(
                f"pack directory entries do not match contract ({'; '.join(details)})"
            )
        paths = {
            name: root / name
            for name in (MANIFEST_NAME, SOURCES_NAME, EVIDENCE_NAME)
        }
        for name, path in paths.items():
            if path.is_symlink() or not path.is_file():
                raise EvidenceValidationError(f"missing or non-regular pack file: {name}")

        try:
            manifest_value = parse_canonical_json_bytes(
                paths[MANIFEST_NAME].read_bytes()
            )
        except ValueError as error:
            raise EvidenceValidationError(f"invalid canonical manifest: {error}") from error
        if type(manifest_value) is not dict:
            raise EvidenceValidationError("manifest must be a JSON object")
        if set(manifest_value) != {"contract_version", "files", "pack_id"}:
            raise EvidenceValidationError("manifest fields do not match contract version 1")
        if manifest_value["contract_version"] != CONTRACT_VERSION:
            raise EvidenceValidationError("unsupported evidence pack contract version")
        files = manifest_value["files"]
        if type(files) is not dict or set(files) != {SOURCES_NAME, EVIDENCE_NAME}:
            raise EvidenceValidationError("manifest file set does not match contract version 1")

        source_bytes = paths[SOURCES_NAME].read_bytes()
        evidence_bytes = paths[EVIDENCE_NAME].read_bytes()
        raw_by_name = {SOURCES_NAME: source_bytes, EVIDENCE_NAME: evidence_bytes}
        for name, data in raw_by_name.items():
            entry = files[name]
            if type(entry) is not dict or set(entry) != {"record_count", "sha256"}:
                raise EvidenceValidationError(f"invalid manifest entry for {name}")
            count = entry["record_count"]
            if type(count) is not int or count < 0 or count > MAX_SAFE_INTEGER:
                raise EvidenceValidationError(
                    f"manifest record_count for {name} must be a non-negative integer"
                )
            digest = entry["sha256"]
            if type(digest) is not str or digest != sha256_hex(data):
                raise EvidenceValidationError(f"manifest hash mismatch for {name}")

        source_records = _parse_canonical_jsonl(source_bytes, "source")
        evidence_records = _parse_canonical_jsonl(evidence_bytes, "evidence")
        if files[SOURCES_NAME]["record_count"] != len(source_records):
            raise EvidenceValidationError("source record count mismatch")
        if files[EVIDENCE_NAME]["record_count"] != len(evidence_records):
            raise EvidenceValidationError("evidence record count mismatch")

        sources = tuple(SourceSnapshot.from_dict(record) for record in source_records)
        units = tuple(EvidenceUnit.from_dict(record) for record in evidence_records)
        pack = cls(sources=sources, units=units)
        if paths[MANIFEST_NAME].read_bytes() != pack.manifest_json_bytes:
            raise EvidenceValidationError("manifest or pack_id does not match pack content")
        if source_bytes != pack.sources_jsonl_bytes:
            raise EvidenceValidationError("sources are not in canonical ID order")
        if evidence_bytes != pack.evidence_jsonl_bytes:
            raise EvidenceValidationError("evidence units are not in canonical ID order")
        return pack

    @classmethod
    def verify_directory(cls, directory: Path | str) -> VerificationReport:
        """Return a report instead of raising for a serialized pack."""

        try:
            pack = cls.from_directory(directory)
        except (EvidenceValidationError, OSError, ValueError) as error:
            return VerificationReport(
                valid=False,
                pack_id=None,
                source_count=0,
                evidence_count=0,
                issues=(VerificationIssue("invalid_pack", str(error)),),
            )
        return pack.verify()
