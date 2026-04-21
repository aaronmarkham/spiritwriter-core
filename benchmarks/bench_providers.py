"""Benchmark: pluggable memory provider harness.

Tests any memory provider that implements spiritwriter's provider protocol.
Measures retrieval quality (Recall@K, NDCG) and overhead latency from
spiritwriter's trust layer (content addressing, encryption, entity resolution).

Run with:
    python -m pytest benchmarks/bench_providers.py -v -s

Any installed provider is auto-discovered and benchmarked.
If no providers are installed, tests are skipped gracefully.
"""

import math
import time
import random
import pytest
from dataclasses import dataclass, field

from conftest import BenchmarkResult, timed_op

from spiritwriter.integrations import available_providers, get_provider
from spiritwriter.integrations.base import (
    RetrievalProvider,
    StorageProvider,
    SearchQuery,
    SearchResult,
    Document,
    ProviderCapability,
)
from spiritwriter.integrations.mempalace.backend import ShardBackend
from spiritwriter.integrations.mempalace.entities import EntityBridge
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.crypto import generate_job_key
from spiritwriter.fabric.canonicalize import normalize_name


# ── Metrics (same as MemPalace's longmemeval_bench.py) ───────────────

def dcg(relevances: list[float], k: int) -> float:
    """Discounted Cumulative Gain."""
    score = 0.0
    for i, rel in enumerate(relevances[:k]):
        score += rel / math.log2(i + 2)
    return score


def ndcg_at_k(retrieved_ids: list[str], correct_ids: set[str], k: int) -> float:
    """Normalized DCG at rank k."""
    relevances = [1.0 if rid in correct_ids else 0.0 for rid in retrieved_ids[:k]]
    ideal = sorted(relevances, reverse=True)
    idcg = dcg(ideal, k)
    if idcg == 0:
        return 0.0
    return dcg(relevances, k) / idcg


def recall_at_k(retrieved_ids: list[str], correct_ids: set[str], k: int) -> float:
    """Recall@K: is any correct ID in the top K?"""
    top_k = set(retrieved_ids[:k])
    return float(any(cid in top_k for cid in correct_ids))


# ── Test data ────────────────────────────────────────────────────────

SAMPLE_CONVERSATIONS = [
    {
        "id": "session_01",
        "text": "My son Max had his first swim meet today at the community pool. "
                "He was really nervous but ended up winning his heat in backstroke. "
                "His coach said he has real potential.",
        "wing": "family",
        "room": "swimming",
        "entities": ["Max"],
        "topics": ["swimming", "sports", "achievement"],
    },
    {
        "id": "session_02",
        "text": "Riley's school called today. She got into the advanced math program. "
                "We're so proud of her. She wants to be an engineer someday.",
        "wing": "family",
        "room": "school",
        "entities": ["Riley"],
        "topics": ["school", "math", "engineering"],
    },
    {
        "id": "session_03",
        "text": "Started migrating the database from MySQL to PostgreSQL today. "
                "The main challenge is the JSON column types. We need ACID guarantees "
                "for the session storage layer.",
        "wing": "work",
        "room": "database_migration",
        "entities": [],
        "topics": ["database", "postgresql", "migration", "sessions"],
    },
    {
        "id": "session_04",
        "text": "Max's chess tournament is this Saturday at the library. He's been "
                "practicing against the computer every evening. His rating went up "
                "to 1200.",
        "wing": "family",
        "room": "chess",
        "entities": ["Max"],
        "topics": ["chess", "tournament", "rating"],
    },
    {
        "id": "session_05",
        "text": "Deployed the new API gateway with rate limiting. Set it to 100 "
                "requests per minute per client. Using FastAPI with Redis for the "
                "token bucket.",
        "wing": "work",
        "room": "api_gateway",
        "entities": [],
        "topics": ["api", "rate limiting", "fastapi", "redis"],
    },
    {
        "id": "session_06",
        "text": "Riley asked me to help with her science fair project about renewable "
                "energy. She wants to build a small solar panel demonstration. We "
                "ordered supplies from Amazon.",
        "wing": "family",
        "room": "school",
        "entities": ["Riley"],
        "topics": ["science fair", "solar", "renewable energy"],
    },
    {
        "id": "session_07",
        "text": "The PostgreSQL migration is done. Ran into an issue with the JSON "
                "columns but fixed it by using JSONB instead. Performance improved "
                "by 3x on the session queries.",
        "wing": "work",
        "room": "database_migration",
        "entities": [],
        "topics": ["postgresql", "migration", "jsonb", "performance"],
    },
    {
        "id": "session_08",
        "text": "Took the kids to the aquarium today. Max was fascinated by the "
                "jellyfish exhibit. Riley wanted to stay at the touch pool forever. "
                "We got annual passes.",
        "wing": "family",
        "room": "outings",
        "entities": ["Max", "Riley"],
        "topics": ["aquarium", "family outing", "jellyfish"],
    },
]

SAMPLE_QUERIES = [
    {
        "query": "What sport does Max do?",
        "correct": {"session_01"},
        "type": "single_session_fact",
    },
    {
        "query": "What database are we migrating to?",
        "correct": {"session_03", "session_07"},
        "type": "multi_session",
    },
    {
        "query": "What is Riley's science project about?",
        "correct": {"session_06"},
        "type": "single_session_fact",
    },
    {
        "query": "What happened at Max's chess tournament?",
        "correct": {"session_04"},
        "type": "single_session_fact",
    },
    {
        "query": "What rate limit did we set on the API?",
        "correct": {"session_05"},
        "type": "technical_fact",
    },
    {
        "query": "Where did we take the kids recently?",
        "correct": {"session_08"},
        "type": "temporal",
    },
    {
        "query": "How did the PostgreSQL migration go?",
        "correct": {"session_03", "session_07"},
        "type": "multi_session",
    },
    {
        "query": "What is Max interested in besides swimming?",
        "correct": {"session_04"},
        "type": "cross_reference",
    },
]


# ── ShardBackend overhead benchmarks ─────────────────────────────────

class TestShardBackendOverhead:
    """Measure the overhead spiritwriter adds on top of raw storage."""

    def test_add_overhead(self, tmp_path):
        """Measure per-drawer overhead of shard content addressing."""
        # Baseline: raw file write
        result_raw = BenchmarkResult("raw file write")
        for i, conv in enumerate(SAMPLE_CONVERSATIONS * 50):
            with timed_op(result_raw):
                path = tmp_path / "raw" / f"drawer_{i}.txt"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(conv["text"])

        # Shard-backed write
        backend = ShardBackend(tmp_path / "shards")
        result_shard = BenchmarkResult("shard-backed add")
        for i, conv in enumerate(SAMPLE_CONVERSATIONS * 50):
            with timed_op(result_shard):
                backend.add(
                    documents=[conv["text"]],
                    ids=[f"drawer_{i}"],
                    metadatas=[{"wing": conv["wing"], "room": conv["room"]}],
                )

        print(f"\n{result_raw.report()}")
        print(f"\n{result_shard.report()}")
        overhead_ms = result_shard.avg_ms - result_raw.avg_ms
        print(f"\n  Shard overhead per drawer: {overhead_ms:.3f}ms")

    def test_add_encrypted_overhead(self, tmp_path):
        """Measure encryption overhead per drawer."""
        key = generate_job_key()

        # Unencrypted
        backend_plain = ShardBackend(tmp_path / "plain")
        result_plain = BenchmarkResult("unencrypted add")
        for i, conv in enumerate(SAMPLE_CONVERSATIONS * 25):
            with timed_op(result_plain):
                backend_plain.add(
                    documents=[conv["text"]],
                    ids=[f"drawer_{i}"],
                    metadatas=[{"wing": conv["wing"]}],
                )

        # Encrypted
        backend_enc = ShardBackend(tmp_path / "encrypted", encryption_key=key)
        result_enc = BenchmarkResult("encrypted add")
        for i, conv in enumerate(SAMPLE_CONVERSATIONS * 25):
            with timed_op(result_enc):
                backend_enc.add(
                    documents=[conv["text"]],
                    ids=[f"enc_drawer_{i}"],
                    metadatas=[{"wing": conv["wing"]}],
                )

        print(f"\n{result_plain.report()}")
        print(f"\n{result_enc.report()}")
        overhead_ms = result_enc.avg_ms - result_plain.avg_ms
        print(f"\n  Encryption overhead per drawer: {overhead_ms:.3f}ms")

    def test_get_overhead(self, tmp_path):
        """Measure retrieval overhead (shard deserialization + hash verify)."""
        backend = ShardBackend(tmp_path / "shards")
        for i, conv in enumerate(SAMPLE_CONVERSATIONS):
            backend.add(
                documents=[conv["text"]],
                ids=[conv["id"]],
                metadatas=[{"wing": conv["wing"]}],
            )

        result = BenchmarkResult("shard-backed get")
        ids = [c["id"] for c in SAMPLE_CONVERSATIONS]
        for _ in range(200):
            drawer_id = random.choice(ids)
            with timed_op(result):
                backend.get(ids=[drawer_id])
        print(f"\n{result.report()}")

    def test_lineage_tracking(self, tmp_path):
        """Benchmark drawer revision history via parent_shard_id."""
        backend = ShardBackend(tmp_path / "shards")

        # Create 10 revisions of the same drawer
        for rev in range(10):
            backend.upsert(
                documents=[f"Revision {rev}: Max's swim time improved to {60 - rev}s"],
                ids=["drawer_max_swim"],
                metadatas=[{"wing": "family", "room": "swimming", "revision": rev}],
            )

        result = BenchmarkResult("lineage traversal (10 revisions)")
        for _ in range(100):
            with timed_op(result):
                history = backend.get_drawer_history("drawer_max_swim")
            assert len(history) == 10
        print(f"\n{result.report()}")


# ── Entity resolution overhead ───────────────────────────────────────

class TestEntityBridgeOverhead:
    """Measure CMC-Lite entity resolution overhead."""

    def test_person_resolution(self, tmp_path):
        """Benchmark cross-conversation person resolution."""
        bridge = EntityBridge(tmp_path / "entities.db")

        # First pass: establish entities
        result_new = BenchmarkResult("entity resolve (new)")
        for i, conv in enumerate(SAMPLE_CONVERSATIONS):
            for entity_name in conv.get("entities", []):
                with timed_op(result_new):
                    bridge.resolve_person(entity_name, context={
                        "wing": conv["wing"],
                        "room": conv["room"],
                    }, source_id=f"{conv['id']}_{entity_name}")

        # Second pass: resolve same entities (should be T1 exact)
        result_known = BenchmarkResult("entity resolve (known, T1)")
        for i, conv in enumerate(SAMPLE_CONVERSATIONS):
            for entity_name in conv.get("entities", []):
                with timed_op(result_known):
                    r = bridge.resolve_person(entity_name, context={
                        "wing": conv["wing"],
                        "room": conv["room"],
                    }, source_id=f"pass2_{conv['id']}_{entity_name}",
                    persist=False)

        print(f"\n{result_new.report()}")
        print(f"\n{result_known.report()}")
        print(f"\n  Entity stats: {bridge.stats()}")
        bridge.close()

    def test_fuzzy_resolution(self, tmp_path):
        """Benchmark fuzzy name matching across conversations."""
        bridge = EntityBridge(tmp_path / "entities.db")

        # Establish "Maxwell"
        bridge.resolve_person("Maxwell", context={
            "wing": "family", "relationship": "son",
        }, source_id="establish")

        # Resolve variations
        variants = ["Max", "MAXWELL", "Maxwel", "max", "Maxwell J"]
        result = BenchmarkResult("fuzzy entity resolution")
        for _ in range(100):
            for name in variants:
                with timed_op(result):
                    bridge.resolve_person(name, context={
                        "wing": "family", "relationship": "son",
                    }, persist=False)

        print(f"\n{result.report()}")
        bridge.close()


# ── Provider discovery ───────────────────────────────────────────────

class TestProviderDiscovery:
    """Test auto-discovery of installed providers."""

    def test_discover_providers(self):
        """List all available providers."""
        providers = available_providers()
        print(f"\n  Discovered providers: {list(providers.keys())}")
        for name, provider in providers.items():
            info = provider.info()
            print(f"    {name}: v{info.version} "
                  f"caps={[c.value for c in info.capabilities]}")

    def test_provider_info(self):
        """Verify provider metadata."""
        providers = available_providers()
        for name, provider in providers.items():
            info = provider.info()
            assert info.name == name
            assert info.version
            assert isinstance(info.capabilities, list)
            print(f"\n  {name}: {info.description[:80]}...")


# ── Retrieval quality (when a provider is available) ─────────────────

class TestRetrievalQuality:
    """Measure retrieval quality metrics (Recall@K, NDCG).

    These tests only run when a provider with SEMANTIC_SEARCH
    capability is installed and has data loaded.
    """

    @pytest.fixture
    def loaded_provider(self, tmp_path):
        """Get a provider loaded with sample data, or skip."""
        providers = available_providers()
        for name, provider in providers.items():
            if (provider.has_capability(ProviderCapability.STORAGE) and
                provider.has_capability(ProviderCapability.SEMANTIC_SEARCH)):
                # Load sample data
                docs = [
                    Document(
                        document_id=conv["id"],
                        text=conv["text"],
                        metadata={"wing": conv["wing"], "room": conv["room"]},
                    )
                    for conv in SAMPLE_CONVERSATIONS
                ]
                try:
                    provider.store(docs)
                    return provider
                except Exception:
                    continue
        pytest.skip("No provider with STORAGE + SEMANTIC_SEARCH available")

    def test_recall_at_5(self, loaded_provider):
        """Measure Recall@5 on sample queries."""
        hits = 0
        total = len(SAMPLE_QUERIES)

        for q in SAMPLE_QUERIES:
            results = loaded_provider.search(SearchQuery(text=q["query"], top_k=5))
            retrieved_ids = [r.document_id for r in results]
            hit = recall_at_k(retrieved_ids, q["correct"], 5)
            hits += hit
            status = "HIT" if hit else "MISS"
            print(f"  [{status}] {q['query'][:50]}... -> {retrieved_ids[:3]}")

        r_at_5 = hits / total
        print(f"\n  Recall@5: {r_at_5:.1%} ({int(hits)}/{total})")

    def test_ndcg_at_5(self, loaded_provider):
        """Measure NDCG@5 on sample queries."""
        scores = []

        for q in SAMPLE_QUERIES:
            results = loaded_provider.search(SearchQuery(text=q["query"], top_k=5))
            retrieved_ids = [r.document_id for r in results]
            score = ndcg_at_k(retrieved_ids, q["correct"], 5)
            scores.append(score)

        avg_ndcg = sum(scores) / len(scores) if scores else 0
        print(f"\n  NDCG@5: {avg_ndcg:.3f}")
