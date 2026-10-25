"""Shard store — local cache with DHT-ready interface.

Storage layout (content-addressed, like git objects):
    shards/
        ab/
            cd1234...json    # shard file, first 2 chars as directory
        index.json           # scope → [shard_id] mapping for fast lookup
        refs/                # named refs (like git branches)
            project-csp.ref  # pointer to latest shard for a scope

Local-first: everything is files. DHT layer wraps this with
network resolution when a shard_id isn't found locally.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import re
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from spiritwriter.fabric.shard import (
    MemoryShard, ShardRef, DecayClass, validate_shard_id,
)
from spiritwriter.fabric.crypto import EncryptedShard, encrypt_shard, decrypt_shard, DecryptionError
from spiritwriter.fabric.entitlement import (
    EntitlementToken, validate_capability, validate_scope,
    is_expired, get_shard_key, Capability,
)
from spiritwriter.fabric.network import IntegrityError, NetworkResolver

try:  # POSIX
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

logger = logging.getLogger(__name__)

# Temp files left by a killed writer are only swept once this old, so a live
# writer's in-flight temp file is never removed.
_STALE_TMP_SECS = 3600

# Seconds to wait for the index lock on Windows before giving up.
_WIN_LOCK_TIMEOUT = 30.0


class _LockState:
    """Per-lock-file reentrancy state, shared by every store on that file."""

    def __init__(self) -> None:
        self.guard = threading.RLock()  # serializes threads in this process
        self.depth = 0  # reentrancy depth of the thread holding ``guard``


# Keyed by resolved lock path so two ShardStore instances on one root share
# state (flock is per open file description; a second instance in the same
# thread would otherwise block on its own process's lock).
_LOCK_STATES: dict[str, _LockState] = {}
_LOCK_STATES_GUARD = threading.Lock()


def _lock_state(lock_path: Path) -> _LockState:
    key = os.path.realpath(lock_path)
    with _LOCK_STATES_GUARD:
        state = _LOCK_STATES.get(key)
        if state is None:
            state = _LOCK_STATES[key] = _LockState()
        return state


def _reset_lock_states() -> None:
    # A forked child inherits whatever another thread held at fork time and
    # can never release it, so start from a clean registry.
    global _LOCK_STATES_GUARD
    _LOCK_STATES_GUARD = threading.Lock()
    _LOCK_STATES.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_lock_states)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: temp file, fsync, os.replace.

    Readers see the old or the new content, never a partial file. The temp
    file is created with mode 0o666 so the kernel applies the process umask
    itself (reading the umask directly means ``os.umask`` set-and-restore,
    which is not thread-safe). An existing file's mode is preserved.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        mode = os.stat(path).st_mode & 0o7777
    except OSError:
        mode = None
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            if mode is not None and hasattr(os, "fchmod"):
                os.fchmod(f.fileno(), mode)
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _fsync_dir(path.parent)


def _fsync_dir(directory: Path) -> None:
    """Persist a rename by fsyncing its directory. Best-effort: a no-op
    where directories can't be opened or synced (e.g. Windows)."""
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _sweep_stale_tmp(directory: Path, pattern: str) -> None:
    """Remove temp files in ``directory`` older than ``_STALE_TMP_SECS``."""
    cutoff = time.time() - _STALE_TMP_SECS
    try:
        candidates = list(directory.glob(pattern))
    except OSError:
        return
    for tmp in candidates:
        try:
            if tmp.stat().st_mtime < cutoff:
                tmp.unlink()
        except OSError:
            pass


# Chars disallowed in Windows filenames (NTFS/FAT), plus control chars and
# `%` itself (so encoded names round-trip unambiguously). Names using only
# portable characters are stored as-is — existing refs on Linux/macOS keep
# their old on-disk form. Only problematic names get percent-encoded.
_ILLEGAL_REF_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f%]')
_PCT_DECODE_RE = re.compile(r'%([0-9A-Fa-f]{2})')

# Reserved Windows device names — illegal as filenames regardless of
# extension (CON.ref, com1.anything, etc.). Matched case-insensitively
# against the part before the first dot.
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


def _encode_ref_name(name: str) -> str:
    """Encode a ref name for safe use as a filename on all platforms.

    Handles three Windows-specific constraints:
      - illegal characters (`<>:"/\\|?*`, control chars) → percent-encoded.
      - reserved device names (`CON`, `PRN`, `COM1`, ...) → first char of
        the stem is percent-encoded so the resulting filename no longer
        matches the reservation.
      - trailing `.` or space → the offending char is percent-encoded so
        Windows doesn't silently strip it at the filesystem layer.
    `%` is in the illegal set so every encoding roundtrips unambiguously.
    """
    encoded = _ILLEGAL_REF_CHARS_RE.sub(
        lambda m: f"%{ord(m.group(0)):02X}", name
    )
    if encoded:
        stem = encoded.split(".", 1)[0]
        if stem.upper() in _WINDOWS_RESERVED:
            encoded = f"%{ord(encoded[0]):02X}{encoded[1:]}"
        if encoded[-1] in ". ":
            encoded = f"{encoded[:-1]}%{ord(encoded[-1]):02X}"
    return encoded


def _decode_ref_name(encoded: str) -> str:
    """Reverse of _encode_ref_name — recover the original ref name."""
    return _PCT_DECODE_RE.sub(
        lambda m: chr(int(m.group(1), 16)), encoded
    )


class ShardStore:
    """Content-addressed shard storage.

    Local file-based store that mirrors git's object storage pattern.
    Designed to be wrapped by a DHT resolver for distributed access.
    """

    def __init__(self, root: str | Path, resolver: NetworkResolver | None = None):
        self.root = Path(root)
        self.shards_dir = self.root / "shards"
        self.refs_dir = self.root / "refs"
        self.index_path = self.root / "index.json"
        self._resolver = resolver
        self._lock_path = self.root / "index.json.lock"
        self.shards_dir.mkdir(parents=True, exist_ok=True)
        self._assert_shards_dir()
        self.refs_dir.mkdir(parents=True, exist_ok=True)

    def _assert_shards_dir(self) -> None:
        """Reject a replaced or symlinked top-level object directory."""
        try:
            mode = os.lstat(self.shards_dir).st_mode
        except OSError as exc:
            raise ValueError(
                f"Shard object directory is unavailable: {self.shards_dir}"
            ) from exc
        if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
            raise ValueError(
                f"Shard object directory must be a real directory, not a symlink: "
                f"{self.shards_dir}"
            )

    @property
    def _secure_dir_fd_supported(self) -> bool:
        """Whether this OS supports the descriptor/no-follow object path."""
        return (
            os.name == "posix"
            and hasattr(os, "O_DIRECTORY")
            and hasattr(os, "O_NOFOLLOW")
            and os.open in os.supports_dir_fd
            and os.mkdir in os.supports_dir_fd
            and os.unlink in os.supports_dir_fd
            and os.link in os.supports_dir_fd
        )

    @contextmanager
    def _shards_fd(self) -> Iterator[int | None]:
        """Open the object root without following a final symlink."""
        self._assert_shards_dir()
        if not self._secure_dir_fd_supported:
            yield None
            return
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            fd = os.open(self.shards_dir, flags)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError(
                    f"Shard object directory is unsafe: {self.shards_dir}"
                ) from exc
            raise
        try:
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _prefix_fd(
        self, shard_id: str, *, create: bool = False
    ) -> Iterator[int | None]:
        """Open one object prefix directory without following symlinks."""
        shard_id = validate_shard_id(shard_id)
        prefix = shard_id[:2]
        with self._shards_fd() as root_fd:
            if root_fd is None:
                path = self.shards_dir / prefix
                if create:
                    path.mkdir(parents=False, exist_ok=True)
                if not path.exists():
                    yield None
                    return
                if path.is_symlink() or not path.is_dir():
                    raise ValueError(f"Unsafe shard prefix directory: {path}")
                yield None
                return

            if create:
                try:
                    os.mkdir(prefix, mode=0o777, dir_fd=root_fd)
                except FileExistsError:
                    pass
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            try:
                prefix_fd = os.open(prefix, flags, dir_fd=root_fd)
            except FileNotFoundError:
                yield None
                return
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise ValueError(
                        f"Unsafe shard prefix directory: {self.shards_dir / prefix}"
                    ) from exc
                raise
            try:
                yield prefix_fd
            finally:
                os.close(prefix_fd)

    @staticmethod
    def _object_name(shard_id: str, suffix: str) -> str:
        return f"{shard_id[2:]}{suffix}"

    def _content_path(self, shard_id: str, suffix: str) -> Path:
        """Return the lexical path for a validated shard identifier.

        Actual object I/O uses directory descriptors and ``O_NOFOLLOW`` where
        the platform supports them. The portable fallback must not call
        ``resolve()`` on the object itself because that would hide a final
        symlink before the caller can reject it.
        """
        shard_id = validate_shard_id(shard_id)
        self._assert_shards_dir()
        object_root = self.shards_dir.absolute()
        prefix = object_root / shard_id[:2]
        try:
            mode = os.lstat(prefix).st_mode
        except FileNotFoundError:
            pass
        else:
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ValueError(f"Unsafe shard prefix directory: {prefix}")
        return prefix / self._object_name(shard_id, suffix)

    def _shard_path(self, shard_id: str) -> Path:
        """Git-style object path: ab/cd1234...json."""
        return self._content_path(shard_id, ".json")

    def _read_at(self, prefix_fd: int, name: str) -> bytes:
        """Read one regular file through a held prefix descriptor."""
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=prefix_fd)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise ValueError(f"Unsafe shard object path: {name}") from exc
            raise
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError(f"Shard object is not a regular file: {name}")
            with os.fdopen(fd, "rb", closefd=False) as f:
                return f.read()
        finally:
            os.close(fd)

    def _read_object_bytes(self, shard_id: str, suffix: str) -> bytes | None:
        """Read an object without following object or prefix symlinks."""
        shard_id = validate_shard_id(shard_id)
        name = self._object_name(shard_id, suffix)
        with self._prefix_fd(shard_id) as prefix_fd:
            if self._secure_dir_fd_supported:
                if prefix_fd is None:
                    return None
                try:
                    return self._read_at(prefix_fd, name)
                except FileNotFoundError:
                    return None

            path = self._content_path(shard_id, suffix)
            try:
                mode = os.lstat(path).st_mode
            except FileNotFoundError:
                return None
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise ValueError(f"Unsafe shard object path: {path}")
            return path.read_bytes()

    def _object_exists(self, shard_id: str, suffix: str) -> bool:
        return self._read_object_bytes(shard_id, suffix) is not None

    def _write_temp_at(self, prefix_fd: int, name: str, text: str) -> str:
        """Write and fsync a uniquely named temporary object."""
        tmp_name = f"{name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp"
        fd = os.open(
            tmp_name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o666,
            dir_fd=prefix_fd,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", closefd=False) as f:
                f.write(text)
                f.flush()
                os.fsync(fd)
        except BaseException:
            try:
                os.unlink(tmp_name, dir_fd=prefix_fd)
            except FileNotFoundError:
                pass
            raise
        finally:
            os.close(fd)
        return tmp_name

    def _store_immutable_text(self, shard_id: str, suffix: str, text: str) -> None:
        """Create an immutable object or verify an identical existing one.

        On POSIX, a fully-written temp file is atomically hard-linked into its
        final name through a held prefix-directory descriptor. The link cannot
        replace an existing name and neither path can be redirected by a
        symlink race. Portable platforms retain the lock + atomic-replace path.
        """
        shard_id = validate_shard_id(shard_id)
        expected = text.encode("utf-8")
        name = self._object_name(shard_id, suffix)
        with self._prefix_fd(shard_id, create=True) as prefix_fd:
            if self._secure_dir_fd_supported:
                assert prefix_fd is not None
                tmp_name = self._write_temp_at(prefix_fd, name, text)
                try:
                    try:
                        os.link(
                            tmp_name,
                            name,
                            src_dir_fd=prefix_fd,
                            dst_dir_fd=prefix_fd,
                            follow_symlinks=False,
                        )
                        os.fsync(prefix_fd)
                        return
                    except FileExistsError:
                        existing = self._read_at(prefix_fd, name)
                finally:
                    try:
                        os.unlink(tmp_name, dir_fd=prefix_fd)
                    except FileNotFoundError:
                        pass
            else:
                path = self._content_path(shard_id, suffix)
                try:
                    existing = path.read_bytes()
                except FileNotFoundError:
                    _atomic_write_text(path, text)
                    return
        if existing != expected:
            raise IntegrityError(
                f"Content-address conflict for shard {shard_id}: existing bytes differ"
            )

    def _replace_object_text(self, shard_id: str, suffix: str, text: str) -> None:
        """Atomically replace an existing object under the store lock."""
        shard_id = validate_shard_id(shard_id)
        name = self._object_name(shard_id, suffix)
        with self._prefix_fd(shard_id) as prefix_fd:
            if self._secure_dir_fd_supported:
                if prefix_fd is None:
                    raise FileNotFoundError(name)
                tmp_name = self._write_temp_at(prefix_fd, name, text)
                try:
                    os.replace(
                        tmp_name,
                        name,
                        src_dir_fd=prefix_fd,
                        dst_dir_fd=prefix_fd,
                    )
                    os.fsync(prefix_fd)
                except BaseException:
                    try:
                        os.unlink(tmp_name, dir_fd=prefix_fd)
                    except FileNotFoundError:
                        pass
                    raise
                return

            path = self._content_path(shard_id, suffix)
            if not path.exists():
                raise FileNotFoundError(path)
            _atomic_write_text(path, text)

    def _unlink_object(self, shard_id: str, suffix: str) -> bool:
        """Unlink an object by descriptor without following symlinks."""
        shard_id = validate_shard_id(shard_id)
        name = self._object_name(shard_id, suffix)
        with self._prefix_fd(shard_id) as prefix_fd:
            if self._secure_dir_fd_supported:
                if prefix_fd is None:
                    return False
                try:
                    os.unlink(name, dir_fd=prefix_fd)
                    os.fsync(prefix_fd)
                    return True
                except FileNotFoundError:
                    return False
            path = self._content_path(shard_id, suffix)
            try:
                path.unlink()
                _fsync_dir(path.parent)
                return True
            except FileNotFoundError:
                return False

    @contextmanager
    def _index_lock(self) -> Iterator[None]:
        """Exclusive advisory lock around index read-modify-write cycles.

        Serializes writers across processes and threads via a sidecar
        ``index.json.lock`` file: ``fcntl.flock`` on POSIX, ``msvcrt.locking``
        on Windows. Reentrant per lock file within a thread, so a mutator may
        call another and several stores on one root can nest. The lock file
        is never deleted — removing it would let two processes lock
        different inodes.
        """
        state = _lock_state(self._lock_path)
        with state.guard:
            if state.depth:
                state.depth += 1
                try:
                    yield
                finally:
                    state.depth -= 1
                return
            with open(self._lock_path, "a+b") as f:
                if fcntl is not None:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                else:  # pragma: no cover - Windows
                    self._win_lock(f)
                state.depth = 1
                try:
                    yield
                finally:
                    state.depth = 0
                    if fcntl is not None:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    else:  # pragma: no cover - Windows
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

    def _win_lock(self, f) -> None:  # pragma: no cover - Windows
        """Lock byte 0 of ``f``. Retry only while another process holds it."""
        deadline = time.monotonic() + _WIN_LOCK_TIMEOUT
        while True:
            try:
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                return
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Timed out after {_WIN_LOCK_TIMEOUT:.0f}s waiting "
                        f"for shard index lock {self._lock_path}"
                    ) from exc
                time.sleep(0.05)

    def _read_index(self) -> dict[str, list[str]] | None:
        """Parse index.json. ``None`` if absent; ``ValueError`` if corrupt.

        ``ValueError`` covers ``JSONDecodeError`` and ``UnicodeDecodeError``
        (a torn write can end mid-character); anything that isn't a JSON
        object is also corrupt.
        """
        try:
            raw = self.index_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"index is {type(data).__name__}, expected object")
        return data

    def _load_index(self) -> dict[str, list[str]]:
        """Load scope → [shard_id] index.

        A corrupt index (e.g. torn by a writer from before atomic saves) is
        rebuilt from the shard files in memory only: readers take no lock
        and never write, so a read-only store still works. Write paths
        persist the healed index when they save; :meth:`reindex` persists it
        explicitly.
        """
        try:
            index = self._read_index()
        except ValueError:
            logger.warning(
                "Shard index %s is corrupt; rebuilding in memory from shard "
                "files (run reindex() to persist)",
                self.index_path,
            )
            return self._scan_index()
        return {} if index is None else index

    def _decode_plaintext(self, shard_id: str, raw: bytes) -> MemoryShard:
        """Parse a plaintext object and normalize integrity failures."""
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if not isinstance(parsed, dict):
                raise TypeError("payload is not a JSON object")
            declared_id = parsed.get("shard_id")
            if declared_id != shard_id:
                raise IntegrityError(
                    f"Plaintext shard ID mismatch: requested {shard_id}, "
                    f"payload declares {declared_id!r}"
                )
            shard = MemoryShard.from_dict(parsed)
        except IntegrityError:
            raise
        except Exception as exc:
            raise IntegrityError(
                f"Plaintext shard {shard_id} is malformed or fails its content address: {exc}"
            ) from exc
        if shard.shard_id != shard_id:
            raise IntegrityError(
                f"Plaintext shard content mismatch: requested {shard_id}, "
                f"computed {shard.shard_id}"
            )
        return shard

    def _decode_encrypted(self, shard_id: str, raw: bytes) -> EncryptedShard:
        """Parse an encrypted wrapper and normalize identity failures."""
        try:
            encrypted = EncryptedShard.from_json(raw.decode("utf-8"))
        except Exception as exc:
            raise IntegrityError(
                f"Encrypted shard {shard_id} is malformed: {exc}"
            ) from exc
        if encrypted.shard_id != shard_id:
            raise IntegrityError(
                f"Encrypted shard ID mismatch: requested {shard_id}, "
                f"payload declares {encrypted.shard_id}"
            )
        return encrypted

    def _decode_sealed(self, shard_id: str, raw: bytes):
        """Parse a sealed wrapper and normalize identity failures."""
        from spiritwriter.fabric.sealed import SealedShard
        try:
            sealed = SealedShard.from_json(raw.decode("utf-8"))
        except Exception as exc:
            raise IntegrityError(f"Sealed shard {shard_id} is malformed: {exc}") from exc
        if sealed.shard_id != shard_id:
            raise IntegrityError(
                f"Sealed shard ID mismatch: requested {shard_id}, "
                f"payload declares {sealed.shard_id}"
            )
        return sealed

    @staticmethod
    def _split_object_name(name: str) -> tuple[str, str] | None:
        """Return ``(id_tail, suffix)`` for a recognized object filename."""
        for suffix in (".sealed.json", ".enc.json", ".json"):
            if name.endswith(suffix):
                return name[:-len(suffix)], suffix
        return None

    def _scan_index(
        self,
        order: dict[str, list[str]] | None = None,
        skipped: list[str] | None = None,
    ) -> dict[str, list[str]]:
        """Reconstruct the index by securely scanning shard files.

        Plaintext, encrypted and sealed shards all carry a top-level
        ``scope``. Malformed object names, unsafe paths, invalid payloads and
        filename/payload identity mismatches are omitted and appended to
        ``skipped`` when the caller asks for a report. Ids that appear in
        ``order`` keep that order; the rest are appended sorted.
        """
        found: dict[str, set[str]] = {}

        def report(path: str, reason: object) -> None:
            if skipped is not None:
                skipped.append(f"{path}: {reason}")

        with self._shards_fd() as root_fd:
            if self._secure_dir_fd_supported:
                assert root_fd is not None
                prefixes = sorted(os.listdir(root_fd))
                for prefix in prefixes:
                    try:
                        prefix_fd = os.open(
                            prefix,
                            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=root_fd,
                        )
                    except OSError as exc:
                        report(f"{prefix}/", f"unsafe or unreadable prefix ({exc})")
                        continue
                    try:
                        names = sorted(os.listdir(prefix_fd))
                        for name in names:
                            split = self._split_object_name(name)
                            if split is None:
                                continue
                            tail, suffix = split
                            shard_id = prefix + tail
                            relpath = f"{prefix}/{name}"
                            try:
                                validate_shard_id(shard_id)
                            except ValueError as exc:
                                report(relpath, exc)
                                continue
                            try:
                                raw = self._read_at(prefix_fd, name)
                                if suffix == ".json":
                                    scope = self._decode_plaintext(shard_id, raw).scope
                                elif suffix == ".enc.json":
                                    scope = self._decode_encrypted(shard_id, raw).scope
                                else:
                                    scope = self._decode_sealed(shard_id, raw).scope
                                if not isinstance(scope, str):
                                    raise ValueError("scope is not a string")
                            except Exception as exc:
                                report(relpath, exc)
                                continue
                            found.setdefault(scope, set()).add(shard_id)
                    finally:
                        os.close(prefix_fd)
            else:
                for prefix_path in sorted(self.shards_dir.iterdir()):
                    if prefix_path.is_symlink() or not prefix_path.is_dir():
                        report(f"{prefix_path.name}/", "unsafe or unreadable prefix")
                        continue
                    for object_path in sorted(prefix_path.iterdir()):
                        split = self._split_object_name(object_path.name)
                        if split is None:
                            continue
                        tail, suffix = split
                        shard_id = prefix_path.name + tail
                        relpath = f"{prefix_path.name}/{object_path.name}"
                        try:
                            validate_shard_id(shard_id)
                            if object_path.is_symlink() or not object_path.is_file():
                                raise ValueError("object is not a regular file")
                            raw = object_path.read_bytes()
                            if suffix == ".json":
                                scope = self._decode_plaintext(shard_id, raw).scope
                            elif suffix == ".enc.json":
                                scope = self._decode_encrypted(shard_id, raw).scope
                            else:
                                scope = self._decode_sealed(shard_id, raw).scope
                            if not isinstance(scope, str):
                                raise ValueError("scope is not a string")
                        except Exception as exc:
                            report(relpath, exc)
                            continue
                        found.setdefault(scope, set()).add(shard_id)

        index: dict[str, list[str]] = {}
        for scope, ids in found.items():
            kept = [i for i in (order or {}).get(scope, []) if i in ids]
            kept_set = set(kept)
            index[scope] = kept + sorted(ids - kept_set)
        return index

    def _save_index(self, index: dict[str, list[str]]) -> None:
        """Write the index atomically (see :func:`_atomic_write_text`)."""
        _atomic_write_text(
            self.index_path, json.dumps(index, indent=2, ensure_ascii=False)
        )
        _sweep_stale_tmp(self.root, "index.json.*.tmp")

    def _index_add(self, scope: str, shard_id: str) -> None:
        """Ensure ``shard_id`` is listed under ``scope``. Idempotent."""
        with self._index_lock():
            index = self._load_index()
            scope_shards = index.setdefault(scope, [])
            if shard_id not in scope_shards:
                scope_shards.append(shard_id)
                self._save_index(index)
            elif self._index_needs_heal():
                self._save_index(index)

    def _index_needs_heal(self) -> bool:
        """True if index.json is missing or unparseable on disk."""
        try:
            return self._read_index() is None
        except ValueError:
            return True

    def reindex(self) -> dict[str, list[str]]:
        """Rebuild the index from the shard files on disk and persist it.

        Heals a store whose index lost entries (old concurrent-writer bug,
        crash windows, hand edits) or is corrupt. Ids still present keep
        their existing order. Returns ``{"added": [...], "removed": [...],
        "skipped": [...]}``; skipped entries include malformed IDs, unsafe
        paths, invalid payloads, and content-address mismatches. Logs a summary.
        """
        with self._index_lock():
            try:
                old = self._read_index() or {}
            except ValueError:
                old = {}
            skipped: list[str] = []
            new = self._scan_index(order=old, skipped=skipped)
            old_ids = {i for ids in old.values() for i in ids}
            new_ids = {i for ids in new.values() for i in ids}
            added = sorted(new_ids - old_ids)
            removed = sorted(old_ids - new_ids)
            self._save_index(new)
        if added or removed or skipped:
            logger.warning(
                "Reindexed %s: added %d, removed %d, skipped %d",
                self.index_path, len(added), len(removed), len(skipped),
            )
            for item in skipped:
                logger.warning("Reindex skipped %s", item)
        return {"added": added, "removed": removed, "skipped": skipped}

    def verify_index(self) -> dict[str, list[str]]:
        """Compare the index with the shard files without changing anything.

        Returns ``{"missing": [...], "dangling": [...]}``: ids on disk but
        not indexed, and indexed ids with no file. Both empty means
        consistent. This scans every shard file, so it is an explicit,
        opt-in check; ``count()`` and other hot paths do not call it.
        """
        try:
            indexed = self._read_index() or {}
        except ValueError:
            indexed = {}
        actual = self._scan_index()
        have = {i for ids in indexed.values() for i in ids}
        want = {i for ids in actual.values() for i in ids}
        return {"missing": sorted(want - have), "dangling": sorted(have - want)}

    # === Core Operations ===

    def put(self, shard: MemoryShard) -> ShardRef:
        """Store a shard. Returns its ref. Idempotent for identical bytes."""
        shard_id = validate_shard_id(shard.shard_id)
        serialized = shard.to_json()

        # One lock covers immutable object creation and index publication. A
        # colliding envelope with the same legacy shard_id is rejected rather
        # than silently keeping or replacing different bytes.
        with self._index_lock():
            self._store_immutable_text(shard_id, ".json", serialized)
            self._index_add(shard.scope, shard_id)

        return shard.ref

    def get(self, shard_id: str) -> MemoryShard | None:
        """Retrieve a shard by content address.

        L1: local file. L2: network fallback (if a resolver is configured).

        Returns ``None`` ONLY on genuine absence (no local file and, when a
        resolver is configured, the resolver reports the shard was never
        published). A transport/config failure from the resolver —
        ``NetworkUnavailable`` / ``NetworkTimeout``, or ``S3ConfigurationError``
        from the S3 backend — PROPAGATES; it is never collapsed into ``None``,
        so a transient failure is not mistaken for "absent". A caller that must
        not treat a transient failure as missing (e.g. a hosted worker
        resolving through this API) should let the error fail the operation
        rather than proceed as if the data were gone.
        """
        # L1: local file
        shard_id = validate_shard_id(shard_id)
        raw = self._read_object_bytes(shard_id, ".json")
        if raw is not None:
            return self._decode_plaintext(shard_id, raw)

        # L2: network fallback
        if self._resolver:
            shard = self._resolver.resolve(shard_id)
            if shard:
                self.put(shard)  # cache locally
                return shard

        return None

    def has(self, shard_id: str) -> bool:
        """Check if a shard exists locally."""
        return self._object_exists(shard_id, ".json")

    def delete(self, shard_id: str) -> bool:
        """Remove a shard. Returns True if it existed."""
        shard_id = validate_shard_id(shard_id)
        if not self._object_exists(shard_id, ".json"):
            return False
        # Update the index first and unlink only once that succeeded, so a
        # failure never leaves a dangling index entry. Errors propagate.
        with self._index_lock():
            index = self._load_index()
            changed = False
            for scope in list(index):
                if shard_id in index[scope]:
                    index[scope].remove(shard_id)
                    changed = True
                    if not index[scope]:
                        del index[scope]
            if changed:
                self._save_index(index)
            self._unlink_object(shard_id, ".json")
        return True

    # === Query Operations ===

    def resolve(self, ref: ShardRef) -> MemoryShard | None:
        """Resolve a shard reference to its full content.

        Delegates to :meth:`get`, so it inherits the same contract: ``None``
        only on genuine absence; a transport/config failure PROPAGATES rather
        than collapsing to ``None``.
        """
        return self.get(ref.shard_id)

    def resolve_many(self, refs: list[ShardRef]) -> list[MemoryShard]:
        """Resolve multiple refs, skipping any that are genuinely absent.

        Inherits :meth:`get`'s contract: a genuinely-absent shard is skipped,
        but a transport/config failure PROPAGATES — the batch fails loud rather
        than returning a misleadingly-partial list that hides an unreachable
        backend.
        """
        shards = []
        for ref in refs:
            shard = self.get(ref.shard_id)
            if shard is not None:
                shards.append(shard)
        return shards

    def hydrate(self, refs: list[ShardRef]) -> str:
        """Resolve refs and render as injectable context.

        This is the main entry point for agent context hydration:
        sub-agent receives refs → calls hydrate() → gets context string.
        """
        shards = self.resolve_many(refs)
        if not shards:
            return ""
        parts = [s.hydrate_context() for s in shards]
        return "\n".join(parts)

    def by_scope(self, scope: str) -> list[MemoryShard]:
        """Get all shards in a scope.

        Skips genuinely-absent shards; a transport/config failure from the
        resolver PROPAGATES (see :meth:`get`) rather than yielding a partial set.
        """
        index = self._load_index()
        shard_ids = index.get(scope, [])
        shards = []
        for sid in shard_ids:
            shard = self.get(sid)
            if shard is not None:
                shards.append(shard)
        return shards

    def by_entity(self, entity_key: str) -> list[MemoryShard]:
        """Get all shards (any scope) with at least one atom for this entity.

        Lets multiple producers each contribute atoms about the same entity
        (e.g. ``article:{sha256(url)}``) without duplicating each other's
        content; consumers merge atoms across the returned shards at the
        entity level.

        Scans all plaintext shards via ``iter_all()``, which explicitly
        skips encrypted (``.enc.json``) and sealed (``.sealed.json``)
        payloads by filename — entity lives in atom content and is
        unreadable without the relevant key.
        """
        return [
            shard for shard in self.iter_all()
            if any(atom.entity == entity_key for atom in shard.atoms)
        ]

    def list_scopes(self) -> list[str]:
        """List all scopes that have shards."""
        return list(self._load_index().keys())

    def iter_all(self) -> Iterator[MemoryShard]:
        """Iterate all valid plaintext shards in the store.

        The secure scanner omits encrypted/sealed-only identifiers and unsafe,
        malformed, or content-address-mismatched objects.
        """
        index = self._scan_index()
        for shard_ids in index.values():
            for shard_id in shard_ids:
                raw = self._read_object_bytes(shard_id, ".json")
                if raw is None:
                    continue
                try:
                    yield self._decode_plaintext(shard_id, raw)
                except IntegrityError:
                    continue

    def count(self) -> int:
        """Total shards in store."""
        return sum(
            len(sids) for sids in self._load_index().values()
        )

    # === Named Refs (like git branches) ===

    def _ref_path(self, name: str) -> Path:
        """Build the on-disk path for a ref, encoding unsafe chars."""
        return self.refs_dir / f"{_encode_ref_name(name)}.ref"

    def set_ref(self, name: str, shard_id: str) -> None:
        """Atomically set a named reference to a validated shard identifier."""
        shard_id = validate_shard_id(shard_id)
        with self._index_lock():
            _atomic_write_text(self._ref_path(name), shard_id)

    def compare_and_set_ref(
        self,
        name: str,
        expected_shard_id: str | None,
        new_shard_id: str,
    ) -> bool:
        """Set ``name`` only if its current value matches ``expected``.

        ``expected_shard_id=None`` means the ref must not exist. Returns
        ``True`` when updated and ``False`` on a stale expectation.
        """
        if expected_shard_id is not None:
            expected_shard_id = validate_shard_id(expected_shard_id)
        new_shard_id = validate_shard_id(new_shard_id)
        with self._index_lock():
            current = self.get_ref(name)
            if current != expected_shard_id:
                return False
            _atomic_write_text(self._ref_path(name), new_shard_id)
            return True

    def get_ref(self, name: str) -> str | None:
        """Resolve a named reference to a validated shard_id."""
        ref_path = self._ref_path(name)
        if ref_path.exists():
            return validate_shard_id(ref_path.read_text(encoding="utf-8").strip())
        return None

    def resolve_ref(self, name: str) -> MemoryShard | None:
        """Resolve a named ref to its full shard.

        A missing ref returns ``None``. When the ref exists, resolution
        delegates to :meth:`get`, inheriting its contract: ``None`` only on
        genuine absence of the target shard; a transport/config failure
        PROPAGATES rather than collapsing to ``None``.
        """
        shard_id = self.get_ref(name)
        if shard_id:
            return self.get(shard_id)
        return None

    def delete_ref(self, name: str) -> bool:
        """Remove a named ref under the ref-update lock."""
        ref_path = self._ref_path(name)
        with self._index_lock():
            if ref_path.exists():
                ref_path.unlink()
                _fsync_dir(ref_path.parent)
                return True
            return False

    def list_refs(self, prefix: str = "") -> list[str]:
        """List all named refs, optionally filtered by prefix.

        Ref filenames are percent-decoded on the way out. Note: a pre-existing
        ref on Linux/macOS whose raw filename happens to contain a `%HH`
        sequence (e.g. ``already%3Aencoded.ref``) will now be decoded when
        listed. Such names are vanishingly rare in practice; when produced by
        this API post-fix, literal `%` is always escaped so roundtrip is safe.
        """
        refs = [_decode_ref_name(p.stem) for p in self.refs_dir.glob("*.ref")]
        if prefix:
            refs = [r for r in refs if r.startswith(prefix)]
        return sorted(refs)

    def move_scope(self, shard_id: str, new_scope: str) -> MemoryShard:
        """Create a new shard with updated scope (shards are immutable).

        Stores the new shard and returns it. The original shard
        remains in the store under its original scope.
        """
        shard = self.get(shard_id)
        if shard is None:
            raise KeyError(f"Shard {shard_id} not found")
        new_shard = MemoryShard(
            atoms=shard.atoms,
            scope=new_scope,
            origin=shard.origin,
            decay_class=shard.decay_class,
            created_at=shard.created_at,
            trace_ref=shard.trace_ref,
            parent_shard_id=shard_id,  # link to original
            tags=shard.tags,
            meta=shard.meta,
        )
        self.put(new_shard)
        return new_shard

    # === Maintenance ===

    def prune_expired(self) -> int:
        """Remove shards whose decay class has expired.

        TTL is measured from last_checked if set (actively polled shards
        stay alive), otherwise from created_at.

        Returns count of pruned shards.
        """
        import time
        now = time.time()
        pruned = 0
        ttls = {
            DecayClass.STABLE: 90 * 86400,
            DecayClass.ACTIVE: 14 * 86400,
            DecayClass.SESSION: 86400,
            DecayClass.CHECKPOINT: 4 * 3600,
        }

        for shard in list(self.iter_all()):
            ttl = ttls.get(shard.decay_class)
            if ttl is None:
                continue  # permanent — never prune
            try:
                from datetime import datetime, timezone
                # Use last_checked if available (polled shards stay alive),
                # otherwise fall back to created_at
                anchor = shard.last_checked or shard.created_at
                anchor_ts = datetime.fromisoformat(
                    anchor.replace("Z", "+00:00")
                ).timestamp()
                if now - anchor_ts > ttl:
                    self.delete(shard.shard_id)
                    pruned += 1
            except Exception:
                continue

        return pruned

    # === Encrypted Shard Operations ===

    def _encrypted_path(self, shard_id: str) -> Path:
        """Path for encrypted shard: same layout, .enc.json suffix."""
        return self._content_path(shard_id, ".enc.json")

    def put_encrypted(self, encrypted: EncryptedShard) -> str:
        """Store an encrypted shard, rejecting bytes that conflict by ID."""
        shard_id = validate_shard_id(encrypted.shard_id)
        with self._index_lock():
            self._store_immutable_text(shard_id, ".enc.json", encrypted.to_json())
            self._index_add(encrypted.scope, shard_id)
        return shard_id

    def get_encrypted(self, shard_id: str) -> EncryptedShard | None:
        """Retrieve an encrypted shard by id. Falls back to network.

        Returns ``None`` ONLY on genuine absence. A transport/config failure
        from the resolver (``NetworkUnavailable`` / ``NetworkTimeout``, or
        ``S3ConfigurationError`` from the S3 backend) PROPAGATES — it is never
        collapsed into ``None``, so a transient failure is not mistaken for
        "absent". See :meth:`get` for the full contract.
        """
        shard_id = validate_shard_id(shard_id)
        raw = self._read_object_bytes(shard_id, ".enc.json")
        if raw is not None:
            return self._decode_encrypted(shard_id, raw)

        if self._resolver:
            encrypted = self._resolver.resolve_encrypted(shard_id)
            if encrypted:
                self.put_encrypted(encrypted)  # cache locally
                return encrypted

        return None

    def has_encrypted(self, shard_id: str) -> bool:
        """Check if an encrypted shard exists."""
        return self._object_exists(shard_id, ".enc.json")

    def encrypt_and_store(self, shard: MemoryShard, key: bytes) -> EncryptedShard:
        """Encrypt and store a shard, returning the existing envelope on retry.

        AES-GCM uses a random nonce, so encrypting the same plaintext twice
        produces different bytes. The store lock makes this helper idempotent:
        a repeated call with the same inner shard and key returns the original
        authenticated envelope instead of manufacturing a content-address
        conflict. A different envelope under the same legacy shard ID remains
        an integrity error.
        """
        shard_id = validate_shard_id(shard.shard_id)
        with self._index_lock():
            raw = self._read_object_bytes(shard_id, ".enc.json")
            if raw is not None:
                existing = self._decode_encrypted(shard_id, raw)
                existing_shard = decrypt_shard(existing, key)
                if existing_shard.to_json() != shard.to_json():
                    raise IntegrityError(
                        f"Encrypted shard {shard_id} already stores a different envelope"
                    )
                self._index_add(existing.scope, shard_id)
                return existing
            encrypted = encrypt_shard(shard, key)
            self._store_immutable_text(shard_id, ".enc.json", encrypted.to_json())
            self._index_add(encrypted.scope, shard_id)
            return encrypted

    def update_metadata(
        self,
        shard_id: str,
        updates: dict,
        *,
        key: bytes | None = None,
    ) -> MemoryShard:
        """Atomically update mutable shard-envelope metadata.

        ``MemoryShard.meta`` is intentionally outside the legacy content
        address. This explicit exception to immutable object writes exists for
        MemPalace metadata-only updates. It holds the store lock, reads through
        no-follow descriptors, verifies the existing object, and atomically
        replaces the plaintext or encrypted envelope. Atom content, scope and
        origin cannot change through this API.
        """
        shard_id = validate_shard_id(shard_id)
        with self._index_lock():
            suffix = ".enc.json" if key is not None else ".json"
            raw = self._read_object_bytes(shard_id, suffix)
            if raw is None:
                raise KeyError(f"Shard {shard_id} not found")
            if key is None:
                shard = self._decode_plaintext(shard_id, raw)
            else:
                encrypted = self._decode_encrypted(shard_id, raw)
                shard = decrypt_shard(encrypted, key)

            shard.meta.update(updates)
            if shard.shard_id != shard_id:
                raise IntegrityError(
                    "Metadata update changed content-addressed shard fields"
                )
            if key is None:
                replacement = shard.to_json()
            else:
                replacement = encrypt_shard(shard, key).to_json()
            self._replace_object_text(shard_id, suffix, replacement)
            return shard

    def decrypt_and_get(self, shard_id: str, key: bytes) -> MemoryShard:
        """Retrieve and decrypt a shard. Raises DecryptionError on failure."""
        encrypted = self.get_encrypted(shard_id)
        if encrypted is None:
            raise KeyError(f"Encrypted shard {shard_id} not found")
        return decrypt_shard(encrypted, key)

    # === Entitlement-Aware Hydration ===

    def hydrate_with_entitlement(self, token: EntitlementToken) -> str:
        """Hydrate all shards an entitlement token grants access to.

        Validates token expiry, scope access, and read capability.
        Decrypts each entitled shard and renders as injectable context.
        Raises on expired token or missing capability.
        """
        if is_expired(token):
            raise PermissionError("Entitlement token has expired")

        if not validate_capability(token, Capability.SHARD_READ):
            raise PermissionError("Token lacks shard:read capability")

        parts = []
        for shard_id, _key_str in token.shard_keys.items():
            # Check if we have this shard (encrypted or plaintext)
            encrypted = self.get_encrypted(shard_id)
            if encrypted is not None:
                # Reject an honestly denied cleartext wrapper before touching
                # key material or attempting decryption. The wrapper remains
                # untrusted, so authenticated inner scope is checked again.
                if not validate_scope(token, encrypted.scope):
                    raise PermissionError(
                        f"Token not entitled to scope '{encrypted.scope}' "
                        f"(shard {shard_id})"
                    )
                key = get_shard_key(token, shard_id)
                shard = decrypt_shard(encrypted, key)
                if not validate_scope(token, shard.scope):
                    raise PermissionError(
                        f"Token not entitled to scope '{shard.scope}' "
                        f"(shard {shard_id})"
                    )
                parts.append(shard.hydrate_context())
            else:
                # Try plaintext shard (for backward compat / unencrypted stores)
                shard = self.get(shard_id)
                if shard is not None:
                    if not validate_scope(token, shard.scope):
                        raise PermissionError(
                            f"Token not entitled to scope '{shard.scope}' "
                            f"(shard {shard_id})"
                        )
                    parts.append(shard.hydrate_context())
                # Shard not found — skip silently (might be on DHT)

        return "\n\n".join(parts)

    # === Sealed Shard Operations (zero-knowledge / owner-encrypted) ===

    def _sealed_path(self, shard_id: str) -> Path:
        """Path for sealed shard: same layout, .sealed.json suffix."""
        return self._content_path(shard_id, ".sealed.json")

    def put_sealed(self, sealed) -> str:
        """Store a sealed shard, rejecting bytes that conflict by ID.

        Accepts a SealedShard from spiritwriter.fabric.sealed.
        The sealed payload is opaque to the operator — only the
        owner (holder of the private key) can decrypt it.
        """
        shard_id = validate_shard_id(sealed.shard_id)
        with self._index_lock():
            self._store_immutable_text(shard_id, ".sealed.json", sealed.to_json())
            self._index_add(sealed.scope, shard_id)
        return shard_id

    def get_sealed(self, shard_id: str):
        """Retrieve a sealed shard by id. Returns SealedShard or None.

        Falls back to network if a resolver is configured.
        Requires PyNaCl to be installed (for deserialization).

        Returns ``None`` ONLY on genuine absence. A transport/config failure
        from the resolver (``NetworkUnavailable`` / ``NetworkTimeout``, or
        ``S3ConfigurationError`` from the S3 backend) PROPAGATES — it is never
        collapsed into ``None``, so a transient failure is not mistaken for
        "absent". See :meth:`get` for the full contract.
        """
        shard_id = validate_shard_id(shard_id)
        raw = self._read_object_bytes(shard_id, ".sealed.json")
        if raw is not None:
            return self._decode_sealed(shard_id, raw)

        if self._resolver:
            sealed = self._resolver.resolve_sealed(shard_id)
            if sealed:
                self.put_sealed(sealed)  # cache locally
                return sealed

        return None

    def has_sealed(self, shard_id: str) -> bool:
        """Check if a sealed shard exists."""
        return self._object_exists(shard_id, ".sealed.json")

    def seal_and_store(self, shard: MemoryShard, owner_pubkey: bytes):
        """Seal and store a shard, returning the existing envelope on retry.

        NaCl sealed boxes are randomized. Under the store lock, a repeated call
        for the same serialized inner shard and owner returns the original
        envelope. Reusing the legacy shard ID for different metadata or a
        different owner remains an integrity error.
        """
        from spiritwriter.fabric.sealed import seal_shard

        shard_id = validate_shard_id(shard.shard_id)
        plaintext = shard.to_json().encode("utf-8")
        content_hash = hashlib.sha256(plaintext).hexdigest()
        with self._index_lock():
            raw = self._read_object_bytes(shard_id, ".sealed.json")
            if raw is not None:
                existing = self._decode_sealed(shard_id, raw)
                expected = (
                    existing.owner_pubkey == owner_pubkey
                    and existing.content_hash == content_hash
                    and existing.scope == shard.scope
                    and existing.atom_count == len(shard.atoms)
                    and existing.created_at == shard.created_at
                    and existing.origin_agent == shard.origin
                )
                if not expected:
                    raise IntegrityError(
                        f"Sealed shard {shard_id} already stores a different envelope"
                    )
                self._index_add(existing.scope, shard_id)
                return existing
            sealed = seal_shard(shard, owner_pubkey)
            self._store_immutable_text(shard_id, ".sealed.json", sealed.to_json())
            self._index_add(sealed.scope, shard_id)
            return sealed

    def unseal_and_get(self, shard_id: str, owner_private_key: bytes) -> MemoryShard:
        """Retrieve and unseal a shard. Raises UnsealError on failure.

        Only the owner (holder of the private key) can call this.
        Requires PyNaCl to be installed.
        """
        from spiritwriter.fabric.sealed import unseal_shard, UnsealError
        sealed = self.get_sealed(shard_id)
        if sealed is None:
            raise KeyError(f"Sealed shard {shard_id} not found")
        return unseal_shard(sealed, owner_private_key)

    def stats(self) -> dict:
        """Summary statistics."""
        index = self._load_index()
        by_scope: dict[str, int] = {}
        by_decay: dict[str, int] = {}
        total_atoms = 0

        for shard in self.iter_all():
            by_scope[shard.scope] = by_scope.get(shard.scope, 0) + 1
            dc = shard.decay_class.value
            by_decay[dc] = by_decay.get(dc, 0) + 1
            total_atoms += len(shard.atoms)

        return {
            "total_shards": self.count(),
            "total_atoms": total_atoms,
            "scopes": by_scope,
            "by_decay_class": by_decay,
        }
