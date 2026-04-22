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

import json
import os
import re
from pathlib import Path
from typing import Iterator

from spiritwriter.fabric.shard import MemoryShard, ShardRef, DecayClass
from spiritwriter.fabric.crypto import EncryptedShard, encrypt_shard, decrypt_shard, DecryptionError
from spiritwriter.fabric.entitlement import (
    EntitlementToken, validate_capability, validate_scope,
    is_expired, get_shard_key, Capability,
)
from spiritwriter.fabric.network import NetworkResolver


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
        self.shards_dir.mkdir(parents=True, exist_ok=True)
        self.refs_dir.mkdir(parents=True, exist_ok=True)

    def _shard_path(self, shard_id: str) -> Path:
        """Git-style object path: ab/cd1234...json"""
        return self.shards_dir / shard_id[:2] / f"{shard_id[2:]}.json"

    def _load_index(self) -> dict[str, list[str]]:
        """Load scope → [shard_id] index."""
        if self.index_path.exists():
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        return {}

    def _save_index(self, index: dict[str, list[str]]) -> None:
        self.index_path.write_text(
            json.dumps(index, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # === Core Operations ===

    def put(self, shard: MemoryShard) -> ShardRef:
        """Store a shard. Returns its ref. Idempotent (content-addressed)."""
        shard_id = shard.shard_id
        path = self._shard_path(shard_id)

        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(shard.to_json(), encoding="utf-8")

            # Update index
            index = self._load_index()
            scope_shards = index.setdefault(shard.scope, [])
            if shard_id not in scope_shards:
                scope_shards.append(shard_id)
            self._save_index(index)

        return shard.ref

    def get(self, shard_id: str) -> MemoryShard | None:
        """Retrieve a shard by content address. Returns None if not found.

        L1: local file. L2: network fallback (if resolver configured).
        """
        # L1: local file
        path = self._shard_path(shard_id)
        if path.exists():
            return MemoryShard.from_json(path.read_text(encoding="utf-8"))

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
        # Load shard to get scope for index cleanup
        try:
            shard = MemoryShard.from_json(path.read_text(encoding="utf-8"))
            index = self._load_index()
            scope_shards = index.get(shard.scope, [])
            if shard_id in scope_shards:
                scope_shards.remove(shard_id)
                if not scope_shards:
                    del index[shard.scope]
                self._save_index(index)
        except Exception:
            pass
        path.unlink()
        return True

    # === Query Operations ===

    def resolve(self, ref: ShardRef) -> MemoryShard | None:
        """Resolve a shard reference to its full content."""
        return self.get(ref.shard_id)

    def resolve_many(self, refs: list[ShardRef]) -> list[MemoryShard]:
        """Resolve multiple refs. Skips any that aren't found locally."""
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
        """Get all shards in a scope."""
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
        """Set a named reference pointing to a shard.

        Use for "latest project context" pointers that update
        as new shards supersede old ones.
        """
        self._ref_path(name).write_text(shard_id, encoding="utf-8")

    def get_ref(self, name: str) -> str | None:
        """Resolve a named reference to a shard_id."""
        ref_path = self._ref_path(name)
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        return None

    def resolve_ref(self, name: str) -> MemoryShard | None:
        """Resolve a named ref to its full shard."""
        shard_id = self.get_ref(name)
        if shard_id:
            return self.get(shard_id)
        return None

    def delete_ref(self, name: str) -> bool:
        """Remove a named ref. Returns True if it existed."""
        ref_path = self._ref_path(name)
        if ref_path.exists():
            ref_path.unlink()
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
        return self.shards_dir / shard_id[:2] / f"{shard_id[2:]}.enc.json"

    def put_encrypted(self, encrypted: EncryptedShard) -> str:
        """Store an encrypted shard. Returns shard_id."""
        path = self._encrypted_path(encrypted.shard_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(encrypted.to_json(), encoding="utf-8")

            # Update index (scope visible even when encrypted)
            index = self._load_index()
            scope_shards = index.setdefault(encrypted.scope, [])
            if encrypted.shard_id not in scope_shards:
                scope_shards.append(encrypted.shard_id)
            self._save_index(index)

        return encrypted.shard_id

    def get_encrypted(self, shard_id: str) -> EncryptedShard | None:
        """Retrieve an encrypted shard by id. Falls back to network."""
        path = self._encrypted_path(shard_id)
        if path.exists():
            return EncryptedShard.from_json(path.read_text(encoding="utf-8"))

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
                if not validate_scope(token, encrypted.scope):
                    raise PermissionError(
                        f"Token not entitled to scope '{encrypted.scope}' "
                        f"(shard {shard_id})"
                    )
                key = get_shard_key(token, shard_id)
                shard = decrypt_shard(encrypted, key)
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
        return self.shards_dir / shard_id[:2] / f"{shard_id[2:]}.sealed.json"

    def put_sealed(self, sealed) -> str:
        """Store a sealed shard. Returns shard_id.

        Accepts a SealedShard from spiritwriter.fabric.sealed.
        The sealed payload is opaque to the operator — only the
        owner (holder of the private key) can decrypt it.
        """
        path = self._sealed_path(sealed.shard_id)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(sealed.to_json(), encoding="utf-8")

            # Update index (scope visible even when sealed)
            index = self._load_index()
            scope_shards = index.setdefault(sealed.scope, [])
            if sealed.shard_id not in scope_shards:
                scope_shards.append(sealed.shard_id)
            self._save_index(index)

        return sealed.shard_id

    def get_sealed(self, shard_id: str):
        """Retrieve a sealed shard by id. Returns SealedShard or None.

        Falls back to network if resolver configured.
        Requires PyNaCl to be installed (for deserialization).
        """
        path = self._sealed_path(shard_id)
        if path.exists():
            from spiritwriter.fabric.sealed import SealedShard
            return SealedShard.from_json(path.read_text(encoding="utf-8"))

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
