"""Shared fixtures for spiritwriter benchmarks."""

import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from spiritwriter.fabric.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass,
)
from spiritwriter.fabric.store import ShardStore


@dataclass
class BenchmarkResult:
    """Collects timing data for a benchmark run."""
    name: str
    operations: int = 0
    total_seconds: float = 0.0
    timings: list = field(default_factory=list)

    @property
    def ops_per_sec(self) -> float:
        if self.total_seconds == 0:
            return 0
        return self.operations / self.total_seconds

    @property
    def avg_ms(self) -> float:
        if not self.timings:
            return 0
        return (sum(self.timings) / len(self.timings)) * 1000

    @property
    def p50_ms(self) -> float:
        if not self.timings:
            return 0
        s = sorted(self.timings)
        return s[len(s) // 2] * 1000

    @property
    def p95_ms(self) -> float:
        if not self.timings:
            return 0
        s = sorted(self.timings)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)] * 1000

    @property
    def p99_ms(self) -> float:
        if not self.timings:
            return 0
        s = sorted(self.timings)
        idx = int(len(s) * 0.99)
        return s[min(idx, len(s) - 1)] * 1000

    def report(self) -> str:
        lines = [
            f"  {self.name}:",
            f"    operations:  {self.operations:,}",
            f"    total:       {self.total_seconds:.3f}s",
            f"    throughput:  {self.ops_per_sec:,.0f} ops/sec",
            f"    avg latency: {self.avg_ms:.3f}ms",
            f"    p50 latency: {self.p50_ms:.3f}ms",
            f"    p95 latency: {self.p95_ms:.3f}ms",
            f"    p99 latency: {self.p99_ms:.3f}ms",
        ]
        return "\n".join(lines).encode("ascii", "replace").decode("ascii")


@contextmanager
def timed_op(result: BenchmarkResult):
    """Context manager that records one operation's timing."""
    start = time.perf_counter()
    yield
    elapsed = time.perf_counter() - start
    result.timings.append(elapsed)
    result.operations += 1
    result.total_seconds += elapsed


def make_shard(
    num_atoms: int = 5,
    scope: str = "bench:test",
    origin: str = "bench",
    decay_class: DecayClass = DecayClass.STABLE,
    unique_id: int = 0,
) -> MemoryShard:
    """Create a shard with the specified number of atoms."""
    atoms = []
    for i in range(num_atoms):
        atoms.append(ShardAtom(
            text=f"Benchmark atom {unique_id}-{i}: this is test content for "
                 f"measuring shard creation and storage performance",
            kind=AtomKind.FACT,
            entity=f"entity-{unique_id}",
            key=f"field-{i}",
            value=f"value-{unique_id}-{i}",
            confidence=0.95,
        ))
    return MemoryShard(
        atoms=atoms,
        scope=scope,
        origin=origin,
        decay_class=decay_class,
        tags=[f"bench:{unique_id}", "benchmark"],
        meta={"bench_id": unique_id},
    )


@pytest.fixture
def tmp_store(tmp_path):
    """Temporary ShardStore for benchmark tests."""
    store = ShardStore(tmp_path / "shards")
    yield store


@pytest.fixture
def populated_store(tmp_path):
    """ShardStore pre-populated with 1000 shards across 10 scopes."""
    store = ShardStore(tmp_path / "shards")
    for i in range(1000):
        scope = f"bench:scope-{i % 10}"
        shard = make_shard(num_atoms=5, scope=scope, unique_id=i)
        store.put(shard)
    return store
