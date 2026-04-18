"""Unit tests for spiritwriter.integrations — ShardBackend, EntityBridge, provider protocol.

Targets the specific correctness issues identified in PR review:
- Metadata-only update must produce a new shard (not silently no-op)
- Encrypted store must survive restart (persistent drawer index)
- _search_direct must apply the same filters as the primary path
"""

import json
import os
import pytest
import tempfile
from pathlib import Path

from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.crypto import generate_job_key
from spiritwriter.integrations.mempalace.backend import ShardBackend
from spiritwriter.integrations.mempalace.entities import EntityBridge
from spiritwriter.integrations.base import (
    SearchQuery, SearchResult, ProviderCapability, ProviderInfo,
)


# ── ShardBackend ─────────────────────────────────────────────────────

class TestShardBackendBasics:
    """Core add/get/delete/count operations."""

    def test_add_and_get(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.add(
            documents=["Hello world"],
            ids=["d1"],
            metadatas=[{"wing": "test", "room": "unit"}],
        )
        assert backend.count() == 1
        result = backend.get(ids=["d1"])
        assert result["documents"] == ["Hello world"]
        assert result["ids"] == ["d1"]

    def test_add_multiple(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.add(
            documents=["Doc A", "Doc B", "Doc C"],
            ids=["a", "b", "c"],
            metadatas=[{"wing": "w1"}, {"wing": "w2"}, {"wing": "w1"}],
        )
        assert backend.count() == 3

    def test_delete(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.add(documents=["To delete"], ids=["del1"], metadatas=[{}])
        assert backend.count() == 1
        backend.delete(ids=["del1"])
        assert backend.count() == 0
        result = backend.get(ids=["del1"])
        assert result["documents"] == []

    def test_get_nonexistent(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        result = backend.get(ids=["does_not_exist"])
        assert result["documents"] == []

    def test_shard_id_returned(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.add(documents=["Content"], ids=["d1"], metadatas=[{}])
        sid = backend.get_shard_id("d1")
        assert sid is not None
        assert len(sid) == 64  # SHA-256 hex


class TestShardBackendUpsertLineage:
    """Upsert creates revision chains via parent_shard_id."""

    def test_upsert_creates_lineage(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.add(documents=["Version 1"], ids=["d1"], metadatas=[{"wing": "test"}])
        sid_v1 = backend.get_shard_id("d1")

        backend.upsert(documents=["Version 2"], ids=["d1"], metadatas=[{"wing": "test"}])
        sid_v2 = backend.get_shard_id("d1")

        assert sid_v1 != sid_v2
        history = backend.get_drawer_history("d1")
        assert len(history) == 2
        assert history[0].atoms[0].text == "Version 2"
        assert history[1].atoms[0].text == "Version 1"
        assert history[0].parent_shard_id == sid_v1

    def test_upsert_new_drawer_no_parent(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.upsert(documents=["Brand new"], ids=["new1"], metadatas=[{}])
        history = backend.get_drawer_history("new1")
        assert len(history) == 1
        assert history[0].parent_shard_id is None


class TestShardBackendMetadataUpdate:
    """Bug fix: metadata-only update must produce a new shard."""

    def test_metadata_only_update_persists(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.add(documents=["Content"], ids=["d1"], metadatas=[{"wing": "test", "tag": "old"}])
        sid_before = backend.get_shard_id("d1")

        backend.update(ids=["d1"], metadatas=[{"tag": "new"}])

        # New metadata must be readable after update
        result = backend.get(ids=["d1"])
        assert result["metadatas"][0]["tag"] == "new"
        # Original metadata preserved
        assert result["metadatas"][0]["wing"] == "test"

        # Content must be unchanged
        assert result["documents"][0] == "Content"

    def test_metadata_update_preserves_content(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.add(documents=["Original text"], ids=["d1"], metadatas=[{"wing": "test"}])
        backend.update(ids=["d1"], metadatas=[{"extra": "field"}])

        result = backend.get(ids=["d1"])
        assert result["documents"][0] == "Original text"

    def test_metadata_update_nonexistent_raises(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        with pytest.raises(KeyError):
            backend.update(ids=["missing"], metadatas=[{"tag": "x"}])


class TestShardBackendEncryptedRestart:
    """Bug fix: encrypted store must survive process restart."""

    def test_encrypted_roundtrip(self, tmp_path):
        key = generate_job_key()
        backend = ShardBackend(tmp_path / "shards", encryption_key=key)
        backend.add(documents=["Secret data"], ids=["enc1"], metadatas=[{"wing": "private"}])

        result = backend.get(ids=["enc1"])
        assert result["documents"] == ["Secret data"]

    def test_encrypted_survives_restart(self, tmp_path):
        """The critical bug: create encrypted backend, add data, destroy it,
        create a new one with same path + key. Data must be retrievable."""
        key = generate_job_key()
        store_path = tmp_path / "shards"

        # Session 1: add data
        backend1 = ShardBackend(store_path, encryption_key=key)
        backend1.add(
            documents=["Secret A", "Secret B"],
            ids=["enc_a", "enc_b"],
            metadatas=[{"wing": "private"}, {"wing": "private"}],
        )
        assert backend1.count() == 2

        # Destroy the backend (simulates process restart)
        del backend1

        # Session 2: new backend, same path + key
        backend2 = ShardBackend(store_path, encryption_key=key)
        assert backend2.count() == 2

        result_a = backend2.get(ids=["enc_a"])
        assert result_a["documents"] == ["Secret A"]

        result_b = backend2.get(ids=["enc_b"])
        assert result_b["documents"] == ["Secret B"]

    def test_encrypted_upsert_survives_restart(self, tmp_path):
        key = generate_job_key()
        store_path = tmp_path / "shards"

        backend1 = ShardBackend(store_path, encryption_key=key)
        backend1.add(documents=["V1"], ids=["d1"], metadatas=[{"wing": "test"}])
        backend1.upsert(documents=["V2"], ids=["d1"], metadatas=[{"wing": "test"}])
        assert backend1.count() == 1
        del backend1

        backend2 = ShardBackend(store_path, encryption_key=key)
        assert backend2.count() == 1
        result = backend2.get(ids=["d1"])
        assert result["documents"] == ["V2"]
        history = backend2.get_drawer_history("d1")
        assert len(history) == 2

    def test_persistent_index_file_exists(self, tmp_path):
        backend = ShardBackend(tmp_path / "shards")
        backend.add(documents=["Test"], ids=["d1"], metadatas=[{}])

        index_path = tmp_path / "shards" / "drawer_index.json"
        assert index_path.exists()
        index = json.loads(index_path.read_text())
        assert "d1" in index


class TestShardBackendQueryFilters:
    """Bug fix: fallback query must apply the same filters as primary."""

    def test_query_filters_wing(self, tmp_path):
        """Fallback query (no ChromaDB) must filter by wing."""
        backend = ShardBackend(tmp_path / "shards")
        backend.add(
            documents=["Swimming practice", "Math homework"],
            ids=["d1", "d2"],
            metadatas=[{"wing": "sports"}, {"wing": "school"}],
        )

        # Query with wing filter
        result = backend.query(
            query_texts=["practice"],
            n_results=10,
            where={"wing": "sports"},
        )
        ids = result["ids"][0]
        assert "d1" in ids
        assert "d2" not in ids

    def test_query_filters_room(self, tmp_path):
        """Fallback query must filter by room."""
        backend = ShardBackend(tmp_path / "shards")
        backend.add(
            documents=["Backstroke PB", "Chess rating 1200"],
            ids=["d1", "d2"],
            metadatas=[
                {"wing": "family", "room": "swimming"},
                {"wing": "family", "room": "chess"},
            ],
        )

        result = backend.query(
            query_texts=["rating"],
            n_results=10,
            where={"room": "chess"},
        )
        ids = result["ids"][0]
        assert "d2" in ids
        assert "d1" not in ids


# ── EntityBridge ─────────────────────────────────────────────────────

class TestEntityBridge:
    """Cross-conversation entity resolution via CMC-Lite."""

    def test_new_entity(self, tmp_path):
        bridge = EntityBridge(tmp_path / "entities.db")
        r = bridge.resolve_person("Max", context={"wing": "family", "relationship": "son"})
        assert r.tier.value == "no_match"
        bridge.close()

    def test_t1_exact_match(self, tmp_path):
        bridge = EntityBridge(tmp_path / "entities.db")
        r1 = bridge.resolve_person("Max", context={"relationship": "son"}, source_id="s1")
        r2 = bridge.resolve_person("Max", context={"relationship": "son"}, source_id="s2")
        assert r2.tier.value == "t1_exact"
        assert r2.confidence == 0.95
        bridge.close()

    def test_different_entities_stay_separate(self, tmp_path):
        bridge = EntityBridge(tmp_path / "entities.db")
        r1 = bridge.resolve_person("Max", context={"relationship": "son"}, source_id="s1")
        r2 = bridge.resolve_person("Riley", context={"relationship": "daughter"}, source_id="s2")
        # Both are new — different entities
        assert r1.tier.value == "no_match"
        assert r2.tier.value == "no_match"
        stats = bridge.stats()
        assert stats["persons"]["entities"] == 2
        bridge.close()

    def test_project_resolution(self, tmp_path):
        bridge = EntityBridge(tmp_path / "entities.db")
        r1 = bridge.resolve_project("MyApp", context={"wing": "work"}, source_id="s1")
        r2 = bridge.resolve_project("MyApp", context={"wing": "work"}, source_id="s2")
        assert r2.tier.value == "t1_exact"
        bridge.close()

    def test_find_similar(self, tmp_path):
        bridge = EntityBridge(tmp_path / "entities.db")
        bridge.resolve_person("Maxwell", context={"relationship": "son"}, source_id="s1")
        results = bridge.find_similar("Max", entity_type="person")
        assert len(results) > 0
        bridge.close()

    def test_db_path_creates_suffixed_files(self, tmp_path):
        """EntityBridge creates _persons.db and _projects.db from the base path."""
        bridge = EntityBridge(tmp_path / "mydb.db")
        bridge.resolve_person("Test", source_id="s1")
        bridge.resolve_project("TestProj", source_id="s2")
        bridge.close()
        assert (tmp_path / "mydb_persons.db").exists()
        assert (tmp_path / "mydb_projects.db").exists()

    def test_context_manager(self, tmp_path):
        with EntityBridge(tmp_path / "entities.db") as bridge:
            bridge.resolve_person("Max", source_id="s1")
            assert bridge.stats()["persons"]["entities"] == 1


# ── Provider Protocol ────────────────────────────────────────────────

class TestProviderDiscovery:
    """Auto-discovery and provider protocol."""

    def test_available_providers_returns_dict(self):
        from spiritwriter.integrations import available_providers
        providers = available_providers()
        assert isinstance(providers, dict)

    def test_get_provider_returns_none_for_unknown(self):
        from spiritwriter.integrations import get_provider
        assert get_provider("nonexistent_provider") is None

    def test_register_custom_provider(self):
        from spiritwriter.integrations import register_provider, get_provider
        from spiritwriter.integrations.base import RetrievalProvider

        class DummyProvider(RetrievalProvider):
            def info(self):
                return ProviderInfo(
                    name="dummy", version="0.1", description="test",
                    capabilities=[], local_only=True,
                )
            def search(self, query):
                return []

        register_provider("dummy_test", DummyProvider())
        p = get_provider("dummy_test")
        assert p is not None
        assert p.info().name == "dummy"
        assert p.search(SearchQuery(text="anything")) == []

    def test_provider_capability_check(self):
        from spiritwriter.integrations.base import RetrievalProvider

        class CapProvider(RetrievalProvider):
            def info(self):
                return ProviderInfo(
                    name="cap", version="1.0", description="test",
                    capabilities=[ProviderCapability.SEMANTIC_SEARCH],
                )
            def search(self, query):
                return []

        p = CapProvider()
        assert p.has_capability(ProviderCapability.SEMANTIC_SEARCH)
        assert not p.has_capability(ProviderCapability.KNOWLEDGE_GRAPH)
