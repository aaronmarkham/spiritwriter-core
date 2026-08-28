"""Unit tests for S3Backend — no real AWS, no network.

Uses an in-memory fake boto3 S3 client (FakeS3Client) that implements just
the surface S3Backend touches: put_object, get_object, head_bucket, and a
typed ``exceptions.NoSuchKey``. This keeps the tests deterministic and
offline, mirroring how test_network.py mocks the NetworkResolver rather
than standing up Kubo.

The IPFS backend has integration tests gated on a live Kubo node
(test_ipfs_backend.py); the S3 analog is fully mockable, so these run
everywhere.
"""

import io

import pytest

from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.fabric.crypto import encrypt_shard, generate_job_key
from spiritwriter.fabric.network import ShardManifest
from spiritwriter.fabric.backends.s3 import S3Backend, S3Config


# === Fake S3 client ===

class _NoSuchKey(Exception):
    """Stand-in for botocore's client.exceptions.NoSuchKey."""


class _ClientError(Exception):
    """Stand-in for a botocore ClientError carrying an error code."""

    def __init__(self, code: str):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class _Exceptions:
    NoSuchKey = _NoSuchKey


class FakeS3Client:
    """Minimal in-memory S3 client covering the S3Backend surface."""

    def __init__(self, existing_buckets=("test-bucket",)):
        self.store: dict[tuple[str, str], bytes] = {}
        self._buckets = set(existing_buckets)
        self.exceptions = _Exceptions()

    def put_object(self, Bucket, Key, Body, **kwargs):
        if Bucket not in self._buckets:
            raise _ClientError("NoSuchBucket")
        self.store[(Bucket, Key)] = Body
        return {}

    def get_object(self, Bucket, Key, **kwargs):
        try:
            body = self.store[(Bucket, Key)]
        except KeyError:
            raise self.exceptions.NoSuchKey()
        return {"Body": io.BytesIO(body)}

    def head_bucket(self, Bucket, **kwargs):
        if Bucket not in self._buckets:
            raise _ClientError("404")
        return {}


def _make_shard(text: str = "s3 test fact", scope: str = "test:s3") -> MemoryShard:
    return MemoryShard(
        atoms=[ShardAtom(text=text, kind=AtomKind.FACT)],
        scope=scope,
        origin="test-agent",
    )


@pytest.fixture
def client():
    return FakeS3Client()


@pytest.fixture
def backend(client):
    return S3Backend(bucket="test-bucket", prefix="sw", client=client)


# === Construction / config ===

class TestConstruction:
    def test_requires_bucket(self, client):
        with pytest.raises(ValueError):
            S3Backend(client=client)

    def test_config_from_env(self, monkeypatch):
        monkeypatch.setenv("SPIRITWRITER_S3_BUCKET", "env-bucket")
        monkeypatch.setenv("SPIRITWRITER_S3_PREFIX", "envprefix")
        cfg = S3Config.from_env()
        assert cfg.bucket == "env-bucket"
        assert cfg.prefix == "envprefix"

    def test_key_layout_git_style(self, backend):
        shard = _make_shard()
        key = backend.key_for(shard.shard_id)
        # {prefix}/shards/{id[:2]}/{id[2:]}.json
        assert key == f"sw/shards/{shard.shard_id[:2]}/{shard.shard_id[2:]}.json"


# === Publish / resolve ===

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
        assert resolved.atoms[0].text == "s3 test fact"

    def test_resolve_unknown_returns_none(self, backend):
        assert backend.resolve("nonexistent0000") is None

    def test_publish_idempotent(self, backend):
        shard = _make_shard()
        loc1 = backend.publish(shard)
        loc2 = backend.publish(shard)
        assert loc1.cid == loc2.cid

    def test_resolve_by_cid(self, backend):
        shard = _make_shard()
        loc = backend.publish(shard)
        raw = backend.resolve_by_cid(loc.cid)
        assert b"s3 test fact" in raw

    def test_cid_is_the_object_key(self, backend):
        shard = _make_shard()
        loc = backend.publish(shard)
        assert loc.cid == backend.key_for(shard.shard_id)


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

    def test_resolve_encrypted_missing_returns_none(self, backend):
        assert backend.resolve_encrypted("nonexistent0000") is None


class TestSealedShards:
    def test_publish_resolve_sealed(self, backend):
        pytest.importorskip("nacl")
        from spiritwriter.fabric.sealed import seal_shard, generate_owner_keypair

        shard = _make_shard("sealed content")
        keypair = generate_owner_keypair()
        sealed = seal_shard(shard, keypair.public_key)

        loc = backend.publish_sealed(sealed)
        assert loc.cid is not None

        resolved = backend.resolve_sealed(shard.shard_id)
        assert resolved is not None
        assert resolved.shard_id == shard.shard_id


class TestPinning:
    def test_pin_unpin_return_true(self, backend):
        shard = _make_shard("pin test")
        loc = backend.publish(shard)
        # S3 has no pinning; both are documented no-ops that report success.
        assert backend.unpin(loc.cid) is True
        assert backend.pin(loc.cid) is True

    def test_unpin_does_not_delete(self, backend):
        shard = _make_shard("still here")
        backend.publish(shard)
        backend.unpin(backend.key_for(shard.shard_id))
        assert backend.resolve(shard.shard_id) is not None


class TestManifest:
    def test_publish_resolve_manifest(self, backend):
        shard1 = _make_shard("manifest entry 1")
        shard2 = _make_shard("manifest entry 2", scope="test:s3-2")
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

    def test_resolve_manifest_missing_returns_none(self, backend):
        assert backend.resolve_manifest("sw/manifests/nope.json") is None


class TestAvailability:
    def test_is_available_true(self, backend):
        assert backend.is_available() is True

    def test_is_available_false_missing_bucket(self, client):
        backend = S3Backend(bucket="does-not-exist", client=client)
        assert backend.is_available() is False
