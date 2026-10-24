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
import json
import logging
import os
import re
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
        self.refs_dir.mkdir(parents=True, exist_ok=True)

    def _content_path(self, shard_id: str, suffix: str) -> Path:
        """Return a contained object path for a validated shard identifier."""
        shard_id = validate_shard_id(shard_id)
        object_root = self.shards_dir.resolve()
        path = (object_root / shard_id[:2] / f"{shard_id[2:]}{suffix}").resolve()
        try:
            path.relative_to(object_root)
        except ValueError as exc:
            raise ValueError("shard path escapes the object store") from exc
        return path

    def _shard_path(self, shard_id: str) -> Path:
        """Git-style object path: ab/cd1234...json."""
        return self._content_path(shard_id, ".json")

    def _store_immutable_text(self, path: Path, text: str) -> None:
        """Create an immutable object or verify an identical existing one.

        Callers hold ``_index_lock`` so competing local writers cannot both
        observe an absent path and replace each other's bytes.
        """
        expected = text.encode("utf-8")
        try:
            existing = path.read_bytes()
        except FileNotFoundError:
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write_text(path, text)
            return
        if existing != expected:
            raise IntegrityError(
                f"Content-address conflict at {path}: existing bytes differ"
            )

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

    def _scan_index(
        self, order: dict[str, list[str]] | None = None
    ) -> dict[str, list[str]]:
        """Reconstruct the index by scanning shard files.

        Plaintext, encrypted and sealed shards all carry a top-level
        ``scope``. Ids that appear in ``order`` keep that order; the rest
        are appended sorted (their original insertion order is not
        recoverable).
        """
        found: dict[str, set[str]] = {}
        for prefix_dir in sorted(self.shards_dir.iterdir()):
            if not prefix_dir.is_dir():
                continue
            for shard_file in sorted(prefix_dir.iterdir()):
                name = shard_file.name
                if not name.endswith(".json"):
                    continue
                stem = name[: -len(".json")]
                for suffix in (".enc", ".sealed"):
                    if stem.endswith(suffix):
                        stem = stem[: -len(suffix)]
                        break
                try:
                    scope = json.loads(shard_file.read_text(encoding="utf-8"))["scope"]
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                found.setdefault(scope, set()).add(prefix_dir.name + stem)

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
        their existing order. Returns ``{"added": [...], "removed": [...]}``
        shard ids and logs a summary.
        """
        with self._index_lock():
            try:
                old = self._read_index() or {}
            except ValueError:
                old = {}
            new = self._scan_index(order=old)
            old_ids = {i for ids in old.values() for i in ids}
            new_ids = {i for ids in new.values() for i in ids}
            added = sorted(new_ids - old_ids)
            removed = sorted(old_ids - new_ids)
            self._save_index(new)
            for prefix_dir in self.shards_dir.iterdir():
                if prefix_dir.is_dir():
                    _sweep_stale_tmp(prefix_dir, "*.tmp")
        if added or removed:
            logger.warning(
                "Reindexed %s: added %d, removed %d",
                self.index_path, len(added), len(removed),
            )
        return {"added": added, "removed": removed}

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
        path = self._shard_path(shard_id)
        serialized = shard.to_json()

        # One lock covers immutable object creation and index publication. A
        # colliding envelope with the same legacy shard_id is rejected rather
        # than silently keeping or replacing different bytes.
        with self._index_lock():
            self._store_immutable_text(path, serialized)
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
        path = self._shard_path(shard_id)
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            try:
                declared_id = json.loads(raw).get("shard_id")
            except AttributeError as exc:
                raise IntegrityError(
                    f"Plaintext shard {shard_id} is not a JSON object"
                ) from exc
            if declared_id != shard_id:
                raise IntegrityError(
                    f"Plaintext shard ID mismatch: requested {shard_id}, "
                    f"payload declares {declared_id!r}"
                )
            shard = MemoryShard.from_json(raw)
            if shard.shard_id != shard_id:
                raise IntegrityError(
                    f"Plaintext shard content mismatch: requested {shard_id}, "
                    f"computed {shard.shard_id}"
                )
            return shard

        # L2: network fallback
        if self._resolver:
            shard = self._resolver.resolve(shard_id)
            if shard:
                self.put(shard)  # cache locally
                return shard

        return None

    def has(self, shard_id: str) -> bool:
        """Check if a shard exists locally."""
        return self._shard_path(shard_id).exists()

    def delete(self, shard_id: str) -> bool:
        """Remove a shard. Returns True if it existed."""
        path = self._shard_path(shard_id)
        if not path.exists():
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
            path.unlink(missing_ok=True)
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
        """Iterate all plaintext shards in the store.

        Encrypted (``.enc.json``) and sealed (``.sealed.json``) payloads
        are skipped by filename — their contents aren't MemoryShard-shaped.
        The previous implementation skipped them only by catching the
        resulting deserialization error, which tied correctness to
        accidental failure of ``from_json``.
        """
        for prefix_dir in sorted(self.shards_dir.iterdir()):
            if not prefix_dir.is_dir():
                continue
            for shard_file in sorted(prefix_dir.iterdir()):
                name = shard_file.name
                if not name.endswith(".json"):
                    continue
                if name.endswith((".enc.json", ".sealed.json")):
                    continue
                try:
                    yield MemoryShard.from_json(
                        shard_file.read_text(encoding="utf-8")
                    )
                except Exception:
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
        path = self._encrypted_path(shard_id)
        with self._index_lock():
            self._store_immutable_text(path, encrypted.to_json())
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
        path = self._encrypted_path(shard_id)
        if path.exists():
            encrypted = EncryptedShard.from_json(path.read_text(encoding="utf-8"))
            if encrypted.shard_id != shard_id:
                raise IntegrityError(
                    f"Encrypted shard ID mismatch: requested {shard_id}, "
                    f"payload declares {encrypted.shard_id}"
                )
            return encrypted

        if self._resolver:
            encrypted = self._resolver.resolve_encrypted(shard_id)
            if encrypted:
                self.put_encrypted(encrypted)  # cache locally
                return encrypted

        return None

    def has_encrypted(self, shard_id: str) -> bool:
        """Check if an encrypted shard exists."""
        return self._encrypted_path(shard_id).exists()

    def encrypt_and_store(self, shard: MemoryShard, key: bytes) -> EncryptedShard:
        """Encrypt a shard and store the encrypted version. Returns EncryptedShard."""
        encrypted = encrypt_shard(shard, key)
        self.put_encrypted(encrypted)
        return encrypted

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
                key = get_shard_key(token, shard_id)
                shard = decrypt_shard(encrypted, key)
                # The encrypted wrapper is untrusted metadata. decrypt_shard()
                # first requires it to match the authenticated inner shard;
                # authorization then uses the decrypted scope itself.
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
        path = self._sealed_path(shard_id)
        with self._index_lock():
            self._store_immutable_text(path, sealed.to_json())
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
        path = self._sealed_path(shard_id)
        if path.exists():
            from spiritwriter.fabric.sealed import SealedShard
            sealed = SealedShard.from_json(path.read_text(encoding="utf-8"))
            if sealed.shard_id != shard_id:
                raise IntegrityError(
                    f"Sealed shard ID mismatch: requested {shard_id}, "
                    f"payload declares {sealed.shard_id}"
                )
            return sealed

        if self._resolver:
            sealed = self._resolver.resolve_sealed(shard_id)
            if sealed:
                self.put_sealed(sealed)  # cache locally
                return sealed

        return None

    def has_sealed(self, shard_id: str) -> bool:
        """Check if a sealed shard exists."""
        return self._sealed_path(shard_id).exists()

    def seal_and_store(self, shard: MemoryShard, owner_pubkey: bytes):
        """Seal a shard for an owner and store it. Returns SealedShard.

        Requires PyNaCl to be installed.
        """
        from spiritwriter.fabric.sealed import seal_shard
        sealed = seal_shard(shard, owner_pubkey)
        self.put_sealed(sealed)
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
