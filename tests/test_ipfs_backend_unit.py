"""Unit tests for IPFSBackend — no Kubo node, no network.

The Kubo-gated integration tests live in ``test_ipfs_backend.py`` (marked
``@pytest.mark.ipfs``, skipped without a running node). These tests instead
exercise the backend's *correctness posture* offline, mirroring
``test_s3_backend.py``'s error-handling tests.

IPFSBackend has no ``client=`` injection like S3Backend; its HTTP boundary is
``_add_bytes`` (Kubo ``add``) and ``_cat_cid`` (Kubo ``cat``). We inject a
``FakeKubo`` at exactly that boundary — a content-addressed in-memory store
with a ``cat_error`` switch that simulates a node-down / timeout / unreachable
failure. ``pin_by_default=False`` keeps ``publish`` from touching the real
network (its pin call is not part of what we test here).
"""

import json

import pytest

from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.fabric.crypto import encrypt_shard, generate_job_key
from spiritwriter.fabric.network import (
    ShardManifest,
    IntegrityError,
    NetworkUnavailable,
    NetworkTimeout,
)

# requests is imported at IPFSBackend construction (_require_requests); skip the
# whole module if the optional network extra isn't installed.
pytest.importorskip("requests")

from spiritwriter.fabric.backends.ipfs import IPFSBackend, IPFSConfig


# === Fake Kubo node (injected at the _add_bytes / _cat_cid boundary) ===

class FakeKubo:
    """Minimal in-memory stand-in for a Kubo node.

    ``add`` content-addresses bytes to a fake CID and stores them; ``cat``
    returns them. Set ``cat_error`` to an exception instance to make every
    ``cat`` raise it — used to simulate transport/timeout/unreachable failures
    that must PROPAGATE from resolve*, not be swallowed into ``None``.
    """

    def __init__(self):
        self.blocks: dict[str, bytes] = {}
        self._counter = 0
        self.cat_error: Exception | None = None

    def add(self, data: bytes) -> str:
        self._counter += 1
        cid = f"Qmfake{self._counter:04d}"
        self.blocks[cid] = data
        return cid

    def cat(self, cid: str) -> bytes:
        if self.cat_error is not None:
            raise self.cat_error
        try:
            return self.blocks[cid]
        except KeyError:  # a block genuinely absent from the node -> transport miss
            raise NetworkUnavailable(f"no block {cid}")


def _make_shard(text: str = "ipfs unit fact", scope: str = "test:ipfs-unit") -> MemoryShard:
    return MemoryShard(
        atoms=[ShardAtom(text=text, kind=AtomKind.FACT)],
        scope=scope,
        origin="test-agent",
    )


@pytest.fixture
def kubo():
    return FakeKubo()


@pytest.fixture
def backend(kubo, tmp_path):
    b = IPFSBackend(
        store_root=tmp_path,
        config=IPFSConfig(require_private_swarm=False, pin_by_default=False),
    )
    # Inject the fake node at the HTTP boundary.
    b._add_bytes = kubo.add
    b._cat_cid = kubo.cat
    return b


# === Publish / resolve round-trips (sanity that the fake is wired right) ===

class TestPublishResolveRoundTrip:
    def test_publish_then_resolve(self, backend):
        shard = _make_shard()
        backend.publish(shard)
        resolved = backend.resolve(shard.shard_id)
        assert resolved is not None
        assert resolved.shard_id == shard.shard_id
        assert resolved.atoms[0].text == "ipfs unit fact"

    def test_publish_idempotent(self, backend):
        shard = _make_shard()
        loc1 = backend.publish(shard)
        loc2 = backend.publish(shard)
        assert loc1.cid == loc2.cid


# === Fix 2: an IPFS object is REMOTE -> ShardLocation.local is False ===

class TestLocalFlagIsFalse:
    def test_publish_sets_local_false(self, backend):
        loc = backend.publish(_make_shard())
        assert loc.local is False

    def test_publish_idempotent_return_sets_local_false(self, backend):
        backend.publish(_make_shard("dupe"))
        loc = backend.publish(_make_shard("dupe"))  # same content -> cid_map hit
        assert loc.local is False

    def test_publish_encrypted_sets_local_false(self, backend):
        encrypted = encrypt_shard(_make_shard("enc"), generate_job_key())
        loc = backend.publish_encrypted(encrypted)
        assert loc.local is False

    def test_publish_sealed_sets_local_false(self, backend):
        pytest.importorskip("nacl")
        from spiritwriter.fabric.sealed import seal_shard, generate_owner_keypair

        keypair = generate_owner_keypair()
        sealed = seal_shard(_make_shard("sealed"), keypair.public_key)
        loc = backend.publish_sealed(sealed)
        assert loc.local is False

    def test_publish_public_sets_local_false(self, backend, kubo):
        # publish_public uses _add_bytes_raw / _api_raw, not _add_bytes; inject there too.
        backend._add_bytes_raw = kubo.add
        loc = backend.publish_public(_make_shard("public"), confirm_public=True)
        assert loc.local is False


# === Fix 1: transport errors PROPAGATE from resolve*; only a genuine
#     not-found (cid_map miss) returns None ===

class TestResolvePropagatesTransportError:
    @pytest.mark.parametrize(
        "err", [NetworkUnavailable("kubo down"), NetworkTimeout("kubo slow")]
    )
    def test_resolve_propagates(self, backend, kubo, err):
        shard = _make_shard()
        backend.publish(shard)  # populate cid_map so we get past the miss check
        kubo.cat_error = err
        with pytest.raises((NetworkUnavailable, NetworkTimeout)):
            backend.resolve(shard.shard_id)

    def test_resolve_encrypted_propagates(self, backend, kubo):
        encrypted = encrypt_shard(_make_shard("enc"), generate_job_key())
        backend.publish_encrypted(encrypted)
        kubo.cat_error = NetworkUnavailable("down")
        with pytest.raises(NetworkUnavailable):
            backend.resolve_encrypted(encrypted.shard_id)

    def test_resolve_sealed_propagates(self, backend, kubo):
        pytest.importorskip("nacl")
        from spiritwriter.fabric.sealed import seal_shard, generate_owner_keypair

        keypair = generate_owner_keypair()
        sealed = seal_shard(_make_shard("sealed"), keypair.public_key)
        backend.publish_sealed(sealed)
        kubo.cat_error = NetworkTimeout("slow")
        with pytest.raises(NetworkTimeout):
            backend.resolve_sealed(sealed.shard_id)

    def test_resolve_by_cid_propagates(self, backend, kubo):
        kubo.cat_error = NetworkUnavailable("down")
        with pytest.raises(NetworkUnavailable):
            backend.resolve_by_cid("Qmwhatever")


class TestResolveGenuineNotFoundReturnsNone:
    def test_resolve_unknown_returns_none(self, backend, kubo):
        # cid_map miss -> None, and _cat_cid is never even called.
        kubo.cat_error = NetworkUnavailable("must not be reached")
        assert backend.resolve("nonexistent0000") is None

    def test_resolve_encrypted_unknown_returns_none(self, backend, kubo):
        kubo.cat_error = NetworkUnavailable("must not be reached")
        assert backend.resolve_encrypted("nonexistent0000") is None

    def test_resolve_sealed_unknown_returns_none(self, backend, kubo):
        kubo.cat_error = NetworkUnavailable("must not be reached")
        assert backend.resolve_sealed("nonexistent0000") is None


# === Fix 1 + Fix 3: resolve_manifest propagates transport errors AND verifies
#     the manifest's content address ===

class TestResolveManifest:
    def test_publish_resolve_manifest_roundtrip(self, backend):
        loc1 = backend.publish(_make_shard("m1"))
        loc2 = backend.publish(_make_shard("m2", scope="test:ipfs-unit-2"))
        manifest = ShardManifest(
            scope="test:manifest", entries=[loc1, loc2], publisher_id="test-node"
        )
        cid = backend.publish_manifest(manifest)
        resolved = backend.resolve_manifest(cid)
        assert resolved is not None
        assert resolved.scope == "test:manifest"
        assert len(resolved.entries) == 2

    def test_resolve_manifest_propagates_transport_error(self, backend, kubo):
        kubo.cat_error = NetworkUnavailable("down")
        with pytest.raises(NetworkUnavailable):
            backend.resolve_manifest("Qmwhatever")

    def test_resolve_manifest_integrity_mismatch_raises(self, backend, kubo):
        # Store bytes whose declared manifest_id no longer matches the content
        # it addresses: keep the original manifest_id field but mutate `scope`
        # (which changes the recomputed content address) -> IntegrityError.
        loc = backend.publish(_make_shard("m"))
        manifest = ShardManifest(scope="scope-a", entries=[loc], publisher_id="pub-a")
        d = manifest.to_dict()
        assert d["manifest_id"] == manifest.manifest_id
        d["scope"] = "scope-b"  # recomputed id now differs; declared id left stale
        tampered = json.dumps(d, ensure_ascii=False).encode("utf-8")
        cid = "Qmtampered"
        kubo.blocks[cid] = tampered

        with pytest.raises(IntegrityError):
            backend.resolve_manifest(cid)
