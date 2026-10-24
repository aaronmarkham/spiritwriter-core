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
from spiritwriter.fabric.network import ShardManifest, IntegrityError, NetworkUnavailable
from spiritwriter.fabric.backends.s3 import S3Backend, S3Config, S3ConfigurationError


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
    """Minimal in-memory S3 client covering the S3Backend surface.

    Set ``get_error`` to an error code (e.g. "AccessDenied", "SlowDown") to
    make every ``get_object`` raise that error — used to simulate IAM/
    throttling/transport failures that must PROPAGATE from resolve*, not be
    swallowed as "not found". A ``get_object`` against a bucket the client
    doesn't know raises ``NoSuchBucket`` (matching real S3), which the backend
    must surface as a configuration error rather than "not found".
    """

    def __init__(self, existing_buckets=("test-bucket",)):
        self.store: dict[tuple[str, str], bytes] = {}
        self._buckets = set(existing_buckets)
        self.exceptions = _Exceptions()
        self.get_error: str | None = None
        self.put_calls: list[dict] = []

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.put_calls.append({"Bucket": Bucket, "Key": Key, **kwargs})
        if Bucket not in self._buckets:
            raise _ClientError("NoSuchBucket")
        object_key = (Bucket, Key)
        if kwargs.get("IfNoneMatch") == "*" and object_key in self.store:
            raise _ClientError("PreconditionFailed")
        self.store[object_key] = Body
        return {}

    def get_object(self, Bucket, Key, **kwargs):
        if self.get_error is not None:
            raise _ClientError(self.get_error)
        if Bucket not in self._buckets:
            raise _ClientError("NoSuchBucket")
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
        monkeypatch.setenv("SPIRITWRITER_S3_REGION", "us-west-2")
        monkeypatch.setenv("SPIRITWRITER_S3_ENDPOINT", "https://s3.example.test")
        cfg = S3Config.from_env()
        assert cfg.bucket == "env-bucket"
        assert cfg.prefix == "envprefix"
        assert cfg.region == "us-west-2"
        assert cfg.endpoint_url == "https://s3.example.test"

    def test_config_from_env_normalizes_empty_region_endpoint_to_none(self, monkeypatch):
        # from_env uses `os.environ.get(...) or None`, so an empty-string env var
        # must normalize to None — not be passed straight to boto3 (an empty
        # endpoint_url="" would break client construction). Region/endpoint are
        # unset here, and an explicitly-empty endpoint must also read as None.
        monkeypatch.setenv("SPIRITWRITER_S3_BUCKET", "env-bucket")
        monkeypatch.setenv("SPIRITWRITER_S3_ENDPOINT", "")
        monkeypatch.delenv("SPIRITWRITER_S3_REGION", raising=False)
        cfg = S3Config.from_env()
        assert cfg.region is None
        assert cfg.endpoint_url is None

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
        # An S3 object is REMOTE (L2), so local=False — intentionally diverges
        # from IPFSBackend, which sets local=True (its bug, tracked separately).
        assert loc.local is False
        assert loc.pinned is True

    def test_resolve_by_shard_id(self, backend):
        shard = _make_shard()
        backend.publish(shard)
        resolved = backend.resolve(shard.shard_id)
        assert resolved is not None
        assert resolved.shard_id == shard.shard_id
        assert resolved.atoms[0].text == "s3 test fact"

    def test_resolve_unknown_returns_none(self, backend):
        assert backend.resolve("0" * 64) is None

    def test_publish_idempotent(self, backend, client):
        shard = _make_shard()
        loc1 = backend.publish(shard)
        loc2 = backend.publish(shard)
        assert loc1.cid == loc2.cid
        assert all(call["IfNoneMatch"] == "*" for call in client.put_calls)

    def test_publish_rejects_different_bytes_under_same_id(self, backend, client):
        first = _make_shard()
        first.created_at = "2026-01-01T00:00:00Z"
        first.meta = {"envelope": 1}
        second = _make_shard()
        second.created_at = first.created_at
        second.meta = {"envelope": 2}
        assert first.shard_id == second.shard_id
        backend.publish(first)
        object_key = ("test-bucket", backend.key_for(first.shard_id))
        before = client.store[object_key]

        with pytest.raises(IntegrityError, match="existing bytes differ"):
            backend.publish(second)
        assert client.store[object_key] == before

    @pytest.mark.parametrize("shard_id", ["short", "A" * 64, "../" + "a" * 61])
    def test_resolve_rejects_noncanonical_shard_id(self, backend, shard_id):
        with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
            backend.resolve(shard_id)

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
        assert backend.resolve_encrypted("0" * 64) is None


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

    def test_resolve_manifest_integrity_mismatch_raises(self, backend, client):
        # Publish a genuine manifest, then overwrite the object at its key
        # with a DIFFERENT manifest's bytes. The parsed manifest_id no longer
        # matches the key it was fetched from -> IntegrityError.
        loc = backend.publish(_make_shard("m"))
        manifest = ShardManifest(scope="scope-a", entries=[loc], publisher_id="pub-a")
        key = backend.publish_manifest(manifest)

        tampered = ShardManifest(scope="scope-b", entries=[loc], publisher_id="pub-b")
        assert tampered.manifest_id != manifest.manifest_id
        client.store[("test-bucket", key)] = tampered.to_json().encode("utf-8")

        with pytest.raises(IntegrityError):
            backend.resolve_manifest(key)


class TestErrorHandling:
    """Finding 1 & 2 — a misconfigured bucket or a transport/IAM error must
    fail loudly on the resolve* path, never read as an empty store."""

    # --- Finding 1: NoSuchBucket is a config error, not "not found" ---

    def test_resolve_missing_bucket_raises_config_error(self, client):
        backend = S3Backend(bucket="does-not-exist", prefix="sw", client=client)
        with pytest.raises(S3ConfigurationError):
            backend.resolve("d" * 64)

    def test_resolve_encrypted_missing_bucket_raises_config_error(self, client):
        backend = S3Backend(bucket="does-not-exist", prefix="sw", client=client)
        with pytest.raises(S3ConfigurationError):
            backend.resolve_encrypted("d" * 64)

    def test_resolve_manifest_missing_bucket_raises_config_error(self, client):
        backend = S3Backend(bucket="does-not-exist", prefix="sw", client=client)
        with pytest.raises(S3ConfigurationError):
            backend.resolve_manifest("sw/manifests/whatever.json")

    def test_publish_missing_bucket_raises_config_error(self, client):
        backend = S3Backend(bucket="does-not-exist", prefix="sw", client=client)
        with pytest.raises(S3ConfigurationError):
            backend.publish(_make_shard())

    # --- Finding 2: IAM/throttling/transport errors propagate ---

    @pytest.mark.parametrize("code", ["AccessDenied", "SlowDown", "RequestTimeout"])
    def test_resolve_propagates_transport_error(self, backend, client, code):
        client.get_error = code
        with pytest.raises(NetworkUnavailable):
            backend.resolve("d" * 64)

    @pytest.mark.parametrize("code", ["AccessDenied", "SlowDown"])
    def test_resolve_encrypted_propagates_transport_error(self, backend, client, code):
        client.get_error = code
        with pytest.raises(NetworkUnavailable):
            backend.resolve_encrypted("d" * 64)

    def test_resolve_sealed_propagates_transport_error(self, backend, client):
        client.get_error = "AccessDenied"
        with pytest.raises(NetworkUnavailable):
            backend.resolve_sealed("d" * 64)

    def test_resolve_manifest_propagates_transport_error(self, backend, client):
        client.get_error = "AccessDenied"
        with pytest.raises(NetworkUnavailable):
            backend.resolve_manifest("sw/manifests/whatever.json")

    def test_resolve_by_cid_propagates_transport_error(self, backend, client):
        client.get_error = "AccessDenied"
        with pytest.raises(NetworkUnavailable):
            backend.resolve_by_cid("sw/shards/de/adbeef00.json")

    # --- Finding 2 boundary: a genuine NoSuchKey still returns None ---

    def test_resolve_genuine_not_found_still_returns_none(self, backend):
        assert backend.resolve("d" * 64) is None
        assert backend.resolve_encrypted("d" * 64) is None


class TestAvailability:
    def test_is_available_true(self, backend):
        assert backend.is_available() is True

    def test_is_available_false_missing_bucket(self, client):
        backend = S3Backend(bucket="does-not-exist", client=client)
        assert backend.is_available() is False
