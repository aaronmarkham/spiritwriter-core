"""Benchmark: end-to-end memory storage and recall patterns.

Simulates real-world usage patterns from frio, perseus-news, and
claude-studio-producer to measure memory pipeline performance:

- Store → recall cycle (write shards, retrieve by various methods)
- Scope-based recall (agent context hydration)
- Encrypted store → recall cycle
- Entity resolution → shard linkage
- Lineage chain traversal
- Mixed workload (concurrent read/write patterns)
"""

import hashlib
import json
import random
import pytest
from conftest import BenchmarkResult, timed_op, make_shard
from spiritwriter.trace.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass,
)
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.crypto import generate_job_key, encrypt_shard, decrypt_shard
from spiritwriter.trace.canonicalize import (
    CanonicalRegistry, CanonicalSchema, canonicalize_batch,
)


# ── Frio-Style Patterns ─────────────────────────────────────────────

class TestFrioPatterns:
    """Simulate frio's search shard lifecycle."""

    def _make_search_shard(self, i: int) -> MemoryShard:
        """Create a frio-style search shard."""
        atoms = [
            ShardAtom(text=f"Target: LASTNAME{i}", kind=AtomKind.ENTITY,
                      entity="target", key="last_name",
                      value=f"LASTNAME{i}"),
            ShardAtom(text=f"First: FIRST{i}", kind=AtomKind.ENTITY,
                      entity="target", key="first_names",
                      value=f"FIRST{i},NICK{i}"),
            ShardAtom(text=f"Threshold: 0.85", kind=AtomKind.FACT,
                      entity="search", key="threshold", value="0.85"),
            ShardAtom(text=f"Region: Nevada", kind=AtomKind.ENTITY,
                      entity="search", key="region", value="Nevada"),
        ]
        return MemoryShard(
            atoms=atoms,
            scope="frio:search",
            origin="frio-web",
            decay_class=DecayClass.ACTIVE,
            tags=[f"frio:lastname{i}"],
            meta={"frio_status": "hot", "intake_channel": "web"},
        )

    def test_intake_to_store(self, tmp_path):
        """Simulate web intake → shard creation → store."""
        store = ShardStore(tmp_path / "frio-shards")
        result = BenchmarkResult("frio: intake -> store")
        for i in range(500):
            with timed_op(result):
                shard = self._make_search_shard(i)
                store.put(shard)
        print(f"\n{result.report()}")

    def test_active_shard_recall(self, tmp_path):
        """Simulate daemon loading active shards for a check cycle."""
        store = ShardStore(tmp_path / "frio-shards")
        for i in range(200):
            store.put(self._make_search_shard(i))

        result = BenchmarkResult("frio: load active shards (200)")
        for _ in range(50):
            with timed_op(result):
                active = []
                for shard in store.by_scope("frio:search"):
                    if shard.meta.get("frio_status") in ("hot", "warm", "cold"):
                        active.append(shard)
        print(f"\n{result.report()}")
        assert len(active) == 200

    def test_match_result_write(self, tmp_path):
        """Simulate writing match results (JSONL append)."""
        results_dir = tmp_path / "results"
        results_dir.mkdir()
        result = BenchmarkResult("frio: write match result")
        for i in range(1000):
            match_data = {
                "shard_id": f"abc{i:060d}",
                "match": {"source": "Lyon County", "name": f"LASTNAME{i}"},
                "stored_at": "2026-04-17T00:00:00Z",
            }
            with timed_op(result):
                with open(results_dir / f"results-{i % 100}.jsonl", "a") as f:
                    f.write(json.dumps(match_data) + "\n")
        print(f"\n{result.report()}")

    def test_full_check_cycle(self, tmp_path):
        """Simulate a complete frio check cycle:
        load shards → read params → match → update status."""
        store = ShardStore(tmp_path / "frio-shards")
        for i in range(100):
            store.put(self._make_search_shard(i))

        result = BenchmarkResult("frio: full check cycle (100 shards)")
        with timed_op(result):
            # Load active
            active = store.by_scope("frio:search")
            # Simulate reading params from each
            for shard in active:
                last_name = shard.get_atom("last_name")
                first_names = shard.get_atom("first_names")
                threshold = shard.get_atom("threshold")
                assert last_name is not None
        result.operations = len(active)
        print(f"\n{result.report()}")


# ── Perseus-Style Patterns ──────────────────────────────────────────

class TestPerseusPatterns:
    """Simulate perseus-news dual-scope article shards."""

    def _make_article_shard(self, i: int, scope: str) -> MemoryShard:
        """Create a perseus-style article shard."""
        url = f"https://news.example.com/article-{i}"
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        entity = f"article:{url_hash[:16]}"

        atoms = [
            ShardAtom(text=f"Article {i} title", kind=AtomKind.FACT,
                      entity=entity, key="title",
                      value=f"News Article {i}"),
            ShardAtom(text=f"Article {i} summary about local events",
                      kind=AtomKind.FACT, entity=entity, key="summary",
                      value=f"Summary of article {i}"),
            ShardAtom(text=url, kind=AtomKind.FACT,
                      entity=entity, key="source_url", value=url),
            ShardAtom(text=f"0.{65 + i % 35}", kind=AtomKind.DECISION,
                      entity=entity, key="rights_relevance",
                      value=f"0.{65 + i % 35}"),
        ]

        return MemoryShard(
            atoms=atoms,
            scope=scope,
            origin="perseus-analyzer",
            decay_class=DecayClass.STABLE,
            tags=[f"region:texas"],
            meta={"entity_key": entity},
        )

    def test_dual_scope_write(self, tmp_path):
        """Write articles to both scopes (internal + shared)."""
        store = ShardStore(tmp_path / "perseus-shards")
        result = BenchmarkResult("perseus: dual-scope write")
        for i in range(500):
            with timed_op(result):
                internal = self._make_article_shard(i, f"perseus:article:texas")
                shared = self._make_article_shard(i, "sw:article")
                store.put(internal)
                store.put(shared)
        result.operations = 500  # count article pairs, not individual puts
        print(f"\n{result.report()}")

    def test_dedup_check(self, tmp_path):
        """Check URL hashes for deduplication."""
        store = ShardStore(tmp_path / "perseus-shards")
        for i in range(500):
            shard = self._make_article_shard(i, "sw:article")
            store.put(shard)

        result = BenchmarkResult("perseus: dedup check (500 articles)")
        with timed_op(result):
            known = set()
            for shard in store.by_scope("sw:article"):
                ek = shard.meta.get("entity_key", "")
                if ek.startswith("article:"):
                    known.add(ek.split(":", 1)[1])
        result.operations = 1
        print(f"\n{result.report()}")
        assert len(known) == 500

    def test_lineage_chain(self, tmp_path):
        """Build and traverse a lineage chain (revisions)."""
        store = ShardStore(tmp_path / "lineage-shards")
        chain_length = 20
        parent_id = None

        # Build chain
        for i in range(chain_length):
            shard = MemoryShard(
                atoms=[ShardAtom(
                    text=f"Revision {i} of article analysis",
                    kind=AtomKind.CONTEXT,
                    entity="article:abc123",
                    key="revision",
                    value=str(i),
                )],
                scope="sw:article",
                origin="perseus-analyzer",
                parent_shard_id=parent_id,
                meta={"revision": i},
            )
            store.put(shard)
            parent_id = shard.shard_id

        # Traverse chain backward
        result = BenchmarkResult(f"lineage: traverse {chain_length}-deep chain")
        for _ in range(100):
            with timed_op(result):
                current_id = parent_id
                depth = 0
                while current_id:
                    current = store.get(current_id)
                    if current is None:
                        break
                    current_id = current.parent_shard_id
                    depth += 1
                assert depth == chain_length
        print(f"\n{result.report()}")


# ── Studio-Style Patterns ───────────────────────────────────────────

class TestStudioPatterns:
    """Simulate claude-studio-producer memory hierarchy."""

    def test_hierarchy_write_and_promote(self, tmp_path):
        """Write shards across SESSION → USER → ORG → PLATFORM."""
        store = ShardStore(tmp_path / "studio-shards")
        result = BenchmarkResult("studio: hierarchy write + promote")

        for i in range(100):
            atoms = [ShardAtom(
                text=f"Luma produces better 4s scenes (observation {i})",
                kind=AtomKind.DECISION,
                entity="luma",
                key="optimal_duration",
                value="4s",
            )]

            with timed_op(result):
                # SESSION
                session = MemoryShard(
                    atoms=atoms, scope=f"session:run-{i}",
                    origin="critic", decay_class=DecayClass.SESSION,
                )
                store.put(session)

                # Promote to USER
                user = MemoryShard(
                    atoms=atoms, scope="org:acme:actor:aaron",
                    origin="critic", decay_class=DecayClass.STABLE,
                    parent_shard_id=session.shard_id,
                )
                store.put(user)

        print(f"\n{result.report()}")

    def test_context_assembly(self, tmp_path):
        """Assemble agent context from multiple hierarchy levels."""
        store = ShardStore(tmp_path / "studio-shards")

        # Seed shards at different levels
        scopes = [
            "platform:learnings",
            "org:acme:learnings",
            "org:acme:actor:aaron",
            "session:current",
        ]
        refs = []
        for j, scope in enumerate(scopes):
            for i in range(5):
                shard = make_shard(num_atoms=10, scope=scope,
                                   unique_id=j * 100 + i)
                ref = store.put(shard)
                refs.append(ref)

        result = BenchmarkResult("studio: context assembly (20 refs)")
        for _ in range(200):
            with timed_op(result):
                context = store.hydrate(refs)
            assert len(context) > 0
        print(f"\n{result.report()}")


# ── Encrypted Recall ─────────────────────────────────────────────────

class TestEncryptedRecall:
    """Benchmark encrypted store → recall cycle."""

    def test_encrypted_store_and_recall(self, tmp_path):
        """Write encrypted shards then recall them."""
        store = ShardStore(tmp_path / "enc-shards")
        key = generate_job_key()
        ids = []

        # Write phase
        result_write = BenchmarkResult("encrypted: store (10 atoms)")
        for i in range(500):
            shard = make_shard(num_atoms=10, unique_id=i)
            with timed_op(result_write):
                enc = store.encrypt_and_store(shard, key)
            ids.append(enc.shard_id)

        # Recall phase
        result_read = BenchmarkResult("encrypted: recall (10 atoms)")
        for sid in ids:
            with timed_op(result_read):
                store.decrypt_and_get(sid, key)

        print(f"\n{result_write.report()}")
        print(f"\n{result_read.report()}")


# ── Entity Resolution + Shard Linkage ────────────────────────────────

class TestResolutionWithShards:
    """Benchmark entity resolution linked to shard storage."""

    def test_resolve_and_store(self, tmp_path):
        """Resolve entities from roster records, then store as shards."""
        schema = CanonicalSchema(
            name="inmate",
            ess_fields=["last_name", "first_name", "dob"],
            fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
        )
        registry = CanonicalRegistry(tmp_path / "resolve.db", schema)
        store = ShardStore(tmp_path / "resolve-shards")

        records = []
        for i in range(500):
            records.append({
                "last_name": f"LASTNAME{i:04d}",
                "first_name": f"FIRST{i:04d}",
                "dob": f"1990-{(i % 12) + 1:02d}-{(i % 28) + 1:02d}",
                "source_id": f"booking-{i}",
                "facility": f"Facility-{i % 5}",
            })

        result = BenchmarkResult("resolve + store (500 records)")
        with timed_op(result):
            resolutions = canonicalize_batch(records, registry, "benchmark")
            for record, resolution in resolutions:
                shard = MemoryShard(
                    atoms=[
                        ShardAtom(text=record["last_name"],
                                  kind=AtomKind.ENTITY,
                                  entity=f"inmate:{resolution.canonical_id or 'new'}",
                                  key="last_name",
                                  value=record["last_name"]),
                    ],
                    scope="frio:search",
                    origin="frio-resolver",
                    meta={"canonical_id": resolution.canonical_id},
                )
                store.put(shard)
        result.operations = 500
        print(f"\n{result.report()}")


# ── Mixed Workload ───────────────────────────────────────────────────

class TestMixedWorkload:
    """Simulate realistic mixed read/write patterns."""

    def test_80_read_20_write(self, tmp_path):
        """80% reads, 20% writes — typical daemon cycle."""
        store = ShardStore(tmp_path / "mixed-shards")

        # Seed with 200 shards
        ids = []
        for i in range(200):
            shard = make_shard(num_atoms=5, unique_id=i)
            store.put(shard)
            ids.append(shard.shard_id)

        result = BenchmarkResult("mixed: 80% read / 20% write")
        write_counter = 200
        for i in range(2000):
            if random.random() < 0.8:
                # Read
                sid = random.choice(ids)
                with timed_op(result):
                    store.get(sid)
            else:
                # Write
                shard = make_shard(num_atoms=5, unique_id=write_counter)
                with timed_op(result):
                    store.put(shard)
                ids.append(shard.shard_id)
                write_counter += 1

        print(f"\n{result.report()}")

    def test_scope_query_heavy(self, tmp_path):
        """Heavy scope-based querying (agent context loading)."""
        store = ShardStore(tmp_path / "scope-shards")
        scopes = [f"project:p{i}" for i in range(10)]

        for i in range(500):
            shard = make_shard(
                num_atoms=5,
                scope=random.choice(scopes),
                unique_id=i,
            )
            store.put(shard)

        result = BenchmarkResult("scope query heavy (500 shards, 10 scopes)")
        for _ in range(500):
            scope = random.choice(scopes)
            with timed_op(result):
                store.by_scope(scope)
        print(f"\n{result.report()}")
