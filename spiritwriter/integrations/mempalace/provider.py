"""MemPalace as a retrieval provider for spiritwriter.

Wraps MemPalace's searcher to provide semantic search over shards.
When both packages are installed, spiritwriter agents can use
MemPalace to find relevant shards by natural language query instead
of only by content address or scope.

Usage:
    from spiritwriter.integrations import get_provider

    mp = get_provider("mempalace")
    if mp and mp.is_available():
        results = mp.search(SearchQuery(text="what database for sessions?"))
        for r in results:
            print(r.text, r.score)
"""

from __future__ import annotations

import logging
from typing import Any

from spiritwriter.integrations.base import (
    RetrievalProvider,
    StorageProvider,
    EntityProvider,
    ProviderInfo,
    ProviderCapability,
    SearchQuery,
    SearchResult,
    Document,
    EntityTriple,
)

logger = logging.getLogger(__name__)

# Lazy imports — mempalace may not be installed
_mempalace = None
_mempalace_checked = False


def _ensure_mempalace():
    """Lazy-import mempalace. Returns True if available."""
    global _mempalace, _mempalace_checked
    if _mempalace_checked:
        return _mempalace is not None
    _mempalace_checked = True
    try:
        import mempalace as mp
        _mempalace = mp
        return True
    except ImportError:
        return False


class MemPalaceProvider(RetrievalProvider, StorageProvider, EntityProvider):
    """MemPalace integration — semantic search + knowledge graph.

    Wraps MemPalace's ChromaDB-backed palace and SQLite knowledge graph
    behind spiritwriter's provider protocol. Any spiritwriter consumer
    can search MemPalace without importing it directly.
    """

    def __init__(self, palace_path: str | None = None):
        """Initialize with optional palace path override.

        Args:
            palace_path: Path to MemPalace data directory.
                         Defaults to ~/.mempalace/palace.
        """
        self._palace_path = palace_path
        self._collection = None
        self._kg = None

    def info(self) -> ProviderInfo:
        version = "unknown"
        if _ensure_mempalace():
            try:
                from mempalace.version import __version__
                version = __version__
            except Exception:
                pass

        caps = []
        if self.is_available():
            caps = [
                ProviderCapability.SEMANTIC_SEARCH,
                ProviderCapability.KEYWORD_SEARCH,
                ProviderCapability.HYBRID_SEARCH,
                ProviderCapability.KNOWLEDGE_GRAPH,
                ProviderCapability.TEMPORAL_QUERY,
                ProviderCapability.ENTITY_DETECTION,
                ProviderCapability.STORAGE,
                ProviderCapability.DEDUP,
            ]

        return ProviderInfo(
            name="mempalace",
            version=version,
            description="Local AI memory with semantic search, temporal "
                        "knowledge graph, and palace architecture. "
                        "96.6%% recall on LongMemEval, zero API keys.",
            capabilities=caps,
            requires_api_key=False,
            requires_gpu=False,
            local_only=True,
        )

    def is_available(self) -> bool:
        """Check if MemPalace is installed and a palace exists."""
        if not _ensure_mempalace():
            return False
        try:
            from mempalace.palace import get_collection
            palace_path = self._palace_path or self._default_palace_path()
            col = get_collection(palace_path=palace_path)
            return col is not None and col.count() > 0
        except Exception:
            return False

    def _default_palace_path(self) -> str:
        """Resolve default palace path from MemPalace config."""
        try:
            from mempalace.config import MempalaceConfig
            return MempalaceConfig().palace_path
        except Exception:
            import os
            return os.path.join(os.path.expanduser("~"), ".mempalace", "palace")

    def _get_collection(self):
        """Lazy-load the MemPalace ChromaDB collection."""
        if self._collection is None:
            from mempalace.palace import get_collection
            palace_path = self._palace_path or self._default_palace_path()
            self._collection = get_collection(palace_path=palace_path)
        return self._collection

    def _get_kg(self):
        """Lazy-load the MemPalace knowledge graph."""
        if self._kg is None:
            try:
                from mempalace.knowledge_graph import KnowledgeGraph
                self._kg = KnowledgeGraph()
            except Exception:
                pass
        return self._kg

    # === RetrievalProvider ===

    def search(self, query: SearchQuery) -> list[SearchResult]:
        """Semantic search via MemPalace's hybrid searcher.

        Uses MemPalace's BM25 + vector hybrid search. Results are
        returned as provider-agnostic SearchResult objects with
        scores normalized to 0.0-1.0 (higher = better).
        """
        if not _ensure_mempalace():
            return []

        try:
            from mempalace.searcher import search as mp_search
        except ImportError:
            # Fall back to direct ChromaDB query
            return self._search_direct(query)

        try:
            palace_path = self._palace_path or self._default_palace_path()
            results = mp_search(
                query.text,
                palace_path=palace_path,
                wing=query.filters.get("wing"),
                room=query.filters.get("room"),
                n_results=query.top_k,
            )
            return self._convert_results(results, query)
        except TypeError as e:
            logger.warning("MemPalace searcher API mismatch, falling back to direct: %s", e)
            return self._search_direct(query)
        except Exception as e:
            logger.debug("MemPalace search fell back to direct: %s", e)
            return self._search_direct(query)

    def _search_direct(self, query: SearchQuery) -> list[SearchResult]:
        """Direct ChromaDB query when MemPalace searcher isn't available."""
        col = self._get_collection()
        if col is None:
            return []

        where_filter = {}
        if query.filters.get("wing"):
            where_filter["wing"] = query.filters["wing"]

        kwargs: dict[str, Any] = {
            "query_texts": [query.text],
            "n_results": query.top_k,
        }
        if where_filter:
            kwargs["where"] = where_filter

        raw = col.query(**kwargs)
        return self._convert_chroma_results(raw)

    def _convert_results(self, results: Any, query: SearchQuery) -> list[SearchResult]:
        """Convert MemPalace search results to SearchResult objects."""
        out = []
        if hasattr(results, "get"):
            return self._convert_chroma_results(results)

        # Handle list-of-dict format from MemPalace searcher
        if isinstance(results, list):
            for i, r in enumerate(results):
                if isinstance(r, dict):
                    # Normalize score: MemPalace uses distance (lower=better)
                    # Convert to similarity (higher=better)
                    distance = r.get("distance", r.get("score", 0.5))
                    score = max(0.0, 1.0 - float(distance))
                    out.append(SearchResult(
                        document_id=r.get("id", f"result_{i}"),
                        text=r.get("document", r.get("text", "")),
                        score=score,
                        metadata=r.get("metadata", {}),
                        source="mempalace",
                    ))
        return out

    def _convert_chroma_results(self, raw: dict) -> list[SearchResult]:
        """Convert raw ChromaDB query results to SearchResult objects."""
        out = []
        documents = raw.get("documents", [[]])[0] if raw.get("documents") else []
        distances = raw.get("distances", [[]])[0] if raw.get("distances") else []
        ids = raw.get("ids", [[]])[0] if raw.get("ids") else []
        metadatas = raw.get("metadatas", [[]])[0] if raw.get("metadatas") else []

        for i, (doc, dist) in enumerate(zip(documents, distances)):
            score = max(0.0, 1.0 - float(dist))
            out.append(SearchResult(
                document_id=ids[i] if i < len(ids) else f"result_{i}",
                text=doc or "",
                score=score,
                metadata=metadatas[i] if i < len(metadatas) else {},
                source="mempalace",
            ))
        return out

    # === StorageProvider ===

    def store(self, documents: list[Document]) -> list[str]:
        """Store documents in MemPalace as drawers."""
        col = self._get_collection()
        if col is None:
            raise RuntimeError("MemPalace collection not available")

        ids = [d.document_id for d in documents]
        texts = [d.text for d in documents]
        metas = [d.metadata for d in documents]

        col.upsert(documents=texts, ids=ids, metadatas=metas)
        return ids

    def get(self, document_id: str) -> Document | None:
        """Retrieve a MemPalace drawer by ID."""
        col = self._get_collection()
        if col is None:
            return None

        result = col.get(ids=[document_id])
        docs = result.get("documents", [])
        metas = result.get("metadatas", [])
        if not docs:
            return None

        return Document(
            document_id=document_id,
            text=docs[0] or "",
            metadata=metas[0] if metas else {},
        )

    def delete(self, document_id: str) -> bool:
        """Delete a MemPalace drawer."""
        col = self._get_collection()
        if col is None:
            return False
        try:
            col.delete(ids=[document_id])
            return True
        except Exception:
            return False

    def count(self) -> int:
        col = self._get_collection()
        return col.count() if col else 0

    def check_duplicate(self, text: str, threshold: float = 0.9) -> str | None:
        """Check for near-duplicate using MemPalace's dedup."""
        if not _ensure_mempalace():
            return None
        try:
            from mempalace.dedup import check_duplicate as mp_check
            return mp_check(text, threshold=threshold)
        except (ImportError, Exception):
            # Fall back to direct similarity check
            results = self.search(SearchQuery(text=text, top_k=1))
            if results and results[0].score >= threshold:
                return results[0].document_id
            return None

    # === EntityProvider ===

    def add_triple(self, triple: EntityTriple) -> str:
        """Add a knowledge graph triple via MemPalace's KG."""
        kg = self._get_kg()
        if kg is None:
            raise RuntimeError("MemPalace knowledge graph not available")

        return kg.add_triple(
            triple.subject, triple.predicate, triple.object,
            valid_from=triple.valid_from,
            source_closet=triple.source_ref,
        )

    def query_entity(
        self, entity: str, as_of: str | None = None
    ) -> list[EntityTriple]:
        """Query entity relationships from MemPalace's KG."""
        kg = self._get_kg()
        if kg is None:
            return []

        rows = kg.query_entity(entity, as_of=as_of)
        return [
            EntityTriple(
                subject=r.get("subject", ""),
                predicate=r.get("predicate", ""),
                object=r.get("object", ""),
                valid_from=r.get("valid_from"),
                valid_to=r.get("valid_to"),
                source_ref=r.get("source_closet"),
            )
            for r in (rows if isinstance(rows, list) else [])
        ]

    def invalidate(
        self, subject: str, predicate: str, obj: str, ended: str | None = None
    ) -> bool:
        kg = self._get_kg()
        if kg is None:
            return False
        try:
            kg.invalidate(subject, predicate, obj, ended=ended)
            return True
        except Exception:
            return False

    def entity_stats(self) -> dict[str, int]:
        kg = self._get_kg()
        if kg is None:
            return {}
        try:
            return kg.stats()
        except Exception:
            return {}
