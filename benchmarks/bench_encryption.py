"""Benchmark: encryption and sealed-box operations.

Measures:
- AES-256-GCM encrypt/decrypt throughput
- NaCl sealed-box seal/unseal throughput
- Key generation
- Ed25519 signing and verification
- Encrypted store operations
"""

import pytest
from conftest import BenchmarkResult, timed_op, make_shard
from spiritwriter.fabric.crypto import (
    generate_job_key, encrypt_shard, decrypt_shard,
    serialize_key, deserialize_key,
)
from spiritwriter.fabric.store import ShardStore


class TestAESEncryption:
    """Benchmark AES-256-GCM operations."""

    def test_key_generation(self):
        """Generate AES-256 keys."""
        result = BenchmarkResult("aes_key_generation")
        for _ in range(5000):
            with timed_op(result):
                generate_job_key()
        print(f"\n{result.report()}")

    def test_encrypt_small(self):
        """Encrypt small shards (5 atoms)."""
        shards = [make_shard(num_atoms=5, unique_id=i) for i in range(2000)]
        key = generate_job_key()
        result = BenchmarkResult("aes_encrypt_small (5 atoms)")
        for shard in shards:
            with timed_op(result):
                encrypt_shard(shard, key)
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 500

    def test_encrypt_large(self):
        """Encrypt large shards (100 atoms)."""
        shards = [make_shard(num_atoms=100, unique_id=i) for i in range(500)]
        key = generate_job_key()
        result = BenchmarkResult("aes_encrypt_large (100 atoms)")
        for shard in shards:
            with timed_op(result):
                encrypt_shard(shard, key)
        print(f"\n{result.report()}")

    def test_decrypt_small(self):
        """Decrypt small shards."""
        key = generate_job_key()
        encrypted = [
            encrypt_shard(make_shard(num_atoms=5, unique_id=i), key)
            for i in range(2000)
        ]
        result = BenchmarkResult("aes_decrypt_small (5 atoms)")
        for enc in encrypted:
            with timed_op(result):
                decrypt_shard(enc, key)
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 500

    def test_decrypt_large(self):
        """Decrypt large shards."""
        key = generate_job_key()
        encrypted = [
            encrypt_shard(make_shard(num_atoms=100, unique_id=i), key)
            for i in range(500)
        ]
        result = BenchmarkResult("aes_decrypt_large (100 atoms)")
        for enc in encrypted:
            with timed_op(result):
                decrypt_shard(enc, key)
        print(f"\n{result.report()}")

    def test_encrypt_decrypt_roundtrip(self):
        """Full encrypt → decrypt roundtrip."""
        key = generate_job_key()
        shards = [make_shard(num_atoms=20, unique_id=i) for i in range(1000)]
        result = BenchmarkResult("aes_roundtrip (20 atoms)")
        for shard in shards:
            with timed_op(result):
                encrypted = encrypt_shard(shard, key)
                decrypted = decrypt_shard(encrypted, key)
            assert decrypted.shard_id == shard.shard_id
        print(f"\n{result.report()}")

    def test_key_serialization(self):
        """Key serialize/deserialize roundtrip."""
        keys = [generate_job_key() for _ in range(5000)]
        result_ser = BenchmarkResult("key_serialize")
        result_de = BenchmarkResult("key_deserialize")
        key_strs = []
        for key in keys:
            with timed_op(result_ser):
                key_strs.append(serialize_key(key))
        for ks in key_strs:
            with timed_op(result_de):
                deserialize_key(ks)
        print(f"\n{result_ser.report()}")
        print(f"\n{result_de.report()}")


class TestSealedBox:
    """Benchmark NaCl sealed-box operations."""

    @pytest.fixture(autouse=True)
    def _check_nacl(self):
        try:
            from spiritwriter.fabric.sealed import HAS_NACL
            if not HAS_NACL:
                pytest.skip("PyNaCl not installed")
        except ImportError:
            pytest.skip("PyNaCl not installed")

    def test_keypair_generation(self):
        """Generate owner keypairs."""
        from spiritwriter.fabric.sealed import generate_owner_keypair
        result = BenchmarkResult("nacl_keypair_generation")
        for _ in range(2000):
            with timed_op(result):
                generate_owner_keypair()
        print(f"\n{result.report()}")

    def test_seal_small(self):
        """Seal small shards."""
        from spiritwriter.fabric.sealed import generate_owner_keypair, seal_shard
        keypair = generate_owner_keypair()
        shards = [make_shard(num_atoms=5, unique_id=i) for i in range(1000)]
        result = BenchmarkResult("nacl_seal_small (5 atoms)")
        for shard in shards:
            with timed_op(result):
                seal_shard(shard, keypair.public_key)
        print(f"\n{result.report()}")

    def test_seal_large(self):
        """Seal large shards."""
        from spiritwriter.fabric.sealed import generate_owner_keypair, seal_shard
        keypair = generate_owner_keypair()
        shards = [make_shard(num_atoms=100, unique_id=i) for i in range(200)]
        result = BenchmarkResult("nacl_seal_large (100 atoms)")
        for shard in shards:
            with timed_op(result):
                seal_shard(shard, keypair.public_key)
        print(f"\n{result.report()}")

    def test_unseal_small(self):
        """Unseal small shards."""
        from spiritwriter.fabric.sealed import (
            generate_owner_keypair, seal_shard, unseal_shard,
        )
        keypair = generate_owner_keypair()
        sealed = [
            seal_shard(make_shard(num_atoms=5, unique_id=i), keypair.public_key)
            for i in range(1000)
        ]
        result = BenchmarkResult("nacl_unseal_small (5 atoms)")
        for s in sealed:
            with timed_op(result):
                unseal_shard(s, keypair.private_key)
        print(f"\n{result.report()}")

    def test_seal_unseal_roundtrip(self):
        """Full seal → unseal roundtrip."""
        from spiritwriter.fabric.sealed import (
            generate_owner_keypair, seal_shard, unseal_shard,
        )
        keypair = generate_owner_keypair()
        shards = [make_shard(num_atoms=20, unique_id=i) for i in range(500)]
        result = BenchmarkResult("nacl_roundtrip (20 atoms)")
        for shard in shards:
            with timed_op(result):
                sealed = seal_shard(shard, keypair.public_key)
                decrypted = unseal_shard(sealed, keypair.private_key)
            assert decrypted.shard_id == shard.shard_id
        print(f"\n{result.report()}")

    def test_raw_seal_throughput(self):
        """Raw seal_for_owner throughput (no shard overhead)."""
        from spiritwriter.fabric.sealed import (
            generate_owner_keypair, seal_for_owner, unseal_as_owner,
        )
        keypair = generate_owner_keypair()
        payload = b'{"last_name": "Smith", "first_names": ["John", "Johnny"]}'
        result = BenchmarkResult("raw_seal_for_owner")
        for _ in range(5000):
            with timed_op(result):
                seal_for_owner(payload, keypair.public_key)
        print(f"\n{result.report()}")


class TestSigning:
    """Benchmark Ed25519 signing operations."""

    @pytest.fixture(autouse=True)
    def _check_nacl(self):
        try:
            from spiritwriter.fabric.sealed import HAS_NACL
            if not HAS_NACL:
                pytest.skip("PyNaCl not installed")
        except ImportError:
            pytest.skip("PyNaCl not installed")

    def test_signing_keypair(self):
        """Generate signing keypairs."""
        from spiritwriter.fabric.sealed import generate_signing_keypair
        result = BenchmarkResult("ed25519_keypair_generation")
        for _ in range(2000):
            with timed_op(result):
                generate_signing_keypair()
        print(f"\n{result.report()}")

    def test_sign_data(self):
        """Sign data."""
        from spiritwriter.fabric.sealed import generate_signing_keypair, sign_data
        sk, vk = generate_signing_keypair()
        data = b"x" * 1024  # 1KB payload
        result = BenchmarkResult("ed25519_sign (1KB)")
        for _ in range(5000):
            with timed_op(result):
                sign_data(data, sk)
        print(f"\n{result.report()}")

    def test_verify_signature(self):
        """Verify signatures."""
        from spiritwriter.fabric.sealed import (
            generate_signing_keypair, sign_data, verify_signature,
        )
        sk, vk = generate_signing_keypair()
        data = b"x" * 1024
        sig = sign_data(data, sk)
        result = BenchmarkResult("ed25519_verify (1KB)")
        for _ in range(5000):
            with timed_op(result):
                verify_signature(data, sig, vk)
        print(f"\n{result.report()}")


class TestEncryptedStoreOps:
    """Benchmark encrypted store operations."""

    def test_encrypt_and_store(self, tmp_store):
        """Encrypt + store pipeline."""
        key = generate_job_key()
        shards = [make_shard(num_atoms=10, unique_id=i) for i in range(500)]
        result = BenchmarkResult("encrypt_and_store (10 atoms)")
        for shard in shards:
            with timed_op(result):
                tmp_store.encrypt_and_store(shard, key)
        print(f"\n{result.report()}")

    def test_decrypt_and_get(self, tmp_store):
        """Retrieve + decrypt pipeline."""
        key = generate_job_key()
        ids = []
        for i in range(500):
            shard = make_shard(num_atoms=10, unique_id=i)
            enc = tmp_store.encrypt_and_store(shard, key)
            ids.append(enc.shard_id)

        result = BenchmarkResult("decrypt_and_get (10 atoms)")
        for sid in ids:
            with timed_op(result):
                tmp_store.decrypt_and_get(sid, key)
        print(f"\n{result.report()}")
