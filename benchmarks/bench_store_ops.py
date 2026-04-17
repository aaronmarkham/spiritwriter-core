"""Benchmark: ShardStore operations.

Measures:
- put() throughput (write + index update)
- get() latency (content-addressed retrieval)
- by_scope() query performance
- iter_all() scan performance
- Named ref operations
- Hydration pipeline (refs → resolve → render)
- Scale behavior (100, 1K, 10K shards)
"""

import pytest
from conftest import BenchmarkResult, timed_op, make_shard
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.shard import DecayClass


class TestStorePut:
    """Benchmark write operations."""

    def test_put_sequential(self, tmp_store):
        """Sequential put of unique shards."""
        result = BenchmarkResult("put_sequential")
        for i in range(2000):
            shard = make_shard(num_atoms=5, unique_id=i)
            with timed_op(result):
                tmp_store.put(shard)
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 100, "Should store >100 shards/sec"

    def test_put_idempotent(self, tmp_store):
        """Re-putting the same shard (no-op path)."""
        shard = make_shard(num_atoms=5, unique_id=0)
        tmp_store.put(shard)  # first put
        result = BenchmarkResult("put_idempotent (no-op)")
        for _ in range(2000):
            with timed_op(result):
                tmp_store.put(shard)
        print(f"\n{result.report()}")
        # Idempotent puts should be faster (file exists check only)

    def test_put_many_scopes(self, tmp_store):
        """Write to many different scopes."""
        result = BenchmarkResult("put_many_scopes (100 scopes)")
        for i in range(2000):
            shard = make_shard(
                num_atoms=5, scope=f"bench:scope-{i % 100}", unique_id=i
            )
            with timed_op(result):
                tmp_store.put(shard)
        print(f"\n{result.report()}")

    def test_put_large_atoms(self, tmp_store):
        """Write shards with large text atoms."""
        result = BenchmarkResult("put_large_atoms (100 atoms each)")
        for i in range(500):
            shard = make_shard(num_atoms=100, unique_id=i)
            with timed_op(result):
                tmp_store.put(shard)
        print(f"\n{result.report()}")


class TestStoreGet:
    """Benchmark read operations."""

    def test_get_by_id(self, populated_store):
        """Retrieve shards by content address."""
        # Collect shard IDs
        ids = [s.shard_id for s in populated_store.iter_all()]
        result = BenchmarkResult("get_by_id")
        for sid in ids[:2000]:
            with timed_op(result):
                populated_store.get(sid)
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 500, "Should retrieve >500 shards/sec"

    def test_get_miss(self, populated_store):
        """Retrieve non-existent shard (miss path)."""
        result = BenchmarkResult("get_miss")
        for i in range(2000):
            with timed_op(result):
                populated_store.get(f"{'0' * 64}")
        print(f"\n{result.report()}")

    def test_has_check(self, populated_store):
        """Check shard existence without loading."""
        ids = [s.shard_id for s in populated_store.iter_all()]
        result = BenchmarkResult("has_check")
        for sid in ids[:2000]:
            with timed_op(result):
                populated_store.has(sid)
        print(f"\n{result.report()}")
        # has() should be faster than get() (no deserialization)


class TestStoreQuery:
    """Benchmark query operations."""

    def test_by_scope(self, populated_store):
        """Query shards by scope."""
        result = BenchmarkResult("by_scope (100 shards per scope)")
        for i in range(100):
            scope = f"bench:scope-{i % 10}"
            with timed_op(result):
                populated_store.by_scope(scope)
        print(f"\n{result.report()}")

    def test_list_scopes(self, populated_store):
        """List all scopes."""
        result = BenchmarkResult("list_scopes")
        for _ in range(1000):
            with timed_op(result):
                populated_store.list_scopes()
        print(f"\n{result.report()}")

    def test_iter_all(self, populated_store):
        """Full store scan."""
        result = BenchmarkResult("iter_all (1000 shards)")
        for _ in range(10):
            with timed_op(result):
                list(populated_store.iter_all())
        print(f"\n{result.report()}")

    def test_count(self, populated_store):
        """Count total shards."""
        result = BenchmarkResult("count")
        for _ in range(1000):
            with timed_op(result):
                populated_store.count()
        print(f"\n{result.report()}")

    def test_stats(self, populated_store):
        """Compute store statistics."""
        result = BenchmarkResult("stats (1000 shards)")
        for _ in range(5):
            with timed_op(result):
                populated_store.stats()
        print(f"\n{result.report()}")


class TestNamedRefs:
    """Benchmark named ref operations."""

    def test_set_and_get_ref(self, tmp_store):
        """Write and read named refs."""
        shard = make_shard(num_atoms=5, unique_id=0)
        tmp_store.put(shard)

        result_set = BenchmarkResult("set_ref")
        result_get = BenchmarkResult("get_ref")

        for i in range(1000):
            with timed_op(result_set):
                tmp_store.set_ref(f"bench-ref-{i}", shard.shard_id)

        for i in range(1000):
            with timed_op(result_get):
                tmp_store.get_ref(f"bench-ref-{i}")

        print(f"\n{result_set.report()}")
        print(f"\n{result_get.report()}")

    def test_resolve_ref(self, tmp_store):
        """Resolve named ref to full shard."""
        shard = make_shard(num_atoms=5, unique_id=0)
        tmp_store.put(shard)
        tmp_store.set_ref("bench-latest", shard.shard_id)

        result = BenchmarkResult("resolve_ref")
        for _ in range(1000):
            with timed_op(result):
                tmp_store.resolve_ref("bench-latest")
        print(f"\n{result.report()}")

    def test_list_refs(self, tmp_store):
        """List refs with prefix filter."""
        shard = make_shard(num_atoms=5, unique_id=0)
        tmp_store.put(shard)
        for i in range(200):
            tmp_store.set_ref(f"project-{i}", shard.shard_id)

        result = BenchmarkResult("list_refs (200 refs)")
        for _ in range(500):
            with timed_op(result):
                tmp_store.list_refs(prefix="project-")
        print(f"\n{result.report()}")


class TestHydrationPipeline:
    """Benchmark the full hydration pipeline: refs → resolve → render."""

    def test_hydrate_5_refs(self, populated_store):
        """Hydrate 5 shard refs (typical sub-agent context)."""
        shards = list(populated_store.iter_all())[:5]
        refs = [s.ref for s in shards]

        result = BenchmarkResult("hydrate_5_refs")
        for _ in range(500):
            with timed_op(result):
                populated_store.hydrate(refs)
        print(f"\n{result.report()}")

    def test_hydrate_20_refs(self, populated_store):
        """Hydrate 20 shard refs (heavy context)."""
        shards = list(populated_store.iter_all())[:20]
        refs = [s.ref for s in shards]

        result = BenchmarkResult("hydrate_20_refs")
        for _ in range(200):
            with timed_op(result):
                populated_store.hydrate(refs)
        print(f"\n{result.report()}")


class TestScaleBehavior:
    """Measure how operations scale with store size."""

    @pytest.mark.parametrize("num_shards", [100, 1000, 5000])
    def test_put_at_scale(self, tmp_path, num_shards):
        """Measure put() throughput at different store sizes."""
        store = ShardStore(tmp_path / f"scale-{num_shards}")
        result = BenchmarkResult(f"put_at_scale ({num_shards} shards)")
        for i in range(num_shards):
            shard = make_shard(num_atoms=5, unique_id=i)
            with timed_op(result):
                store.put(shard)
        print(f"\n{result.report()}")

    @pytest.mark.parametrize("num_shards", [100, 1000, 5000])
    def test_get_at_scale(self, tmp_path, num_shards):
        """Measure get() latency at different store sizes."""
        store = ShardStore(tmp_path / f"scale-get-{num_shards}")
        ids = []
        for i in range(num_shards):
            shard = make_shard(num_atoms=5, unique_id=i)
            store.put(shard)
            ids.append(shard.shard_id)

        result = BenchmarkResult(f"get_at_scale ({num_shards} shards)")
        # Sample 500 random reads
        import random
        sample = random.choices(ids, k=min(500, num_shards))
        for sid in sample:
            with timed_op(result):
                store.get(sid)
        print(f"\n{result.report()}")
