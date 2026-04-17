"""ShardStore as a storage backend for MemPalace.

Implements MemPalace's BaseCollection interface backed by spiritwriter's
ShardStore. Every MemPalace drawer becomes a content-addressed shard with
optional encryption, provenance, and IPFS distribution.

Usage (from MemPalace side):
    from spiritwriter.integrations.mempalace import ShardBackend
    backend = ShardBackend("~/.mempalace/shards")

    # Use as drop-in replacement for ChromaDB collection
    backend.add(documents=["verbatim text"], ids=["drawer_1"],
                metadatas=[{"wing": "alice", "room": "school"}])
    results = backend.query(query_texts=["swimming"], n_results=5)

What MemPalace gains:
- Content addressing (SHA-256) — tamper detection, free dedup
- Encryption (AES-GCM or NaCl sealed boxes) — encrypted palaces
- Lineage (parent_shard_id) — drawer revision history
- Distribution (IPFS) — share palaces across nodes
- Provenance (TraceEmitter) — auditable memory access log

What MemPalace keeps:
- Its own ChromaDB collection for vector search (embedding index)
- The ShardBackend wraps around it, not replaces it
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.crypto import DecryptionError

logger = logging.getLogger(__name__)

# Sidecar index filename for persistent drawer_id → shard_id mapping
_INDEX_FILENAME = "drawer_index.json"


class ShardBackend:
    """MemPalace BaseCollection implementation backed by ShardStore.

    Provides the same add/upsert/query/get/delete/count interface that
    MemPalace expects from its storage backend, but stores drawers as
    content-addressed shards.

    Optionally wraps a ChromaDB collection for vector search — the shard
    store handles persistence and trust, ChromaDB handles embeddings.

    The drawer_id to shard_id mapping is persisted as a sidecar JSON
    index alongside the shard store, so encrypted stores survive restarts.
    """

    def __init__(
        self,
        store_path: str | Path = "~/.mempalace/shards",
        chroma_collection: Any | None = None,
        encryption_key: bytes | None = None,
        scope_prefix: str = "palace",
        tracer: Any | None = None,
    ):
        """Initialize the shard-backed storage.

        Args:
            store_path: Path for ShardStore (content-addressed files).
            chroma_collection: Optional ChromaDB collection for vector
                search. If None, query() falls back to shard metadata scan.
            encryption_key: Optional AES-256 key. If set, all drawers are
                encrypted at rest.
            scope_prefix: Shard scope prefix (default "palace").
            tracer: Optional TraceEmitter for provenance logging.
        """
        self._root = Path(store_path).expanduser()
        self._store = ShardStore(self._root)
        self._chroma = chroma_collection
        self._encryption_key = encryption_key
        self._scope_prefix = scope_prefix
        self._tracer = tracer
        # drawer_id → shard_id mapping (persisted to disk)
        self._id_map: dict[str, str] = {}
        self._index_path = self._root / _INDEX_FILENAME
        self._load_id_map()

    def _load_id_map(self) -> None:
        """Load drawer_id → shard_id mapping from persistent index.

        Falls back to scanning plaintext shards if no index exists
        (first run or migration from unencrypted store).
        """
        if self._index_path.exists():
            try:
                self._id_map = json.loads(
                    self._index_path.read_text(encoding="utf-8")
                )
                return
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load drawer index, rebuilding: %s", e)

        # Fallback: scan plaintext shards (won't find encrypted ones,
        # but handles migration from a pre-index store)
        for shard in self._store.iter_all():
            drawer_id = shard.meta.get("drawer_id")
            if drawer_id:
                self._id_map[drawer_id] = shard.shard_id
        self._save_id_map()

    def _save_id_map(self) -> None:
        """Persist drawer_id → shard_id mapping to disk."""
        self._index_path.parent.mkdir(parents=True, exist_ok=True)
        self._index_path.write_text(
            json.dumps(self._id_map, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _drawer_to_shard(
        self, text: str, drawer_id: str, metadata: dict[str, Any] | None = None
    ) -> MemoryShard:
        """Convert a MemPalace drawer to a spiritwriter shard."""
        meta = metadata or {}
        wing = meta.get("wing", "general")
        room = meta.get("room", "")

        atoms = [
            ShardAtom(
                text=text,
                kind=AtomKind.CONTEXT,
                entity=wing,
                key="drawer",
                value=drawer_id,
            ),
        ]

        # Extract structured fields from metadata as additional atoms
        if room:
            atoms.append(ShardAtom(
                text=f"Room: {room}", kind=AtomKind.FACT,
                entity=wing, key="room", value=room,
            ))

        for key in ("source_file", "timestamp", "speaker"):
            if key in meta:
                atoms.append(ShardAtom(
                    text=f"{key}: {meta[key]}", kind=AtomKind.FACT,
                    entity=wing, key=key, value=str(meta[key]),
                ))

        scope = f"{self._scope_prefix}:{wing}"

        tags = [f"wing:{wing}"]
        if room:
            tags.append(f"room:{room}")

        shard = MemoryShard(
            atoms=atoms,
            scope=scope,
            origin="mempalace",
            decay_class=DecayClass.PERMANENT,  # verbatim = never discard
            tags=tags,
            meta={**meta, "drawer_id": drawer_id},
        )
        return shard

    def _store_shard(self, shard: MemoryShard) -> str:
        """Store a shard, with optional encryption and tracing."""
        if self._encryption_key:
            encrypted = self._store.encrypt_and_store(shard, self._encryption_key)
            shard_id = encrypted.shard_id
        else:
            ref = self._store.put(shard)
            shard_id = ref.shard_id

        if self._tracer:
            self._tracer.shard_created(
                shard_id=shard_id,
                scope=shard.scope,
                atom_count=len(shard.atoms),
                source="mempalace",
                drawer_id=shard.meta.get("drawer_id"),
            )

        return shard_id

    def _resolve_shard(self, shard_id: str) -> MemoryShard | None:
        """Retrieve a shard, decrypting if needed."""
        if self._encryption_key:
            try:
                return self._store.decrypt_and_get(shard_id, self._encryption_key)
            except (DecryptionError, KeyError) as e:
                logger.warning("Failed to decrypt shard %s: %s", shard_id[:12], e)
                return None
        return self._store.get(shard_id)

    # === BaseCollection interface ===

    def add(
        self,
        *,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        """Store drawers as shards + index in ChromaDB for search."""
        metas = metadatas or [{}] * len(documents)

        for text, drawer_id, meta in zip(documents, ids, metas):
            shard = self._drawer_to_shard(text, drawer_id, meta)
            shard_id = self._store_shard(shard)
            self._id_map[drawer_id] = shard_id

        self._save_id_map()

        # Also index in ChromaDB for vector search
        if self._chroma is not None:
            self._chroma.add(documents=documents, ids=ids, metadatas=metas)

    def upsert(
        self,
        *,
        documents: list[str],
        ids: list[str],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        """Upsert drawers — new shards link to previous via parent_shard_id."""
        metas = metadatas or [{}] * len(documents)

        for text, drawer_id, meta in zip(documents, ids, metas):
            shard = self._drawer_to_shard(text, drawer_id, meta)

            # Link to previous version if exists
            if drawer_id in self._id_map:
                old_shard_id = self._id_map[drawer_id]
                shard = MemoryShard(
                    atoms=shard.atoms,
                    scope=shard.scope,
                    origin=shard.origin,
                    decay_class=shard.decay_class,
                    tags=shard.tags,
                    meta=shard.meta,
                    parent_shard_id=old_shard_id,
                )

            shard_id = self._store_shard(shard)
            self._id_map[drawer_id] = shard_id

        self._save_id_map()

        if self._chroma is not None:
            self._chroma.upsert(documents=documents, ids=ids, metadatas=metas)

    def update(self, **kwargs: Any) -> None:
        """Update existing drawers.

        With documents: full content upsert (creates new shard with lineage).
        Metadata-only: creates a new shard with updated meta and links to
        the previous version via parent_shard_id (shards are immutable).
        """
        ids = kwargs.get("ids", [])
        documents = kwargs.get("documents")
        metadatas = kwargs.get("metadatas")

        if documents:
            metas = metadatas or [{}] * len(ids)
            self.upsert(documents=documents, ids=ids, metadatas=metas)
        elif metadatas:
            # Metadata-only update. Shards are content-addressed so
            # changing only meta (which is excluded from shard_id) won't
            # produce a new ID. We force-write the updated JSON to the
            # same path, preserving the content address guarantee while
            # allowing mutable metadata on the shard envelope.
            for drawer_id, new_meta in zip(ids, metadatas):
                if drawer_id not in self._id_map:
                    raise KeyError(f"Drawer {drawer_id} not found")
                shard_id = self._id_map[drawer_id]
                shard = self._resolve_shard(shard_id)
                if shard is None:
                    raise KeyError(f"Shard {shard_id[:12]} not found for drawer {drawer_id}")

                # Merge new metadata and force-write
                shard.meta.update(new_meta)
                shard.meta["drawer_id"] = drawer_id
                path = self._store._shard_path(shard_id)
                if self._encryption_key:
                    self._store.encrypt_and_store(shard, self._encryption_key)
                else:
                    path.write_text(shard.to_json(), encoding="utf-8")

        if self._chroma is not None:
            self._chroma.update(**kwargs)

    def query(self, **kwargs: Any) -> dict[str, Any]:
        """Query via ChromaDB (vector search) with shard-backed results.

        If ChromaDB isn't configured, falls back to metadata scan with
        keyword overlap scoring. Supports wing and room filtering via
        the 'where' parameter.
        """
        if self._chroma is not None:
            return self._chroma.query(**kwargs)

        # Fallback: basic text matching on shard atoms
        query_texts = kwargs.get("query_texts", [])
        n_results = kwargs.get("n_results", 10)
        where = kwargs.get("where", {})

        if not query_texts:
            return {"documents": [[]], "ids": [[]], "distances": [[]], "metadatas": [[]]}

        query_lower = query_texts[0].lower()
        scored = []

        for shard in self._store.iter_all():
            drawer_id = shard.meta.get("drawer_id")
            if not drawer_id:
                continue

            # Apply where filters (wing, room, etc.)
            skip = False
            for filter_key, filter_val in where.items():
                if shard.meta.get(filter_key) != filter_val:
                    skip = True
                    break
            if skip:
                continue

            text = shard.atoms[0].text if shard.atoms else ""
            # Simple keyword overlap score
            words = set(query_lower.split())
            doc_words = set(text.lower().split())
            overlap = len(words & doc_words) / max(len(words), 1)
            scored.append((overlap, drawer_id, text, shard.meta))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:n_results]

        return {
            "documents": [[t[2] for t in top]],
            "ids": [[t[1] for t in top]],
            "distances": [[1.0 - t[0] for t in top]],
            "metadatas": [[t[3] for t in top]],
        }

    def get(self, **kwargs: Any) -> dict[str, Any]:
        """Get drawers by ID, backed by shard store."""
        ids = kwargs.get("ids", [])
        documents = []
        metadatas = []
        found_ids = []

        for drawer_id in ids:
            shard_id = self._id_map.get(drawer_id)
            if shard_id:
                shard = self._resolve_shard(shard_id)
                if shard:
                    text = shard.atoms[0].text if shard.atoms else ""
                    documents.append(text)
                    metadatas.append(shard.meta)
                    found_ids.append(drawer_id)

                    if self._tracer:
                        self._tracer.shard_resolved(shard_id, by_agent="mempalace")

        return {"documents": documents, "ids": found_ids, "metadatas": metadatas}

    def delete(self, **kwargs: Any) -> None:
        """Delete drawers from shard store + ChromaDB."""
        ids = kwargs.get("ids", [])
        for drawer_id in ids:
            shard_id = self._id_map.pop(drawer_id, None)
            if shard_id:
                self._store.delete(shard_id)

        self._save_id_map()

        if self._chroma is not None:
            self._chroma.delete(**kwargs)

    def count(self) -> int:
        """Total drawers in the store."""
        return len(self._id_map)

    # === Spiritwriter-specific extras ===

    @property
    def shard_store(self) -> ShardStore:
        """Direct access to the underlying ShardStore."""
        return self._store

    def get_shard_id(self, drawer_id: str) -> str | None:
        """Get the shard content address for a drawer."""
        return self._id_map.get(drawer_id)

    def get_drawer_history(self, drawer_id: str) -> list[MemoryShard]:
        """Get the full revision history of a drawer via lineage chain."""
        shard_id = self._id_map.get(drawer_id)
        if not shard_id:
            return []

        history = []
        current_id = shard_id
        while current_id:
            shard = self._resolve_shard(current_id)
            if shard is None:
                break
            history.append(shard)
            current_id = shard.parent_shard_id

        return history

    def stats(self) -> dict[str, Any]:
        """Combined stats from shard store."""
        store_stats = self._store.stats()
        return {
            "drawers": len(self._id_map),
            "encrypted": self._encryption_key is not None,
            **store_stats,
        }
