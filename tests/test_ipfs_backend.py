"""Integration tests for IPFSBackend — requires a running local Kubo node.

Run with: python -m pytest tests/test_ipfs_backend.py -v -m ipfs
Skip in CI without Kubo: tests are marked @pytest.mark.ipfs

Note: These tests use require_private_swarm=False since the test
Kubo node may be on the public network. Private swarm verification
is tested separately in test_network.py via mocks.
"""

import tempfile

import pytest

from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.trace.crypto import encrypt_shard, generate_job_key
from spiritwriter.trace.network import ShardLocation, ShardManifest, IntegrityError

# Guard: skip entire module if requests is not installed
requests = pytest.importorskip("requests")

from spiritwriter.trace.backends.ipfs import IPFSBackend, IPFSConfig


def _make_shard(text: str = "ipfs test fact", scope: str = "test:ipfs") -> MemoryShard:
    return MemoryShard(
        atoms=[ShardAtom(text=text, kind=AtomKind.FACT)],
        scope=scope,
        origin="test-agent",
    )


def _kubo_available() -> bool:
    """Check if Kubo is running on the default port."""
    try:
        resp = requests.post("http://127.0.0.1:5001/api/v0/id", timeout=3)
        return resp.status_code == 200
    except Exception:
        return False


# Skip all tests in this module if Kubo is not available
pytestmark = [
    pytest.mark.ipfs,
    pytest.mark.skipif(not _kubo_available(), reason="Kubo node not running"),
]


@pytest.fixture
def backend():
    with tempfile.TemporaryDirectory() as td:
        config = IPFSConfig(require_private_swarm=False)
        yield IPFSBackend(store_root=td, config=config)


class TestPublishResolve:
    def test_publish_plaintext_shard(self, backend):
        shard = _make_shard()
        loc = backend.publish(shard)
        assert loc.shard_id == shard.shard_id
        assert loc.cid is not None
        assert loc.local is True

    def test_resolve_by_shard_id(self, backend):
        shard = _make_shard()
        backend.publish(shard)
        resolved = backend.resolve(shard.shard_id)
        assert resolved is not None
        assert resolved.shard_id == shard.shard_id
        assert resolved.atoms[0].text == "ipfs test fact"

    def test_resolve_unknown_returns_none(self, backend):
        assert backend.resolve("nonexistent") is None

    def test_publish_idempotent(self, backend):
        shard = _make_shard()
        loc1 = backend.publish(shard)
        loc2 = backend.publish(shard)
        assert loc1.cid == loc2.cid

    def test_resolve_by_cid(self, backend):
        shard = _make_shard()
        loc = backend.publish(shard)
        raw = backend.resolve_by_cid(loc.cid)
        assert b"ipfs test fact" in raw


class TestEncryptedShards:
    def test_publish_resolve_encrypted(self, backend):
        shard = _make_shard("encrypted content")
        key = generate_job_key()
        encrypted = encrypt_shard(shard, key)

        loc = backend.publish_encrypted(encrypted)
        assert loc.cid is not None

        resolved = backend.resolve_encrypted(shard.shard_id)
        assert resolved is not None
        assert resolved.shard_id == shard.shard_id


class TestSealedShards:
    def test_publish_resolve_sealed(self, backend):
        nacl = pytest.importorskip("nacl")
        from spiritwriter.trace.sealed import seal_shard, generate_owner_keypair

        shard = _make_shard("sealed content")
        keypair = generate_owner_keypair()
        sealed = seal_shard(shard, keypair.public_key)

        loc = backend.publish_sealed(sealed)
        assert loc.cid is not None

        resolved = backend.resolve_sealed(shard.shard_id)
        assert resolved is not None
        assert resolved.shard_id == shard.shard_id


class TestPinning:
    def test_pin_unpin(self, backend):
        shard = _make_shard("pin test")
        loc = backend.publish(shard)

        assert backend.unpin(loc.cid) is True
        assert backend.pin(loc.cid) is True


class TestManifest:
    def test_publish_resolve_manifest(self, backend):
        shard1 = _make_shard("manifest entry 1")
        shard2 = _make_shard("manifest entry 2")
        loc1 = backend.publish(shard1)
        loc2 = backend.publish(shard2)

        manifest = ShardManifest(
            scope="test:manifest",
            entries=[loc1, loc2],
            publisher_id="test-node",
        )
        cid = backend.publish_manifest(manifest)
        assert cid is not None

        resolved = backend.resolve_manifest(cid)
        assert resolved is not None
        assert resolved.scope == "test:manifest"
        assert len(resolved.entries) == 2


class TestAvailability:
    def test_is_available(self, backend):
        assert backend.is_available() is True

    def test_unavailable_config(self):
        with tempfile.TemporaryDirectory() as td:
            config = IPFSConfig(api_url="http://127.0.0.1:59999", require_private_swarm=False)
            backend = IPFSBackend(store_root=td, config=config)
            assert backend.is_available() is False
