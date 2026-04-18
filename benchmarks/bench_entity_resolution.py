"""Benchmark: CMC-Lite entity resolution.

Measures:
- CanonicalRegistry resolve() performance at various registry sizes
- upsert() throughput (T1/T2/T3/NO_MATCH paths)
- Batch canonicalization
- ESS computation
- Normalization utilities
- Fuzzy scoring
"""

import pytest
import random
import string
from conftest import BenchmarkResult, timed_op
from spiritwriter.trace.canonicalize import (
    CanonicalRegistry, CanonicalSchema, ResolutionTier,
    EntitySenseSig, canonicalize_batch,
    normalize_name, normalize_date, age_to_bucket, fuzzy_score,
)


# ── Fixtures ────────────────────────────────────────────────────────

INMATE_SCHEMA = CanonicalSchema(
    name="inmate",
    ess_fields=["last_name", "first_name", "dob"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.80},
    context_fields=["facility", "gender"],
    metadata_fields=["charges", "booking_date"],
)

FIRST_NAMES = [
    "John", "James", "Robert", "Michael", "David", "Carlos", "Jose",
    "Maria", "Ana", "Luis", "Pedro", "Juan", "Francisco", "Antonio",
    "Miguel", "Sarah", "Jennifer", "Jessica", "Ashley", "Emily",
]
LAST_NAMES = [
    "Smith", "Johnson", "Martinez", "Garcia", "Rodriguez", "Davis",
    "Brown", "Wilson", "Taylor", "Thomas", "Hernandez", "Lopez",
    "Anderson", "Moore", "Jackson", "White", "Harris", "Clark",
    "Lewis", "Walker",
]
FACILITIES = [
    "Lyon County", "Clark County CCDC", "NDOC", "Washoe County",
    "Douglas County", "Napa County", "Travis County", "Harris County",
]


def random_candidate(i: int) -> dict:
    """Generate a candidate record with some variation."""
    return {
        "last_name": random.choice(LAST_NAMES),
        "first_name": random.choice(FIRST_NAMES),
        "dob": f"19{random.randint(60, 99)}-{random.randint(1,12):02d}-"
               f"{random.randint(1,28):02d}",
        "facility": random.choice(FACILITIES),
        "gender": random.choice(["M", "F"]),
        "source_id": f"booking-{i:06d}",
    }


def deterministic_candidate(i: int) -> dict:
    """Generate a unique candidate (guaranteed distinct ESS)."""
    return {
        "last_name": f"LASTNAME{i:06d}",
        "first_name": f"FIRST{i:06d}",
        "dob": f"1990-01-{(i % 28) + 1:02d}",
        "facility": FACILITIES[i % len(FACILITIES)],
        "gender": "M" if i % 2 == 0 else "F",
        "source_id": f"det-{i:06d}",
    }


@pytest.fixture
def empty_registry(tmp_path):
    """Empty CanonicalRegistry."""
    return CanonicalRegistry(tmp_path / "empty.db", INMATE_SCHEMA)


@pytest.fixture
def small_registry(tmp_path):
    """Registry with 100 entities."""
    reg = CanonicalRegistry(tmp_path / "small.db", INMATE_SCHEMA)
    for i in range(100):
        c = deterministic_candidate(i)
        result = reg.resolve(c)
        reg.upsert(c, result, "seed", c["source_id"])
    return reg


@pytest.fixture
def medium_registry(tmp_path):
    """Registry with 1000 entities."""
    reg = CanonicalRegistry(tmp_path / "medium.db", INMATE_SCHEMA)
    for i in range(1000):
        c = deterministic_candidate(i)
        result = reg.resolve(c)
        reg.upsert(c, result, "seed", c["source_id"])
    return reg


@pytest.fixture
def large_registry(tmp_path):
    """Registry with 5000 entities."""
    reg = CanonicalRegistry(tmp_path / "large.db", INMATE_SCHEMA)
    for i in range(5000):
        c = deterministic_candidate(i)
        result = reg.resolve(c)
        reg.upsert(c, result, "seed", c["source_id"])
    return reg


# ── ESS Computation ─────────────────────────────────────────────────

class TestESSComputation:
    """Benchmark Entity Sense Signature operations."""

    def test_ess_compute(self):
        """Compute ESS from fields."""
        result = BenchmarkResult("ess_compute")
        for i in range(10000):
            with timed_op(result):
                EntitySenseSig.compute(
                    last_name=f"Smith{i}",
                    first_name="John",
                    dob="1990-05-12",
                )
        print(f"\n{result.report()}")
        assert result.ops_per_sec > 10000

    def test_ess_overlap(self):
        """Compute ESS overlap."""
        ess_a = EntitySenseSig.compute(
            last_name="Smith", first_name="John", dob="1990-05-12"
        )
        ess_b = EntitySenseSig.compute(
            last_name="Smith", first_name="John", dob="1991-06-13"
        )
        result = BenchmarkResult("ess_overlap")
        for _ in range(10000):
            with timed_op(result):
                ess_a.overlap(ess_b)
        print(f"\n{result.report()}")

    def test_ess_equality(self):
        """ESS equality check (T1 match)."""
        ess_a = EntitySenseSig.compute(
            last_name="Smith", first_name="John", dob="1990-05-12"
        )
        ess_b = EntitySenseSig.compute(
            last_name="smith", first_name="john", dob="1990-05-12"
        )
        result = BenchmarkResult("ess_equality")
        for _ in range(50000):
            with timed_op(result):
                _ = ess_a == ess_b
        print(f"\n{result.report()}")


# ── Normalization ────────────────────────────────────────────────────

class TestNormalization:
    """Benchmark normalization utilities."""

    def test_normalize_name(self):
        """Name normalization throughput."""
        names = [
            "  martinez, carlos a.  ", "DE LA CRUZ", "Smith-Jones",
            "O'Brien", "  john   doe  ", "MARÍA GARCÍA",
        ]
        result = BenchmarkResult("normalize_name")
        for _ in range(10000):
            for name in names:
                with timed_op(result):
                    normalize_name(name)
        print(f"\n{result.report()}")

    def test_normalize_date(self):
        """Date normalization across formats."""
        dates = [
            "05/12/1990", "1990-05-12", "May 12, 1990",
            "12/05/1990", "19900512", "5/12/90",
        ]
        result = BenchmarkResult("normalize_date")
        for _ in range(5000):
            for d in dates:
                with timed_op(result):
                    normalize_date(d)
        print(f"\n{result.report()}")

    def test_fuzzy_score(self):
        """Fuzzy scoring between name pairs."""
        pairs = [
            ("Martinez", "MARTINEZ"),   # exact
            ("Carlos", "Carlitos"),      # diminutive
            ("Smith", "Smythe"),         # variant
            ("Johnson", "Johnsen"),      # variant
            ("Garcia", "Garza"),         # different
            ("De La Cruz", "Delacruz"),  # spacing
        ]
        result = BenchmarkResult("fuzzy_score")
        for _ in range(5000):
            for a, b in pairs:
                with timed_op(result):
                    fuzzy_score(a, b)
        print(f"\n{result.report()}")

    def test_age_to_bucket(self):
        """Age bucketing."""
        result = BenchmarkResult("age_to_bucket")
        for _ in range(50000):
            age = random.randint(18, 80)
            with timed_op(result):
                age_to_bucket(age)
        print(f"\n{result.report()}")


# ── Resolution ───────────────────────────────────────────────────────

class TestResolve:
    """Benchmark resolve() at different registry sizes."""

    def test_resolve_t1_exact(self, medium_registry):
        """Resolve known entities (T1 exact match)."""
        result = BenchmarkResult("resolve_T1 (1000 entities)")
        for i in range(500):
            c = deterministic_candidate(i)
            with timed_op(result):
                r = medium_registry.resolve(c)
            assert r.tier == ResolutionTier.T1_EXACT
        print(f"\n{result.report()}")

    def test_resolve_no_match(self, medium_registry):
        """Resolve new entities (NO_MATCH or T4_WEAK — scans all)."""
        result = BenchmarkResult("resolve_NO_MATCH (1000 entities)")
        for i in range(200):
            c = deterministic_candidate(10000 + i)  # outside seeded range
            with timed_op(result):
                r = medium_registry.resolve(c)
            # May be NO_MATCH or T4_WEAK (context fields like facility/gender
            # can overlap with seeded data)
            assert r.tier in (ResolutionTier.NO_MATCH, ResolutionTier.T4_WEAK)
        print(f"\n{result.report()}")

    def test_resolve_fuzzy(self, medium_registry):
        """Resolve with fuzzy matching (typos)."""
        result = BenchmarkResult("resolve_fuzzy (1000 entities)")
        for i in range(200):
            c = deterministic_candidate(i)
            # Introduce typo
            c["first_name"] = c["first_name"][:-2] + "XX"
            with timed_op(result):
                medium_registry.resolve(c)
        print(f"\n{result.report()}")

    def test_resolve_scale_100(self, small_registry):
        """Resolve against 100-entity registry."""
        result = BenchmarkResult("resolve (100 entities)")
        for i in range(500):
            c = random_candidate(i)
            with timed_op(result):
                small_registry.resolve(c)
        print(f"\n{result.report()}")

    def test_resolve_scale_1000(self, medium_registry):
        """Resolve against 1000-entity registry."""
        result = BenchmarkResult("resolve (1000 entities)")
        for i in range(500):
            c = random_candidate(i)
            with timed_op(result):
                medium_registry.resolve(c)
        print(f"\n{result.report()}")

    def test_resolve_scale_5000(self, large_registry):
        """Resolve against 5000-entity registry."""
        result = BenchmarkResult("resolve (5000 entities)")
        for i in range(200):
            c = random_candidate(i)
            with timed_op(result):
                large_registry.resolve(c)
        print(f"\n{result.report()}")


# ── Upsert ───────────────────────────────────────────────────────────

class TestUpsert:
    """Benchmark upsert() operations."""

    def test_upsert_new_entities(self, empty_registry):
        """Upsert brand-new entities (NO_MATCH → create)."""
        result = BenchmarkResult("upsert_new")
        for i in range(1000):
            c = deterministic_candidate(i)
            r = empty_registry.resolve(c)
            with timed_op(result):
                empty_registry.upsert(c, r, "bench", c["source_id"])
        print(f"\n{result.report()}")

    def test_upsert_existing_entities(self, medium_registry):
        """Upsert existing entities (T1 → add sighting)."""
        result = BenchmarkResult("upsert_existing (T1)")
        for i in range(500):
            c = deterministic_candidate(i)
            r = medium_registry.resolve(c)
            assert r.tier == ResolutionTier.T1_EXACT
            with timed_op(result):
                medium_registry.upsert(
                    c, r, f"bench-{i}", f"new-{i}"
                )
        print(f"\n{result.report()}")


# ── Batch ────────────────────────────────────────────────────────────

class TestBatch:
    """Benchmark batch canonicalization."""

    def test_batch_100(self, empty_registry):
        """Canonicalize 100 records."""
        records = [deterministic_candidate(i) for i in range(100)]
        result = BenchmarkResult("canonicalize_batch (100 records)")
        with timed_op(result):
            canonicalize_batch(records, empty_registry, "batch-bench")
        result.operations = 100  # count individual records
        print(f"\n{result.report()}")

    def test_batch_1000(self, tmp_path):
        """Canonicalize 1000 records."""
        reg = CanonicalRegistry(tmp_path / "batch1k.db", INMATE_SCHEMA)
        records = [deterministic_candidate(i) for i in range(1000)]
        result = BenchmarkResult("canonicalize_batch (1000 records)")
        with timed_op(result):
            canonicalize_batch(records, reg, "batch-bench")
        result.operations = 1000
        print(f"\n{result.report()}")


# ── Query ────────────────────────────────────────────────────────────

class TestQuery:
    """Benchmark registry query operations."""

    def test_get_entity(self, medium_registry):
        """Fetch entity with sightings."""
        entities = list(medium_registry.entities())
        result = BenchmarkResult("get_entity (with sightings)")
        for e in entities[:500]:
            with timed_op(result):
                medium_registry.get_entity(e["canonical_id"])
        print(f"\n{result.report()}")

    def test_find_fuzzy(self, medium_registry):
        """Fuzzy search across registry."""
        result = BenchmarkResult("find_fuzzy (1000 entities)")
        for i in range(100):
            with timed_op(result):
                medium_registry.find_fuzzy(
                    {"last_name": f"LASTNAME{i:06d}", "first_name": "FIRST"},
                    limit=5,
                )
        print(f"\n{result.report()}")

    def test_stats(self, medium_registry):
        """Registry statistics."""
        result = BenchmarkResult("stats (1000 entities)")
        for _ in range(100):
            with timed_op(result):
                medium_registry.stats()
        print(f"\n{result.report()}")
