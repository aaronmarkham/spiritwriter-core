"""Tests for spiritwriter.trace.sealed — zero-knowledge sealed-box encryption."""

import json
import tempfile
from pathlib import Path

import pytest

from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind
from spiritwriter.trace.sealed import (
    generate_owner_keypair,
    seal_for_owner,
    unseal_as_owner,
    seal_shard,
    unseal_shard,
    generate_signing_keypair,
    sign_data,
    verify_signature,
    OwnerKeypair,
    SealedShard,
    UnsealError,
    HAS_NACL,
)
from spiritwriter.trace.store import ShardStore


pytestmark = pytest.mark.skipif(not HAS_NACL, reason="PyNaCl not installed")


def _make_shard(**kwargs):
    defaults = dict(
        atoms=[
            ShardAtom(text="Carlos Martinez, aliases: Carlitos", kind=AtomKind.FACT),
            ShardAtom(text="Search NV and CA", kind=AtomKind.CONTEXT),
        ],
        scope="search:active",
        origin="intake:signal",
    )
    defaults.update(kwargs)
    return MemoryShard(**defaults)


# === Key Management ===

class TestOwnerKeypair:
    def test_generate_keypair(self):
        kp = generate_owner_keypair()
        assert len(kp.private_key) == 32
        assert len(kp.public_key) == 32
        assert kp.private_key != kp.public_key

    def test_keypairs_are_unique(self):
        kp1 = generate_owner_keypair()
        kp2 = generate_owner_keypair()
        assert kp1.private_key != kp2.private_key
        assert kp1.public_key != kp2.public_key

    def test_roundtrip_from_private_b64(self):
        kp = generate_owner_keypair()
        restored = OwnerKeypair.from_private_b64(kp.private_key_b64)
        assert restored.private_key == kp.private_key
        assert restored.public_key == kp.public_key

    def test_public_only_keypair(self):
        kp = generate_owner_keypair()
        pub_only = OwnerKeypair.from_public_b64(kp.public_key_b64)
        assert pub_only.public_key == kp.public_key
        assert pub_only.private_key == b""  # no private key


# === Seal / Unseal Primitives ===

class TestSealUnseal:
    def test_roundtrip(self):
        kp = generate_owner_keypair()
        plaintext = b"sensitive search data"
        sealed = seal_for_owner(plaintext, kp.public_key)
        assert sealed != plaintext
        assert len(sealed) > len(plaintext)  # sealed box adds overhead
        result = unseal_as_owner(sealed, kp.private_key)
        assert result == plaintext

    def test_wrong_key_fails(self):
        kp1 = generate_owner_keypair()
        kp2 = generate_owner_keypair()
        sealed = seal_for_owner(b"secret", kp1.public_key)
        with pytest.raises(Exception):  # nacl.exceptions.CryptoError
            unseal_as_owner(sealed, kp2.private_key)

    def test_operator_cannot_decrypt(self):
        """The whole point: sealing with pubkey means only private key holder can read."""
        kp = generate_owner_keypair()
        sealed = seal_for_owner(b"who is detained", kp.public_key)
        # Operator has the public key but NOT the private key
        # There's no way to decrypt with just the public key
        with pytest.raises(Exception):
            unseal_as_owner(sealed, kp.public_key)  # wrong key type/size anyway

    def test_different_ciphertext_each_time(self):
        """Sealed boxes use ephemeral keys — same plaintext produces different output."""
        kp = generate_owner_keypair()
        s1 = seal_for_owner(b"same data", kp.public_key)
        s2 = seal_for_owner(b"same data", kp.public_key)
        assert s1 != s2  # non-deterministic


# === SealedShard ===

class TestSealedShard:
    def test_seal_unseal_shard(self):
        kp = generate_owner_keypair()
        shard = _make_shard()
        sealed = seal_shard(shard, kp.public_key)

        assert sealed.shard_id == shard.shard_id
        assert sealed.scope == shard.scope
        assert sealed.atom_count == 2
        assert sealed.owner_pubkey == kp.public_key

        restored = unseal_shard(sealed, kp.private_key)
        assert restored.shard_id == shard.shard_id
        assert len(restored.atoms) == 2
        assert restored.atoms[0].text == "Carlos Martinez, aliases: Carlitos"

    def test_wrong_key_fails(self):
        kp1 = generate_owner_keypair()
        kp2 = generate_owner_keypair()
        shard = _make_shard()
        sealed = seal_shard(shard, kp1.public_key)
        with pytest.raises(UnsealError):
            unseal_shard(sealed, kp2.private_key)

    def test_tampered_payload_fails(self):
        kp = generate_owner_keypair()
        shard = _make_shard()
        sealed = seal_shard(shard, kp.public_key)
        sealed.sealed_payload = sealed.sealed_payload[:-1] + bytes(
            [sealed.sealed_payload[-1] ^ 0xFF]
        )
        with pytest.raises(UnsealError):
            unseal_shard(sealed, kp.private_key)

    def test_tampered_hash_fails(self):
        kp = generate_owner_keypair()
        shard = _make_shard()
        sealed = seal_shard(shard, kp.public_key)
        sealed.content_hash = "0" * 64
        with pytest.raises(UnsealError, match="hash mismatch"):
            unseal_shard(sealed, kp.private_key)

    def test_json_roundtrip(self):
        kp = generate_owner_keypair()
        shard = _make_shard()
        sealed = seal_shard(shard, kp.public_key)

        raw = sealed.to_json()
        restored = SealedShard.from_json(raw)
        assert restored.shard_id == sealed.shard_id
        assert restored.owner_pubkey == sealed.owner_pubkey
        assert restored.content_hash == sealed.content_hash

        # And it still decrypts
        result = unseal_shard(restored, kp.private_key)
        assert result.shard_id == shard.shard_id

    def test_operator_visible_metadata(self):
        """Operator can see shard_id, scope, atom_count — but not content."""
        kp = generate_owner_keypair()
        shard = _make_shard()
        sealed = seal_shard(shard, kp.public_key)

        d = sealed.to_dict()
        assert d["shard_id"] == shard.shard_id
        assert d["scope"] == "search:active"
        assert d["atom_count"] == 2
        assert d["origin_agent"] == "intake:signal"
        # The sealed_payload is base64 — operator can see it but can't decrypt
        assert isinstance(d["sealed_payload"], str)
        assert len(d["sealed_payload"]) > 0


# === ShardStore Integration ===

class TestStoreSealed:
    @pytest.fixture
    def store(self, tmp_path):
        return ShardStore(tmp_path / "shards")

    def test_seal_and_store(self, store):
        kp = generate_owner_keypair()
        shard = _make_shard()
        sealed = store.seal_and_store(shard, kp.public_key)

        assert store.has_sealed(shard.shard_id)
        retrieved = store.get_sealed(shard.shard_id)
        assert retrieved is not None
        assert retrieved.shard_id == shard.shard_id

    def test_unseal_and_get(self, store):
        kp = generate_owner_keypair()
        shard = _make_shard()
        store.seal_and_store(shard, kp.public_key)

        restored = store.unseal_and_get(shard.shard_id, kp.private_key)
        assert restored.shard_id == shard.shard_id
        assert len(restored.atoms) == 2

    def test_unseal_wrong_key(self, store):
        kp1 = generate_owner_keypair()
        kp2 = generate_owner_keypair()
        shard = _make_shard()
        store.seal_and_store(shard, kp1.public_key)

        with pytest.raises(UnsealError):
            store.unseal_and_get(shard.shard_id, kp2.private_key)

    def test_unseal_not_found(self, store):
        kp = generate_owner_keypair()
        with pytest.raises(KeyError, match="not found"):
            store.unseal_and_get("nonexistent", kp.private_key)

    def test_sealed_indexed_by_scope(self, store):
        kp = generate_owner_keypair()
        shard = _make_shard()
        store.seal_and_store(shard, kp.public_key)

        scopes = store.list_scopes()
        assert "search:active" in scopes

    def test_sealed_separate_from_plaintext(self, store):
        """Sealed and plaintext shards use different file paths."""
        kp = generate_owner_keypair()
        shard = _make_shard()

        # Store both plaintext and sealed
        store.put(shard)
        store.seal_and_store(shard, kp.public_key)

        # Both exist independently
        assert store.has(shard.shard_id)
        assert store.has_sealed(shard.shard_id)

    def test_idempotent_put(self, store):
        kp = generate_owner_keypair()
        shard = _make_shard()
        id1 = store.seal_and_store(shard, kp.public_key).shard_id
        id2 = store.seal_and_store(shard, kp.public_key).shard_id
        assert id1 == id2


# === Ed25519 Signing ===

class TestSigningKeypair:
    def test_generate_keypair(self):
        signing_key, verify_key = generate_signing_keypair()
        assert len(signing_key) == 32
        assert len(verify_key) == 32
        assert signing_key != verify_key

    def test_keypairs_are_unique(self):
        sk1, vk1 = generate_signing_keypair()
        sk2, vk2 = generate_signing_keypair()
        assert sk1 != sk2
        assert vk1 != vk2


class TestSignVerify:
    def test_roundtrip(self):
        signing_key, verify_key = generate_signing_keypair()
        data = b"sealed match result payload"
        signature = sign_data(data, signing_key)
        assert len(signature) == 64
        assert verify_signature(data, signature, verify_key) is True

    def test_tampered_data_fails(self):
        signing_key, verify_key = generate_signing_keypair()
        data = b"original data"
        signature = sign_data(data, signing_key)
        with pytest.raises(Exception):  # nacl.exceptions.BadSignatureError
            verify_signature(b"tampered data", signature, verify_key)

    def test_wrong_verify_key_fails(self):
        sk1, vk1 = generate_signing_keypair()
        _, vk2 = generate_signing_keypair()
        data = b"signed by key 1"
        signature = sign_data(data, sk1)
        with pytest.raises(Exception):
            verify_signature(data, signature, vk2)

    def test_tampered_signature_fails(self):
        signing_key, verify_key = generate_signing_keypair()
        data = b"important data"
        signature = sign_data(data, signing_key)
        bad_sig = bytes([b ^ 0xFF for b in signature[:8]]) + signature[8:]
        with pytest.raises(Exception):
            verify_signature(data, bad_sig, verify_key)

    def test_sign_sealed_box_output(self):
        """Sign the output of seal_for_owner — the primary use case."""
        owner_kp = generate_owner_keypair()
        signing_key, verify_key = generate_signing_keypair()

        plaintext = b'{"name": "martinez", "source": "Douglas County"}'
        sealed = seal_for_owner(plaintext, owner_kp.public_key)

        signature = sign_data(sealed, signing_key)
        assert verify_signature(sealed, signature, verify_key) is True

        # Verify first, then unseal — the intended order
        result = unseal_as_owner(sealed, owner_kp.private_key)
        assert result == plaintext


# === Zero-Knowledge Property Verification ===

class TestZeroKnowledge:
    """Verify the core zero-knowledge property: operator sees metadata, not content."""

    def test_operator_view(self):
        """Simulate what the operator can see from a sealed shard on disk."""
        kp = generate_owner_keypair()
        shard = _make_shard(
            atoms=[
                ShardAtom(
                    text="Martinez, Carlos / Carlitos",
                    kind=AtomKind.FACT,
                    entity="target",
                    key="name",
                    value="Carlos Martinez",
                ),
            ],
            meta={"status": "active", "source_count": 47},
        )
        sealed = seal_shard(shard, kp.public_key)

        # What the operator can see from the SealedShard:
        assert sealed.shard_id  # ✅ content hash
        assert sealed.scope == "search:active"  # ✅ scope
        assert sealed.atom_count == 1  # ✅ count
        assert sealed.origin_agent == "intake:signal"  # ✅ origin

        # What the operator CANNOT see:
        raw = sealed.to_json()
        assert "Martinez" not in raw  # name not in serialized output
        assert "Carlos" not in raw
        assert "Carlitos" not in raw

    def test_service_can_seal_results_to_owner(self):
        """Service encrypts results that only the owner can read."""
        owner_kp = generate_owner_keypair()

        # Service has only the public key
        service_pubkey_only = OwnerKeypair.from_public_b64(owner_kp.public_key_b64)

        # Service seals a result shard for the owner
        result_shard = MemoryShard(
            atoms=[
                ShardAtom(
                    text="Match found: MARTINEZ, CARLOS at Douglas County NV",
                    kind=AtomKind.FACT,
                ),
            ],
            scope="search:results",
            origin="engine:frio",
        )
        sealed = seal_shard(result_shard, service_pubkey_only.public_key)

        # Service cannot unseal it (no private key)
        with pytest.raises(UnsealError):
            unseal_shard(sealed, service_pubkey_only.private_key)  # empty bytes

        # Owner CAN unseal it
        restored = unseal_shard(sealed, owner_kp.private_key)
        assert "MARTINEZ, CARLOS" in restored.atoms[0].text
