"""Regression tests for immutable and scope-safe shard storage."""

from __future__ import annotations

import json
import threading

import pytest

from spiritwriter.fabric import store as store_mod
from spiritwriter.fabric.crypto import (
    DecryptionError,
    decrypt_shard,
    encrypt_shard,
    generate_job_key,
)
from spiritwriter.fabric.entitlement import Capability, create_entitlement
from spiritwriter.fabric.network import IntegrityError
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, validate_shard_id
from spiritwriter.fabric.store import ShardStore


ID_A = "a" * 64
ID_B = "b" * 64
ID_C = "c" * 64


def _shard(*, meta=None, scope="project:allowed") -> MemoryShard:
    return MemoryShard(
        atoms=[ShardAtom(text="security test")],
        scope=scope,
        origin="agent:test",
        created_at="2026-01-01T00:00:00Z",
        meta=meta or {},
    )


class TestShardIdValidation:
    @pytest.mark.parametrize(
        "shard_id",
        [
            "",
            "a" * 63,
            "a" * 65,
            "A" * 64,
            "g" * 64,
            "../" + "a" * 61,
            "aa/tmp/" + "a" * 57,
            None,
        ],
    )
    def test_rejects_noncanonical_ids(self, shard_id):
        with pytest.raises(ValueError, match="64 lowercase hexadecimal"):
            validate_shard_id(shard_id)

    def test_accepts_legacy_sha256_hex(self):
        assert validate_shard_id(ID_A) == ID_A

    @pytest.mark.parametrize(
        "operation",
        [
            lambda store: store.get("../" + "a" * 61),
            lambda store: store.has("A" * 64),
            lambda store: store.delete("short"),
            lambda store: store.get_encrypted("aa/tmp/" + "a" * 57),
            lambda store: store.has_encrypted("g" * 64),
            lambda store: store.get_sealed("a" * 63),
            lambda store: store.has_sealed("a" * 65),
        ],
    )
    def test_store_entry_points_reject_invalid_ids(self, tmp_path, operation):
        with pytest.raises(ValueError):
            operation(ShardStore(tmp_path / "store"))

    def test_symlinked_top_level_shards_directory_is_rejected(self, tmp_path):
        root = tmp_path / "store"
        outside = tmp_path / "outside"
        root.mkdir()
        outside.mkdir()
        (root / "shards").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="not a symlink"):
            ShardStore(root)

    def test_symlinked_prefix_cannot_escape_object_root(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        outside = tmp_path / "outside"
        outside.mkdir()
        (store.shards_dir / "aa").symlink_to(outside, target_is_directory=True)

        with pytest.raises(ValueError, match="Unsafe shard prefix|unsafe or unreadable"):
            store.get(ID_A)
        assert list(outside.iterdir()) == []

    def test_symlinked_object_is_not_followed(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        outside = tmp_path / "outside.json"
        outside.write_text(_shard().to_json(), encoding="utf-8")
        prefix = store.shards_dir / ID_A[:2]
        prefix.mkdir()
        (prefix / f"{ID_A[2:]}.json").symlink_to(outside)

        with pytest.raises(ValueError, match="Unsafe shard object"):
            store.get(ID_A)

    def test_portable_fallback_rejects_object_symlink(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            ShardStore,
            "_secure_dir_fd_supported",
            property(lambda self: False),
        )
        store = ShardStore(tmp_path / "store")
        outside = tmp_path / "outside.json"
        outside.write_text(_shard().to_json(), encoding="utf-8")
        prefix = store.shards_dir / ID_A[:2]
        prefix.mkdir()
        (prefix / f"{ID_A[2:]}.json").symlink_to(outside)

        with pytest.raises(ValueError, match="Unsafe shard object"):
            store.get(ID_A)


class TestStrictLocalReads:
    def test_plaintext_requires_declared_requested_id(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        shard = _shard()
        payload = shard.to_dict()
        del payload["shard_id"]
        path = store._shard_path(ID_A)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(IntegrityError, match="payload declares None"):
            store.get(ID_A)

    def test_plaintext_rejects_different_declared_id_at_path(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        shard = _shard()
        assert shard.shard_id != ID_A
        path = store._shard_path(ID_A)
        path.parent.mkdir(parents=True)
        path.write_text(shard.to_json(), encoding="utf-8")

        with pytest.raises(IntegrityError, match="Plaintext shard ID mismatch"):
            store.get(ID_A)

    def test_plaintext_content_mismatch_is_integrity_error(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        shard = _shard()
        payload = shard.to_dict()
        payload["atoms"][0]["text"] = "tampered"
        path = store._shard_path(shard.shard_id)
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")

        with pytest.raises(IntegrityError, match="fails its content address"):
            store.get(shard.shard_id)

    def test_encrypted_rejects_different_wrapper_id_at_path(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        encrypted = encrypt_shard(_shard(), generate_job_key())
        assert encrypted.shard_id != ID_A
        path = store._encrypted_path(ID_A)
        path.parent.mkdir(parents=True)
        path.write_text(encrypted.to_json(), encoding="utf-8")

        with pytest.raises(IntegrityError, match="Encrypted shard ID mismatch"):
            store.get_encrypted(ID_A)

    def test_sealed_rejects_different_wrapper_id_at_path(self, tmp_path):
        pytest.importorskip("nacl")
        from spiritwriter.fabric.sealed import generate_owner_keypair, seal_shard

        store = ShardStore(tmp_path / "store")
        sealed = seal_shard(_shard(), generate_owner_keypair().public_key)
        assert sealed.shard_id != ID_A
        path = store._sealed_path(ID_A)
        path.parent.mkdir(parents=True)
        path.write_text(sealed.to_json(), encoding="utf-8")

        with pytest.raises(IntegrityError, match="Sealed shard ID mismatch"):
            store.get_sealed(ID_A)


class TestImmutableLocalObjects:
    def test_plaintext_reput_same_id_keeps_first_envelope(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        first = _shard(meta={"envelope": 1})
        second = _shard(meta={"envelope": 2})
        assert first.shard_id == second.shard_id
        store.put(first)
        before = store._shard_path(first.shard_id).read_bytes()

        assert store.put(second) == first.ref
        assert store._shard_path(first.shard_id).read_bytes() == before
        assert store.get(first.shard_id).meta == {"envelope": 1}

    def test_plaintext_reput_with_new_created_at_is_a_noop(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        first = _shard()
        second = _shard()
        second.created_at = "2026-02-02T00:00:00Z"
        assert first.shard_id == second.shard_id
        store.put(first)

        store.put(second)
        assert store.get(first.shard_id).created_at == first.created_at
        assert [s.shard_id for s in store.by_scope(first.scope)] == [first.shard_id]

    def test_plaintext_corrupt_object_is_replaced_on_put(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        shard = _shard()
        store.put(shard)
        path = store._shard_path(shard.shard_id)
        path.write_bytes(b'{"shard_id": "torn"')

        store.put(shard)
        assert path.read_bytes() == shard.to_json().encode("utf-8")
        assert store.get(shard.shard_id).shard_id == shard.shard_id

    def test_encrypted_reput_same_id_keeps_first_payload(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        key = generate_job_key()
        first = encrypt_shard(_shard(), key)
        second = encrypt_shard(_shard(), key)
        assert first.shard_id == second.shard_id
        store.put_encrypted(first)

        store.put_encrypted(second)
        assert store.get_encrypted(first.shard_id).encrypted_payload == first.encrypted_payload
    def test_encrypt_and_store_same_inner_shard_is_idempotent(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        shard = _shard()
        key = generate_job_key()
        first = store.encrypt_and_store(shard, key)
        second = store.encrypt_and_store(shard, key)

        assert second.to_json() == first.to_json()
        assert store.decrypt_and_get(shard.shard_id, key).to_json() == shard.to_json()

    def test_encrypt_and_store_rejects_different_legacy_envelope(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        key = generate_job_key()
        store.encrypt_and_store(_shard(meta={"version": 1}), key)

        with pytest.raises(IntegrityError, match="different envelope"):
            store.encrypt_and_store(_shard(meta={"version": 2}), key)


class TestReindexValidation:
    def test_reindex_skips_and_reports_malformed_and_mismatched_objects(
        self, tmp_path, caplog
    ):
        store = ShardStore(tmp_path / "store")
        good = _shard(scope="project:good")
        store.put(good)

        malformed_dir = store.shards_dir / "zz"
        malformed_dir.mkdir()
        (malformed_dir / "not-an-id.json").write_text(
            good.to_json(), encoding="utf-8"
        )

        wrong_path = store.shards_dir / ID_A[:2] / f"{ID_A[2:]}.json"
        wrong_path.parent.mkdir()
        wrong_path.write_text(good.to_json(), encoding="utf-8")

        tampered = _shard(scope="project:tampered")
        tampered_payload = tampered.to_dict()
        tampered_payload["atoms"][0]["text"] = "changed after addressing"
        tampered_path = (
            store.shards_dir
            / tampered.shard_id[:2]
            / f"{tampered.shard_id[2:]}.json"
        )
        tampered_path.parent.mkdir()
        tampered_path.write_text(json.dumps(tampered_payload), encoding="utf-8")

        result = store.reindex()

        assert len(result["skipped"]) == 3
        assert any("64 lowercase hexadecimal" in item for item in result["skipped"])
        assert any("ID mismatch" in item for item in result["skipped"])
        assert any("content address" in item for item in result["skipped"])
        assert store.list_scopes() == ["project:good"]
        assert "skipped 3" in caplog.text


class TestAtomicRefs:
    def test_set_ref_failure_preserves_previous_value(self, tmp_path, monkeypatch):
        store = ShardStore(tmp_path / "store")
        store.set_ref("head", ID_A)

        def fail_replace(*args, **kwargs):
            raise OSError("replace failed")

        monkeypatch.setattr(store_mod.os, "replace", fail_replace)
        with pytest.raises(OSError, match="replace failed"):
            store.set_ref("head", ID_B)
        assert store.get_ref("head") == ID_A

    def test_compare_and_set_create_update_and_stale(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        assert store.compare_and_set_ref("head", None, ID_A) is True
        assert store.compare_and_set_ref("head", None, ID_B) is False
        assert store.compare_and_set_ref("head", ID_B, ID_C) is False
        assert store.compare_and_set_ref("head", ID_A, ID_B) is True
        assert store.get_ref("head") == ID_B

    def test_compare_and_set_serializes_competing_writers(self, tmp_path):
        first = ShardStore(tmp_path / "store")
        second = ShardStore(tmp_path / "store")
        first.set_ref("head", ID_A)
        barrier = threading.Barrier(2)
        results = []

        def update(store, new_id):
            barrier.wait()
            results.append(store.compare_and_set_ref("head", ID_A, new_id))

        threads = [
            threading.Thread(target=update, args=(first, ID_B)),
            threading.Thread(target=update, args=(second, ID_C)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        assert sorted(results) == [False, True]
        assert first.get_ref("head") in {ID_B, ID_C}

    def test_ref_targets_must_be_canonical_ids(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        with pytest.raises(ValueError):
            store.set_ref("head", "short")
        with pytest.raises(ValueError):
            store.compare_and_set_ref("head", "short", ID_A)


class TestWrapperConsistency:
    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("shard_id", ID_A),
            ("scope", "project:tampered"),
            ("atom_count", 99),
            ("created_at", "2099-01-01T00:00:00Z"),
            ("origin_agent", "agent:tampered"),
        ],
    )
    def test_encrypted_wrapper_must_match_inner_shard(self, field, value):
        key = generate_job_key()
        encrypted = encrypt_shard(_shard(), key)
        setattr(encrypted, field, value)

        with pytest.raises(DecryptionError, match=field):
            decrypt_shard(encrypted, key)

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("shard_id", ID_A),
            ("scope", "project:tampered"),
            ("atom_count", 99),
            ("created_at", "2099-01-01T00:00:00Z"),
            ("origin_agent", "agent:tampered"),
            ("owner_pubkey", b"x" * 32),
        ],
    )
    def test_sealed_wrapper_must_match_inner_shard(self, field, value):
        pytest.importorskip("nacl")
        from spiritwriter.fabric.sealed import (
            UnsealError,
            generate_owner_keypair,
            seal_shard,
            unseal_shard,
        )

        keypair = generate_owner_keypair()
        sealed = seal_shard(_shard(), keypair.public_key)
        setattr(sealed, field, value)

        with pytest.raises(UnsealError, match=field):
            unseal_shard(sealed, keypair.private_key)

    def test_denied_wrapper_scope_is_rejected_before_decryption(
        self, tmp_path, monkeypatch
    ):
        store = ShardStore(tmp_path / "store")
        key = generate_job_key()
        shard = _shard(scope="project:denied")
        encrypted = store.encrypt_and_store(shard, key)
        token = create_entitlement(
            granted_to="reader",
            granted_by="test",
            shard_keys={shard.shard_id: key},
            scopes=["project:allowed"],
            capabilities=[Capability.SHARD_READ],
            secrets=[],
            budget_usd=1.0,
        )
        decrypt_called = False

        def should_not_decrypt(wrapper, job_key):
            nonlocal decrypt_called
            decrypt_called = True
            raise AssertionError("denied wrapper was decrypted")

        monkeypatch.setattr(store_mod, "decrypt_shard", should_not_decrypt)
        monkeypatch.setattr(store, "get_encrypted", lambda shard_id: encrypted)

        with pytest.raises(PermissionError, match="project:denied"):
            store.hydrate_with_entitlement(token)
        assert decrypt_called is False

    def test_scope_tampering_cannot_bypass_entitlement(self, tmp_path):
        store = ShardStore(tmp_path / "store")
        key = generate_job_key()
        shard = _shard(scope="project:denied")
        encrypted = store.encrypt_and_store(shard, key)
        path = store._encrypted_path(shard.shard_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["scope"] = "project:allowed"
        path.write_text(json.dumps(payload), encoding="utf-8")
        token = create_entitlement(
            granted_to="reader",
            granted_by="test",
            shard_keys={shard.shard_id: key},
            scopes=["project:allowed"],
            capabilities=[Capability.SHARD_READ],
            secrets=[],
            budget_usd=1.0,
        )

        with pytest.raises(DecryptionError, match="scope"):
            store.hydrate_with_entitlement(token)

    def test_entitlement_authorizes_decrypted_scope(self, tmp_path, monkeypatch):
        store = ShardStore(tmp_path / "store")
        key = generate_job_key()
        inner = _shard(scope="project:denied")
        encrypted = encrypt_shard(inner, key)
        encrypted.scope = "project:allowed"
        token = create_entitlement(
            granted_to="reader",
            granted_by="test",
            shard_keys={inner.shard_id: key},
            scopes=["project:allowed"],
            capabilities=[Capability.SHARD_READ],
            secrets=[],
            budget_usd=1.0,
        )
        monkeypatch.setattr(store, "get_encrypted", lambda shard_id: encrypted)
        monkeypatch.setattr(store_mod, "decrypt_shard", lambda wrapper, job_key: inner)

        with pytest.raises(PermissionError, match="project:denied"):
            store.hydrate_with_entitlement(token)
