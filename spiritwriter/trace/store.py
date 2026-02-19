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
from pathlib import Path
from typing import Iterator

from spiritwriter.trace.shard import MemoryShard, ShardRef, DecayClass


class ShardStore:
    """Content-addressed shard storage.

    Local file-based store that mirrors git's object storage pattern.
    Designed to be wrapped by a DHT resolver for distributed access.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.shards_dir = self.root / "shards"
        self.refs_dir = self.root / "refs"
        self.index_path = self.root / "index.json"
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
        """Retrieve a shard by content address. Returns None if not found."""
        path = self._shard_path(shard_id)
        if not path.exists():
            return None
        return MemoryShard.from_json(path.read_text(encoding="utf-8"))

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

    def list_scopes(self) -> list[str]:
        """List all scopes that have shards."""
        return list(self._load_index().keys())

    def iter_all(self) -> Iterator[MemoryShard]:
        """Iterate all shards in the store."""
        for prefix_dir in sorted(self.shards_dir.iterdir()):
            if not prefix_dir.is_dir():
                continue
            for shard_file in sorted(prefix_dir.iterdir()):
                if shard_file.suffix == ".json":
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

    def set_ref(self, name: str, shard_id: str) -> None:
        """Set a named reference pointing to a shard.

        Use for "latest project context" pointers that update
        as new shards supersede old ones.
        """
        ref_path = self.refs_dir / f"{name}.ref"
        ref_path.write_text(shard_id, encoding="utf-8")

    def get_ref(self, name: str) -> str | None:
        """Resolve a named reference to a shard_id."""
        ref_path = self.refs_dir / f"{name}.ref"
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        return None

    def resolve_ref(self, name: str) -> MemoryShard | None:
        """Resolve a named ref to its full shard."""
        shard_id = self.get_ref(name)
        if shard_id:
            return self.get(shard_id)
        return None

    # === Maintenance ===

    def prune_expired(self) -> int:
        """Remove shards whose decay class has expired.

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
                created = datetime.fromisoformat(
                    shard.created_at.replace("Z", "+00:00")
                ).timestamp()
                if now - created > ttl:
                    self.delete(shard.shard_id)
                    pruned += 1
            except Exception:
                continue

        return pruned

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
