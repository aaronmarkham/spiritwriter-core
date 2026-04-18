# spiritwriter-core Benchmarks

Performance benchmarks for memory shard storage, recall, encryption, and entity resolution.

## Quick Start

```bash
# Install with dev + sealed extras
pip install -e ".[dev,sealed]"

# Run all benchmarks (with timing output)
python -m pytest benchmarks/ -v -s

# Run a specific benchmark file
python -m pytest benchmarks/bench_shard_create.py -v -s
python -m pytest benchmarks/bench_store_ops.py -v -s
python -m pytest benchmarks/bench_encryption.py -v -s
python -m pytest benchmarks/bench_entity_resolution.py -v -s
python -m pytest benchmarks/bench_memory_recall.py -v -s

# Run only scale tests
python -m pytest benchmarks/bench_store_ops.py -v -s -k "scale"

# Run only frio/perseus/studio patterns
python -m pytest benchmarks/bench_memory_recall.py -v -s -k "Frio or Perseus or Studio"
```

The `-s` flag is important — it shows the benchmark output (ops/sec, latency percentiles).

## What's Measured

### bench_shard_create.py — Shard Construction

- **MemoryShard creation** at 1-5, 20, 100 atoms
- **shard_id computation** (SHA-256 content addressing)
- **Serialization roundtrip** (to_json / from_json)
- **Content hash verification** overhead on deserialization
- **Hydration rendering** (XML-tagged context output)
- **Token estimation** (cost prediction)

### bench_store_ops.py — ShardStore Operations

- **put()** sequential, idempotent, many-scopes, large-atom
- **get()** by ID, miss path, has() check
- **by_scope()** query, list_scopes(), iter_all(), count(), stats()
- **Named refs** — set, get, resolve, list with prefix
- **Hydration pipeline** — 5 and 20 ref resolution
- **Scale behavior** — 100, 1K, 5K shards

### bench_encryption.py — Encryption & Sealed Boxes

- **AES-256-GCM** — key gen, encrypt, decrypt, roundtrip (small/large shards)
- **NaCl sealed boxes** — keypair gen, seal, unseal, raw throughput
- **Ed25519 signing** — keypair gen, sign, verify
- **Encrypted store ops** — encrypt_and_store, decrypt_and_get

### bench_entity_resolution.py — CMC-Lite

- **ESS computation** — entity sense signature creation, overlap, equality
- **Normalization** — name, date, fuzzy score, age bucketing
- **resolve()** at 100, 1K, 5K entities (T1, NO_MATCH, fuzzy paths)
- **upsert()** — new entities, existing entities (T1 add-sighting)
- **Batch canonicalization** — 100 and 1000 records
- **Registry queries** — get_entity, find_fuzzy, stats

### bench_memory_recall.py — End-to-End Patterns

Real-world workload simulations:

- **Frio patterns** — intake→store, active shard recall, match result write, full check cycle
- **Perseus patterns** — dual-scope write, URL dedup check, lineage chain traversal
- **Studio patterns** — hierarchy write+promote, context assembly from 4 levels
- **Encrypted recall** — store→recall roundtrip with AES-256
- **Entity resolution + shard linkage** — resolve→shard in one pipeline
- **Mixed workload** — 80% read / 20% write, scope query heavy

## Output Format

Each benchmark prints:

```
  benchmark_name:
    operations:  5,000
    total:       1.234s
    throughput:  4,050 ops/sec
    avg latency: 0.247ms
    p50 latency: 0.221ms
    p95 latency: 0.412ms
    p99 latency: 0.589ms
```

## Assertions

Some benchmarks include minimum throughput assertions (e.g., `>1000 ops/sec`). These are conservative targets for a modern machine. If your hardware is significantly slower, you can remove the assertions — the timing data is still collected.

## Adding Benchmarks

Use the shared fixtures from `conftest.py`:

```python
from conftest import BenchmarkResult, timed_op, make_shard

def test_my_benchmark(tmp_store):
    result = BenchmarkResult("my_operation")
    for i in range(1000):
        shard = make_shard(num_atoms=10, unique_id=i)
        with timed_op(result):
            tmp_store.put(shard)
    print(f"\n{result.report()}")
```
