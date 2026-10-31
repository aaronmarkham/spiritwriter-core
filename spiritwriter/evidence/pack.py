"""Deterministic evidence bundles with strict verification and atomic output."""

from __future__ import annotations

import ctypes
import errno
import os
import platform
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping, Optional, Union

from .canonical import (
    MAX_SAFE_INTEGER,
    CanonicalJsonError,
    JsonValue,
    canonical_json_bytes,
    domain_digest,
    parse_canonical_json_bytes,
    sha256_hex,
    validate_json_value,
)
from .models import EvidenceUnit, EvidenceValidationError, SourceSnapshot
from .protocols import (
    LocatorResolver,
    NormalizationProfile,
    ProtocolValidationError,
    ResolvedSpan,
    _bind_locator_resolver,
    _bind_normalizer,
)

PACK_DOMAIN = "spiritwriter.evidence.pack.v1"
CONTRACT_VERSION = "1"
MANIFEST_NAME = "manifest.json"
SOURCES_NAME = "sources.jsonl"
EVIDENCE_NAME = "evidence.jsonl"
MAX_MANIFEST_BYTES = 64 * 1024
MAX_PACK_DATA_FILE_BYTES = 256 * 1024 * 1024
MAX_JSONL_RECORD_BYTES = 8 * 1024 * 1024


class PublicationCapabilityError(OSError):
    """The platform or filesystem cannot publish with no-replace semantics."""

    def __init__(self, detail: str) -> None:
        super().__init__(errno.ENOTSUP, detail)


class PublicationDurabilityError(OSError):
    """Publication committed, but parent-directory durability is uncertain.

    The destination already names the complete pack. Callers must not retry at
    the same path as though publication had failed before the rename.
    """

    def __init__(self, destination: Path, cause: BaseException) -> None:
        super().__init__(
            getattr(cause, "errno", None),
            f"evidence pack was published at {destination}, but directory "
            f"durability could not be confirmed: {cause}",
            str(destination),
        )
        self.destination = destination
        self.published = True
        self.durability_confirmed = False


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
        if len(line) > MAX_JSONL_RECORD_BYTES:
            raise EvidenceValidationError(
                f"{record_name} JSONL line {line_number} exceeds "
                f"{MAX_JSONL_RECORD_BYTES} bytes"
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


def _read_bounded(path: Path, *, limit: int, name: str) -> bytes:
    with path.open("rb") as file_handle:
        data = file_handle.read(limit + 1)
    if len(data) > limit:
        raise EvidenceValidationError(f"{name} exceeds {limit} bytes")
    return data


def _write_synced(path: Path, data: bytes) -> None:
    with path.open("xb") as file_handle:
        file_handle.write(data)
        file_handle.flush()
        os.fsync(file_handle.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        # The Windows publication path uses MoveFileExW with
        # MOVEFILE_WRITE_THROUGH. Python cannot independently open and fsync a
        # directory there, so this helper is intentionally a no-op on Windows.
        return
    descriptor = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _unsupported_rename_error(error_number: int) -> bool:
    unsupported = {errno.EINVAL, errno.ENOSYS, errno.ENOTSUP}
    if hasattr(errno, "EOPNOTSUPP"):
        unsupported.add(errno.EOPNOTSUPP)
    return error_number in unsupported


def _raise_rename_error(source: Path, target: Path) -> None:
    error_number = ctypes.get_errno()
    if error_number in (errno.EEXIST, errno.ENOTEMPTY):
        raise FileExistsError(error_number, os.strerror(error_number), str(target))
    if _unsupported_rename_error(error_number):
        raise PublicationCapabilityError(
            f"atomic no-replace directory publication is unsupported for "
            f"{source.parent}: {os.strerror(error_number)}"
        )
    raise OSError(error_number, os.strerror(error_number), f"{source} -> {target}")


# renameat2(2) syscall numbers, used when libc has no renameat2() wrapper. The
# wrapper arrived in glibc 2.28; Amazon Linux 2 ships glibc 2.26 on kernels that
# do support the syscall.
_LINUX_RENAMEAT2_SYSCALLS = {
    "x86_64": 316,
    "amd64": 316,
    "aarch64": 276,
    "arm64": 276,
}


def _process_is_64bit() -> bool:
    """Whether this interpreter uses the 64-bit syscall ABI's pointer size.

    ``platform.machine()`` reports the kernel's architecture, so a 32-bit
    userspace on a 64-bit kernel (armhf on aarch64, x32 or i386 on x86_64)
    would otherwise pick a 64-bit syscall number that is wrong for its ABI.
    """

    return ctypes.sizeof(ctypes.c_void_p) == 8


def _linux_renameat2(libc):
    """Return a renameat2(olddirfd, oldpath, newdirfd, newpath, flags) callable."""

    wrapper = getattr(libc, "renameat2", None)
    if wrapper is not None:
        wrapper.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        wrapper.restype = ctypes.c_int
        return wrapper
    number = (
        _LINUX_RENAMEAT2_SYSCALLS.get(platform.machine().lower())
        if _process_is_64bit()
        else None
    )
    syscall = getattr(libc, "syscall", None)
    if number is None or syscall is None:
        raise PublicationCapabilityError(
            "atomic no-replace directory publication requires renameat2"
        )
    syscall.argtypes = [
        ctypes.c_long,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    syscall.restype = ctypes.c_long

    def renameat2(olddirfd, oldpath, newdirfd, newpath, flags):
        return syscall(number, olddirfd, oldpath, newdirfd, newpath, flags)

    return renameat2


def _rename_directory_noreplace(source: Path, target: Path) -> None:
    """Atomically rename a directory while refusing an existing target.

    No check-then-rename fallback is used because it would permit a concurrent
    publisher to be replaced. Unsupported platforms and filesystems receive a
    distinct capability error before publication is reported as successful.
    """

    if sys.platform == "darwin":
        renamex_np = getattr(ctypes.CDLL(None, use_errno=True), "renamex_np", None)
        if renamex_np is None:
            raise PublicationCapabilityError(
                "atomic no-replace directory publication requires renamex_np"
            )
        renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        renamex_np.restype = ctypes.c_int
        # RENAME_EXCL from <stdio.h>: fail if the destination exists.
        if renamex_np(os.fsencode(source), os.fsencode(target), 0x00000004) != 0:
            _raise_rename_error(source, target)
        return
    if sys.platform.startswith("linux"):
        renameat2 = _linux_renameat2(ctypes.CDLL(None, use_errno=True))
        # AT_FDCWD and RENAME_NOREPLACE from renameat2(2).
        if renameat2(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
            _raise_rename_error(source, target)
        return
    if os.name == "nt":
        # MOVEFILE_WRITE_THROUGH waits for the move metadata to reach disk;
        # omitting MOVEFILE_REPLACE_EXISTING preserves no-replace semantics.
        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            move_file_ex = kernel32.MoveFileExW
        except (AttributeError, OSError) as error:
            raise PublicationCapabilityError(
                "atomic durable no-replace publication requires MoveFileExW"
            ) from error
        move_file_ex.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
        move_file_ex.restype = ctypes.c_int
        if move_file_ex(str(source), str(target), 0x00000008) == 0:
            error_number = ctypes.get_last_error()
            if error_number in (80, 183):  # ERROR_FILE_EXISTS, ERROR_ALREADY_EXISTS
                raise FileExistsError(error_number, "destination exists", str(target))
            if error_number in (1, 50, 120):
                raise PublicationCapabilityError(
                    "filesystem does not support durable no-replace publication"
                )
            raise ctypes.WinError(error_number)
        return
    raise PublicationCapabilityError(
        f"atomic no-replace directory publication is unavailable on {sys.platform}"
    )


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

    def verify_structure(self) -> VerificationReport:
        """Verify record structure, IDs, references, order, and serialization.

        This check intentionally does not authenticate source content or prove
        that locators still resolve to the recorded excerpts. Use
        :meth:`verify_with_sources` when immutable source bytes are available.
        """

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

    def verify(self) -> VerificationReport:
        """Backward-compatible alias for structural-only verification."""

        return self.verify_structure()

    def _source_verification_report(
        self, issues: list[VerificationIssue]
    ) -> VerificationReport:
        return VerificationReport(
            valid=not issues,
            pack_id=self.pack_id,
            source_count=len(self.sources),
            evidence_count=len(self.units),
            issues=tuple(issues),
        )

    def verify_with_sources(
        self,
        source_contents: Mapping[str, bytes],
        resolvers: Iterable[LocatorResolver],
        normalizers: Iterable[NormalizationProfile],
    ) -> VerificationReport:
        """Verify structure and replay every locator against source bytes.

        Source bytes must be keyed by ``source_id``. Resolvers return byte
        ranges, which this method slices from authenticated source content.
        Normalizers are selected by each unit's normalization profile and are
        applied to those exact bytes. Full verification requires every unit to
        carry ``raw_span_sha256``.
        """

        structural = self.verify_structure()
        issues = list(structural.issues)
        if not isinstance(source_contents, Mapping):
            issues.append(
                VerificationIssue(
                    "invalid_source_contents",
                    "source_contents must be a mapping keyed by source_id",
                )
            )
            return self._source_verification_report(issues)
        try:
            source_content_snapshot = dict(source_contents)
        except Exception as error:
            issues.append(
                VerificationIssue(
                    "invalid_source_contents",
                    f"source contents could not be read: {type(error).__name__}: {error}",
                )
            )
            return self._source_verification_report(issues)

        try:
            resolver_candidates = tuple(resolvers)
        except Exception as error:
            issues.append(
                VerificationIssue(
                    "invalid_resolvers",
                    f"resolvers could not be read: {type(error).__name__}: {error}",
                )
            )
            return self._source_verification_report(issues)
        resolver_by_key: dict[tuple[str, str], LocatorResolver] = {}
        for candidate in resolver_candidates:
            try:
                binding = _bind_locator_resolver(candidate)
            except ProtocolValidationError as error:
                issues.append(VerificationIssue("invalid_resolver", str(error)))
                continue
            key = (binding.locator_scheme, binding.locator_version)
            if key in resolver_by_key:
                issues.append(
                    VerificationIssue(
                        "duplicate_resolver",
                        "duplicate resolver for " + "/".join(key),
                    )
                )
            else:
                resolver_by_key[key] = binding.resolver

        try:
            normalizer_candidates = tuple(normalizers)
        except Exception as error:
            issues.append(
                VerificationIssue(
                    "invalid_normalizers",
                    f"normalizers could not be read: {type(error).__name__}: {error}",
                )
            )
            return self._source_verification_report(issues)
        normalizer_by_profile: dict[str, NormalizationProfile] = {}
        for candidate in normalizer_candidates:
            try:
                binding = _bind_normalizer(candidate)
            except ProtocolValidationError as error:
                issues.append(VerificationIssue("invalid_normalizer", str(error)))
                continue
            profile = binding.normalization_profile
            if profile in normalizer_by_profile:
                issues.append(
                    VerificationIssue(
                        "duplicate_normalizer",
                        f"duplicate normalizer for {profile}",
                    )
                )
            else:
                normalizer_by_profile[profile] = binding.normalizer

        verified_content: dict[str, bytes] = {}
        for source in self.sources:
            if source.source_id not in source_content_snapshot:
                issues.append(
                    VerificationIssue(
                        "missing_source_content",
                        "source bytes were not provided",
                        source.source_id,
                    )
                )
                continue
            content = source_content_snapshot[source.source_id]
            if type(content) is not bytes:
                issues.append(
                    VerificationIssue(
                        "invalid_source_content",
                        "source content must be bytes",
                        source.source_id,
                    )
                )
                continue
            try:
                source.verify_content(content)
            except EvidenceValidationError as error:
                issues.append(
                    VerificationIssue(
                        "source_content_mismatch",
                        str(error),
                        source.source_id,
                    )
                )
                continue
            verified_content[source.source_id] = content

        source_by_id = {source.source_id: source for source in self.sources}
        for unit in self.units:
            content = verified_content.get(unit.source_id)
            if content is None:
                continue
            resolver_key = (unit.locator.scheme, unit.locator.version)
            resolver = resolver_by_key.get(resolver_key)
            if resolver is None:
                issues.append(
                    VerificationIssue(
                        "missing_resolver",
                        "no resolver for " + "/".join(resolver_key),
                        unit.evidence_id,
                    )
                )
                continue
            normalizer = normalizer_by_profile.get(unit.normalization_profile)
            if normalizer is None:
                issues.append(
                    VerificationIssue(
                        "missing_normalizer",
                        f"no normalizer for {unit.normalization_profile}",
                        unit.evidence_id,
                    )
                )
                continue
            try:
                resolved = resolver.resolve(
                    source_by_id[unit.source_id], unit.locator, content
                )
            except Exception as error:
                issues.append(
                    VerificationIssue(
                        "locator_resolution_failed",
                        f"{type(error).__name__}: {error}",
                        unit.evidence_id,
                    )
                )
                continue
            if type(resolved) is not ResolvedSpan:
                issues.append(
                    VerificationIssue(
                        "invalid_resolution",
                        "resolver must return ResolvedSpan",
                        unit.evidence_id,
                    )
                )
                continue
            if resolved.end_byte > len(content):
                issues.append(
                    VerificationIssue(
                        "resolved_span_out_of_bounds",
                        f"resolved byte range [{resolved.start_byte}, "
                        f"{resolved.end_byte}) exceeds source length {len(content)}",
                        unit.evidence_id,
                    )
                )
                continue
            raw_span = content[resolved.start_byte:resolved.end_byte]
            try:
                normalized_excerpt = normalizer.normalize(raw_span)
                if type(normalized_excerpt) is not str:
                    raise ProtocolValidationError(
                        "NormalizationProfile.normalize must return a string"
                    )
                validate_json_value(
                    normalized_excerpt,
                    path="NormalizationProfile.normalize result",
                )
            except (ProtocolValidationError, CanonicalJsonError) as error:
                issues.append(
                    VerificationIssue(
                        "normalization_failed",
                        str(error),
                        unit.evidence_id,
                    )
                )
                continue
            except Exception as error:
                issues.append(
                    VerificationIssue(
                        "normalization_failed",
                        f"{type(error).__name__}: {error}",
                        unit.evidence_id,
                    )
                )
                continue
            if normalized_excerpt != unit.normalized_excerpt:
                issues.append(
                    VerificationIssue(
                        "normalized_excerpt_mismatch",
                        "normalized source span does not match evidence",
                        unit.evidence_id,
                    )
                )
            resolved_normalized_sha256 = sha256_hex(
                normalized_excerpt.encode("utf-8")
            )
            if resolved_normalized_sha256 != unit.normalized_sha256:
                issues.append(
                    VerificationIssue(
                        "normalized_hash_mismatch",
                        "normalized source span hash does not match evidence",
                        unit.evidence_id,
                    )
                )
            if unit.raw_span_sha256 is None:
                issues.append(
                    VerificationIssue(
                        "missing_raw_span_hash",
                        "source-aware verification requires raw_span_sha256",
                        unit.evidence_id,
                    )
                )
            elif sha256_hex(raw_span) != unit.raw_span_sha256:
                issues.append(
                    VerificationIssue(
                        "raw_span_hash_mismatch",
                        "resolved source span hash does not match evidence",
                        unit.evidence_id,
                    )
                )

        return self._source_verification_report(issues)

    def write_atomic(self, destination: Union[Path, str]) -> Path:
        """Publish a complete new pack directory with one atomic rename.

        Existing destinations are never replaced. The destination parent must
        already exist as a real directory so its own directory entry is not a
        new, unsynchronized part of this transaction. This avoids exposing a
        mix of files from different pack versions.
        """

        target = Path(destination)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f"evidence pack destination already exists: {target}")
        if target.parent.is_symlink() or not target.parent.is_dir():
            raise FileNotFoundError(
                "evidence pack destination parent must be an existing real directory: "
                f"{target.parent}"
            )
        temporary = Path(
            tempfile.mkdtemp(prefix=f".{target.name}.tmp-", dir=str(target.parent))
        )
        published = False
        try:
            _write_synced(temporary / MANIFEST_NAME, self.manifest_json_bytes)
            _write_synced(temporary / SOURCES_NAME, self.sources_jsonl_bytes)
            _write_synced(temporary / EVIDENCE_NAME, self.evidence_jsonl_bytes)
            _fsync_directory(temporary)
            try:
                _rename_directory_noreplace(temporary, target)
            except BaseException as error:
                if not temporary.exists() and target.is_dir() and not target.is_symlink():
                    try:
                        destination_matches = (
                            EvidencePack.from_directory(target).pack_id == self.pack_id
                        )
                    except (EvidenceValidationError, OSError, ValueError):
                        destination_matches = False
                    if destination_matches:
                        raise PublicationDurabilityError(target, error) from error
                raise
            published = True
            try:
                _fsync_directory(target.parent)
            except BaseException as error:
                raise PublicationDurabilityError(target, error) from error
        except BaseException:
            if not published and temporary.exists():
                shutil.rmtree(temporary)
            raise
        return target

    @classmethod
    def from_directory(cls, directory: Union[Path, str]) -> "EvidencePack":
        """Load a pack only if every serialized byte verifies structurally."""

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
            manifest_bytes = _read_bounded(
                paths[MANIFEST_NAME],
                limit=MAX_MANIFEST_BYTES,
                name=MANIFEST_NAME,
            )
            manifest_value = parse_canonical_json_bytes(manifest_bytes)
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

        source_bytes = _read_bounded(
            paths[SOURCES_NAME],
            limit=MAX_PACK_DATA_FILE_BYTES,
            name=SOURCES_NAME,
        )
        evidence_bytes = _read_bounded(
            paths[EVIDENCE_NAME],
            limit=MAX_PACK_DATA_FILE_BYTES,
            name=EVIDENCE_NAME,
        )
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
        if manifest_bytes != pack.manifest_json_bytes:
            raise EvidenceValidationError("manifest or pack_id does not match pack content")
        if source_bytes != pack.sources_jsonl_bytes:
            raise EvidenceValidationError("sources are not in canonical ID order")
        if evidence_bytes != pack.evidence_jsonl_bytes:
            raise EvidenceValidationError("evidence units are not in canonical ID order")
        return pack

    @classmethod
    def verify_directory_structure(cls, directory: Union[Path, str]) -> VerificationReport:
        """Structurally verify a serialized pack without source bytes."""

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
        return pack.verify_structure()

    @classmethod
    def verify_directory(cls, directory: Union[Path, str]) -> VerificationReport:
        """Backward-compatible alias for structural directory verification."""

        return cls.verify_directory_structure(directory)

    @classmethod
    def verify_directory_with_sources(
        cls,
        directory: Union[Path, str],
        source_contents: Mapping[str, bytes],
        resolvers: Iterable[LocatorResolver],
        normalizers: Iterable[NormalizationProfile],
    ) -> VerificationReport:
        """Load a serialized pack, then replay locators against source bytes."""

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
        return pack.verify_with_sources(source_contents, resolvers, normalizers)
