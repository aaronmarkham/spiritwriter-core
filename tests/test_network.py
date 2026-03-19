"""Unit tests for network resolver types, CID map, and ShardStore fallback.

No IPFS/Kubo needed — uses mocks for the NetworkResolver protocol.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.trace.crypto import EncryptedShard, encrypt_shard, generate_job_key
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.network import (
    ShardLocation,
    ShardManifest,
    NetworkResolver,
    NetworkUnavailable,
    NetworkTimeout,
    IntegrityError,
)


# === Fixtures ===

def _make_shard(text: str = "test fact", scope: str = "test:unit") -> MemoryShard:
    return MemoryShard(
        atoms=[ShardAtom(text=text, kind=AtomKind.FACT)],
        scope=scope,
        origin="test-agent",
    )


# === ShardLocation Tests ===

class TestShardLocation:
    def test_roundtrip(self):
        loc = ShardLocation(shard_id="abc123", cid="QmXyz", local=True, pinned=True)
        d = loc.to_dict()
        restored = ShardLocation.from_dict(d)
        assert restored.shard_id == "abc123"
        assert restored.cid == "QmXyz"
        assert restored.local is True
        assert restored.pinned is True

    def test_sparse_serialization(self):
        loc = ShardLocation(shard_id="abc123")
        d = loc.to_dict()
        assert "cid" not in d
        assert "local" not in d
        assert "pinned" not in d

    def test_defaults(self):
        loc = ShardLocation.from_dict({"shard_id": "abc123"})
        assert loc.cid is None
        assert loc.local is False
        assert loc.pinned is False


# === ShardManifest Tests ===

class TestShardManifest:
    def test_roundtrip(self):
        entries = [
            ShardLocation(shard_id="a1", cid="QmA"),
            ShardLocation(shard_id="b2", cid="QmB"),
        ]
        manifest = ShardManifest(
            scope="test:jobs",
            entries=entries,
            publisher_id="node1",
        )
        json_str = manifest.to_json()
        restored = ShardManifest.from_json(json_str)

        assert restored.scope == "test:jobs"
        assert len(restored.entries) == 2
        assert restored.entries[0].shard_id == "a1"
        assert restored.publisher_id == "node1"

    def test_manifest_id_deterministic(self):
        entries = [ShardLocation(shard_id="a1", cid="QmA")]
        m1 = ShardManifest(scope="test", entries=entries, publisher_id="n1")
        m2 = ShardManifest(scope="test", entries=entries, publisher_id="n1")
        assert m1.manifest_id == m2.manifest_id

    def test_manifest_id_changes_with_content(self):
        e1 = [ShardLocation(shard_id="a1", cid="QmA")]
        e2 = [ShardLocation(shard_id="b2", cid="QmB")]
        m1 = ShardManifest(scope="test", entries=e1, publisher_id="n1")
        m2 = ShardManifest(scope="test", entries=e2, publisher_id="n1")
        assert m1.manifest_id != m2.manifest_id

    def test_empty_manifest(self):
        m = ShardManifest(scope="empty", entries=[])
        d = m.to_dict()
        assert d["entries"] == []
        restored = ShardManifest.from_dict(d)
        assert restored.entries == []


# === CID Map Tests (via IPFSBackend internals) ===

class TestCIDMap:
    def test_load_empty(self):
        with tempfile.TemporaryDirectory() as td:
            cid_map_path = Path(td) / "cid_map.json"
            assert not cid_map_path.exists()
            # Simulate what IPFSBackend._load_cid_map does
            result = json.loads(cid_map_path.read_text()) if cid_map_path.exists() else {}
            assert result == {}

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as td:
            cid_map_path = Path(td) / "cid_map.json"
            data = {"abc123": "QmXyz", "def456": "QmAbc"}
            cid_map_path.write_text(json.dumps(data), encoding="utf-8")
            loaded = json.loads(cid_map_path.read_text(encoding="utf-8"))
            assert loaded == data


# === ShardStore Fallback Tests ===

class TestShardStoreFallback:
    def test_get_local_hit_no_resolver_call(self):
        """When shard is local, resolver is not called."""
        with tempfile.TemporaryDirectory() as td:
            resolver = MagicMock()
            store = ShardStore(td, resolver=resolver)
            shard = _make_shard()
            store.put(shard)

            result = store.get(shard.shard_id)
            assert result is not None
            assert result.shard_id == shard.shard_id
            resolver.resolve.assert_not_called()

    def test_get_falls_back_to_resolver(self):
        """When shard is not local, resolver is called."""
        with tempfile.TemporaryDirectory() as td:
            shard = _make_shard()
            resolver = MagicMock()
            resolver.resolve.return_value = shard
            store = ShardStore(td, resolver=resolver)

            result = store.get(shard.shard_id)
            assert result is not None
            assert result.shard_id == shard.shard_id
            resolver.resolve.assert_called_once_with(shard.shard_id)

            # Should be cached locally now
            resolver.reset_mock()
            result2 = store.get(shard.shard_id)
            assert result2 is not None
            resolver.resolve.assert_not_called()

    def test_get_resolver_returns_none(self):
        """When resolver also can't find it, get() returns None."""
        with tempfile.TemporaryDirectory() as td:
            resolver = MagicMock()
            resolver.resolve.return_value = None
            store = ShardStore(td, resolver=resolver)

            result = store.get("nonexistent")
            assert result is None

    def test_get_no_resolver(self):
        """Without resolver, missing shard returns None (original behavior)."""
        with tempfile.TemporaryDirectory() as td:
            store = ShardStore(td)
            assert store.get("nonexistent") is None

    def test_get_encrypted_falls_back(self):
        """Encrypted shard falls back to resolver."""
        with tempfile.TemporaryDirectory() as td:
            shard = _make_shard()
            key = generate_job_key()
            encrypted = encrypt_shard(shard, key)

            resolver = MagicMock()
            resolver.resolve_encrypted.return_value = encrypted
            store = ShardStore(td, resolver=resolver)

            result = store.get_encrypted(shard.shard_id)
            assert result is not None
            assert result.shard_id == shard.shard_id
            resolver.resolve_encrypted.assert_called_once()

            # Should be cached
            resolver.reset_mock()
            result2 = store.get_encrypted(shard.shard_id)
            assert result2 is not None
            resolver.resolve_encrypted.assert_not_called()

    def test_get_sealed_falls_back(self):
        """Sealed shard falls back to resolver."""
        pytest.importorskip("nacl")
        from spiritwriter.trace.sealed import seal_shard, generate_owner_keypair

        with tempfile.TemporaryDirectory() as td:
            shard = _make_shard()
            keypair = generate_owner_keypair()
            sealed = seal_shard(shard, keypair.public_key)

            resolver = MagicMock()
            resolver.resolve_sealed.return_value = sealed
            store = ShardStore(td, resolver=resolver)

            result = store.get_sealed(shard.shard_id)
            assert result is not None
            assert result.shard_id == shard.shard_id
            resolver.resolve_sealed.assert_called_once()


# === Exception Tests ===

class TestExceptions:
    def test_network_unavailable(self):
        with pytest.raises(NetworkUnavailable):
            raise NetworkUnavailable("Kubo not running")

    def test_network_timeout(self):
        with pytest.raises(NetworkTimeout):
            raise NetworkTimeout("Request timed out")

    def test_integrity_error(self):
        with pytest.raises(IntegrityError):
            raise IntegrityError("Shard ID mismatch")


# === Protocol Check ===

class TestProtocol:
    def test_mock_satisfies_protocol(self):
        """A mock with the right methods satisfies NetworkResolver."""
        mock = MagicMock()
        # runtime_checkable Protocol checks for method existence
        assert isinstance(mock, NetworkResolver)
