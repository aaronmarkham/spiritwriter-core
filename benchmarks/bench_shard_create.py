"""Benchmark: shard creation and content addressing.

Measures:
- MemoryShard construction time
- shard_id computation (SHA-256 content addressing)
- Serialization (to_json / from_json) roundtrip
- Hydration rendering
- Token estimation
"""

import pytest
from conftest import BenchmarkResult, timed_op, make_shard
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass


class TestShardCreation:
    """Benchmark shard construction and property computation."""

    def test_create_small_shards(self):
        """Create shards with 1-5 atoms."""
        result = BenchmarkResult("create_small_shards (1-5 atoms)")
        for i in range(5000):
            with timed_op(result):
                make_shard(num_atoms=3, unique_id=i)
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 1000, "Should create >1k small shards/sec"

    def test_create_medium_shards(self):
        """Create shards with 20 atoms."""
        result = BenchmarkResult("create_medium_shards (20 atoms)")
        for i in range(2000):
            with timed_op(result):
                make_shard(num_atoms=20, unique_id=i)
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 500, "Should create >500 medium shards/sec"

    def test_create_large_shards(self):
        """Create shards with 100 atoms."""
        result = BenchmarkResult("create_large_shards (100 atoms)")
        for i in range(500):
            with timed_op(result):
                make_shard(num_atoms=100, unique_id=i)
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 100, "Should create >100 large shards/sec"

    def test_shard_id_computation(self):
        """Benchmark content address (SHA-256) computation."""
        shards = [make_shard(num_atoms=10, unique_id=i) for i in range(1000)]
        result = BenchmarkResult("shard_id computation")
        for shard in shards:
            with timed_op(result):
                _ = shard.shard_id
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 5000, "Should compute >5k hashes/sec"

    def test_shard_id_determinism(self):
        """Verify identical content produces identical IDs."""
        atoms = [
            ShardAtom(text="Test", kind=AtomKind.FACT,
                      entity="e", key="k", value="v"),
        ]
        ids = set()
        for _ in range(100):
            shard = MemoryShard(atoms=atoms, scope="test", origin="bench")
            ids.add(shard.shard_id)
        assert len(ids) == 1, "Same content must produce same shard_id"


class TestSerialization:
    """Benchmark JSON serialization roundtrips."""

    def test_serialize_small(self):
        """Serialize/deserialize small shards (5 atoms)."""
        shards = [make_shard(num_atoms=5, unique_id=i) for i in range(2000)]
        result_ser = BenchmarkResult("serialize_small (to_json)")
        result_de = BenchmarkResult("deserialize_small (from_json)")

        json_strs = []
        for shard in shards:
            with timed_op(result_ser):
                j = shard.to_json()
            json_strs.append(j)

        for j in json_strs:
            with timed_op(result_de):
                MemoryShard.from_json(j)

        print(f"\n{result_ser.report()}")
        print(f"\n{result_de.report()}")
        assert result_ser.ops_per_sec > 1000
        assert result_de.ops_per_sec > 1000

    def test_serialize_large(self):
        """Serialize/deserialize large shards (100 atoms)."""
        shards = [make_shard(num_atoms=100, unique_id=i) for i in range(200)]
        result_ser = BenchmarkResult("serialize_large (to_json)")
        result_de = BenchmarkResult("deserialize_large (from_json)")

        json_strs = []
        for shard in shards:
            with timed_op(result_ser):
                j = shard.to_json()
            json_strs.append(j)

        for j in json_strs:
            with timed_op(result_de):
                MemoryShard.from_json(j)

        print(f"\n{result_ser.report()}")
        print(f"\n{result_de.report()}")

    def test_content_hash_verification_on_load(self):
        """Measure overhead of content hash verification during from_json."""
        shard = make_shard(num_atoms=50, unique_id=999)
        j = shard.to_json()
        result = BenchmarkResult("content_hash_verification")
        for _ in range(2000):
            with timed_op(result):
                MemoryShard.from_json(j)
        print(f"\n{result.report()}")


class TestHydration:
    """Benchmark context hydration rendering."""

    def test_hydrate_small(self):
        """Hydrate small shards (5 atoms)."""
        shards = [make_shard(num_atoms=5, unique_id=i) for i in range(2000)]
        result = BenchmarkResult("hydrate_small (5 atoms)")
        for shard in shards:
            with timed_op(result):
                shard.hydrate_context()
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 5000

    def test_hydrate_large(self):
        """Hydrate large shards (100 atoms)."""
        shards = [make_shard(num_atoms=100, unique_id=i) for i in range(500)]
        result = BenchmarkResult("hydrate_large (100 atoms)")
        for shard in shards:
            with timed_op(result):
                shard.hydrate_context()
        print(f"\n{result.report()}")

    def test_token_estimation(self):
        """Benchmark token cost estimation."""
        shards = [make_shard(num_atoms=50, unique_id=i) for i in range(2000)]
        result = BenchmarkResult("token_estimation")
        for shard in shards:
            with timed_op(result):
                _ = shard.token_estimate
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 10000
