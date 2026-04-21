"""Tests for the canonicalize module — entity resolution engine."""

import json

import pytest

from spiritwriter.fabric.canonicalize import (
    EntitySenseSig,
    ResolutionTier,
    ResolutionResult,
    CanonicalSchema,
    CanonicalRegistry,
    canonicalize_batch,
    normalize_name,
    normalize_date,
    age_to_bucket,
    fuzzy_score,
)


# ── Schemas for testing ──────────────────────────────────────────────

INMATE_SCHEMA = CanonicalSchema(
    name="inmate",
    ess_fields=["last_name", "first_name", "dob", "gender"],
    fuzzy_fields={"last_name": 0.90, "first_name": 0.85},
    context_fields=["facility", "booking_date"],
    age_bucket_size=2,
    temporal_window_days=7,
)

MEMORY_SCHEMA = CanonicalSchema(
    name="memory_entity",
    ess_fields=["entity_name", "entity_type", "defining_context"],
    fuzzy_fields={"entity_name": 0.85},
    context_fields=["source_scope"],
)


# ── EntitySenseSig Tests ─────────────────────────────────────────────

class TestEntitySenseSig:
    def test_ess_compute_deterministic(self):
        """Same fields → same digest."""
        ess1 = EntitySenseSig.compute(last="Smith", first="John", dob="1984-03-15")
        ess2 = EntitySenseSig.compute(last="Smith", first="John", dob="1984-03-15")
        assert ess1 == ess2
        assert ess1.digest == ess2.digest

    def test_ess_compute_ignores_none(self):
        """None fields are excluded from computation."""
        ess1 = EntitySenseSig.compute(last="Smith", first="John", dob=None)
        ess2 = EntitySenseSig.compute(last="Smith", first="John")
        assert ess1 == ess2
        # Only non-None fields should appear
        assert len(ess1.fields) == 2

    def test_ess_compute_normalized(self):
        """Case and whitespace insensitive."""
        ess1 = EntitySenseSig.compute(last="Smith", first="John")
        ess2 = EntitySenseSig.compute(last="SMITH", first="  john  ")
        assert ess1 == ess2

    def test_ess_different_values(self):
        """Different values → different digest."""
        ess1 = EntitySenseSig.compute(last="Smith", first="John")
        ess2 = EntitySenseSig.compute(last="Smith", first="Jane")
        assert ess1 != ess2

    def test_ess_overlap_full(self):
        """All fields match → 1.0."""
        ess1 = EntitySenseSig.compute(last="Smith", first="John", dob="1984-03-15")
        ess2 = EntitySenseSig.compute(last="Smith", first="John", dob="1984-03-15")
        assert ess1.overlap(ess2) == 1.0

    def test_ess_overlap_partial(self):
        """Some fields match."""
        ess1 = EntitySenseSig.compute(last="Smith", first="John", dob="1984-03-15")
        ess2 = EntitySenseSig.compute(last="Smith", first="Jane", dob="1984-03-15")
        overlap = ess1.overlap(ess2)
        # 2 of 3 shared keys match (last, dob match; first doesn't)
        assert abs(overlap - 2 / 3) < 0.01

    def test_ess_overlap_disjoint(self):
        """No shared keys → 0.0."""
        ess1 = EntitySenseSig.compute(last="Smith")
        ess2 = EntitySenseSig.compute(first="John")
        assert ess1.overlap(ess2) == 0.0

    def test_ess_hashable(self):
        """ESS can be used in sets and as dict keys."""
        ess = EntitySenseSig.compute(last="Smith", first="John")
        s = {ess}
        assert ess in s

    def test_ess_fields_preserved(self):
        """Fields are preserved for inspection."""
        ess = EntitySenseSig.compute(last="Smith", first="John")
        fields_dict = dict(ess.fields)
        assert fields_dict["first"] == "john"
        assert fields_dict["last"] == "smith"


# ── Normalization Tests ──────────────────────────────────────────────

class TestNormalization:
    def test_normalize_name_basic(self):
        assert normalize_name("  John Smith  ") == "JOHN SMITH"

    def test_normalize_name_punctuation(self):
        """Removes punctuation except hyphen."""
        assert normalize_name("O'Brien") == "OBRIEN"
        assert normalize_name("Smith-Jones") == "SMITH-JONES"

    def test_normalize_name_collapse_whitespace(self):
        assert normalize_name("John   Q    Smith") == "JOHN Q SMITH"

    def test_normalize_date_iso(self):
        assert normalize_date("2024-03-15") == "2024-03-15"

    def test_normalize_date_us_format(self):
        assert normalize_date("03/15/2024") == "2024-03-15"

    def test_normalize_date_long_format(self):
        assert normalize_date("March 15, 2024") == "2024-03-15"

    def test_normalize_date_invalid(self):
        assert normalize_date("not a date") is None

    def test_normalize_date_short_year(self):
        result = normalize_date("03/15/24")
        assert result == "2024-03-15"

    def test_age_bucket_even(self):
        assert age_to_bucket(42, 2) == "42-43"

    def test_age_bucket_odd(self):
        assert age_to_bucket(43, 2) == "42-43"

    def test_age_bucket_custom_size(self):
        assert age_to_bucket(42, 5) == "40-44"

    def test_fuzzy_score_exact(self):
        assert fuzzy_score("Smith", "Smith") == 1.0

    def test_fuzzy_score_case_insensitive(self):
        assert fuzzy_score("smith", "SMITH") == 1.0

    def test_fuzzy_score_similar(self):
        score = fuzzy_score("Smith", "Smyth")
        assert 0.6 < score < 1.0

    def test_fuzzy_score_different(self):
        score = fuzzy_score("Smith", "Johnson")
        assert score < 0.5

    def test_fuzzy_score_empty(self):
        assert fuzzy_score("", "Smith") == 0.0

    def test_fuzzy_score_prefix(self):
        """Prefix of 3+ chars gets boosted."""
        score = fuzzy_score("Jon", "Jonathan")
        assert score >= 0.85


# ── Resolution Tier Tests ────────────────────────────────────────────

class TestResolutionTier:
    def test_auto_merge_t1(self):
        assert ResolutionTier.T1_EXACT.auto_merge is True

    def test_auto_merge_t2(self):
        assert ResolutionTier.T2_STRONG.auto_merge is True

    def test_no_auto_merge_t3(self):
        assert ResolutionTier.T3_FUZZY.auto_merge is False

    def test_no_auto_merge_t4(self):
        assert ResolutionTier.T4_WEAK.auto_merge is False

    def test_confidence_ordering(self):
        tiers = [
            ResolutionTier.T1_EXACT,
            ResolutionTier.T2_STRONG,
            ResolutionTier.T3_FUZZY,
            ResolutionTier.T4_WEAK,
            ResolutionTier.NO_MATCH,
        ]
        confidences = [t.confidence for t in tiers]
        assert confidences == sorted(confidences, reverse=True)


# ── Resolution Tests (in-memory via registry) ────────────────────────

class TestResolution:
    @pytest.fixture
    def registry(self, tmp_path):
        return CanonicalRegistry(tmp_path / "test.db", INMATE_SCHEMA)

    def _seed(self, registry, **fields):
        """Insert a record and return its canonical_id."""
        candidate = {**fields}
        resolution = registry.resolve(candidate)
        return registry.upsert(
            candidate, resolution,
            source_name="test", source_id=fields.get("source_id", "s1"),
        )

    def test_resolution_t1_exact(self, registry):
        """Exact ESS → T1."""
        self._seed(registry,
            last_name="Smith", first_name="John",
            dob="1984-03-15", gender="M", source_id="s1",
        )
        result = registry.resolve({
            "last_name": "Smith", "first_name": "John",
            "dob": "1984-03-15", "gender": "M",
        })
        assert result.tier == ResolutionTier.T1_EXACT
        assert result.canonical_id is not None

    def test_resolution_t2_strong(self, registry):
        """Most fields match with fuzzy → T2."""
        self._seed(registry,
            last_name="Smith", first_name="Jonathan",
            dob="1984-03-15", gender="M", source_id="s1",
        )
        result = registry.resolve({
            "last_name": "Smith", "first_name": "Jonathon",  # minor typo
            "dob": "1984-03-15", "gender": "M",
        })
        assert result.tier in (ResolutionTier.T2_STRONG, ResolutionTier.T3_FUZZY)
        assert result.canonical_id is not None

    def test_resolution_t3_fuzzy(self, registry):
        """Fuzzy name + same DOB → T3. Different DOB → NO_MATCH."""
        self._seed(registry,
            last_name="Hernandez", first_name="Maria",
            dob="1990-06-20", gender="F", source_id="s1",
        )
        # Same DOB, misspelled name → fuzzy match
        result = registry.resolve({
            "last_name": "Hernandz",  # misspelling
            "first_name": "Maria",
            "dob": "1990-06-20",  # same DOB
            "gender": "F",
        })
        assert result.tier in (ResolutionTier.T3_FUZZY, ResolutionTier.T2_STRONG)
        assert result.canonical_id is not None

        # Different DOB → non-fuzzy ESS field contradicts → NO_MATCH
        result2 = registry.resolve({
            "last_name": "Hernandz",
            "first_name": "Maria",
            "dob": "1990-07-20",  # different DOB
            "gender": "F",
        })
        assert result2.tier == ResolutionTier.NO_MATCH

    def test_resolution_t4_weak(self, registry):
        """Partial signal only → T4."""
        self._seed(registry,
            last_name="Garcia", first_name="Carlos",
            dob="1975-01-01", gender="M",
            facility="Main Jail", booking_date="2024-01-15",
            source_id="s1",
        )
        result = registry.resolve({
            "last_name": "Garcia", "first_name": "Roberto",  # different first name
            "dob": "1980-01-01", "gender": "M",  # different DOB
            "facility": "Main Jail",  # same facility
        })
        # Different ESS, but same context — could be T4 or NO_MATCH
        assert result.tier in (ResolutionTier.T4_WEAK, ResolutionTier.NO_MATCH,
                               ResolutionTier.T3_FUZZY)

    def test_resolution_no_match(self, registry):
        """Nothing matches → NO_MATCH."""
        self._seed(registry,
            last_name="Smith", first_name="John",
            dob="1984-03-15", gender="M", source_id="s1",
        )
        result = registry.resolve({
            "last_name": "Williams", "first_name": "Sarah",
            "dob": "1995-11-20", "gender": "F",
        })
        assert result.tier == ResolutionTier.NO_MATCH
        assert result.canonical_id is None


# ── Registry Tests ───────────────────────────────────────────────────

class TestCanonicalRegistry:
    @pytest.fixture
    def registry(self, tmp_path):
        return CanonicalRegistry(tmp_path / "test.db", INMATE_SCHEMA)

    def test_registry_upsert_new(self, registry):
        """Creates a new entity when no match exists."""
        candidate = {
            "last_name": "Smith", "first_name": "John",
            "dob": "1984-03-15", "gender": "M",
        }
        resolution = registry.resolve(candidate)
        assert resolution.tier == ResolutionTier.NO_MATCH

        cid = registry.upsert(candidate, resolution, "test", "s1")
        assert cid is not None

        entity = registry.get_entity(cid)
        assert entity is not None
        assert entity["ess_fields"]["last_name"] == "Smith"

    def test_registry_upsert_existing(self, registry):
        """Adds sighting to existing entity on T1 match."""
        candidate = {
            "last_name": "Smith", "first_name": "John",
            "dob": "1984-03-15", "gender": "M",
        }
        # First insert
        r1 = registry.resolve(candidate)
        cid1 = registry.upsert(candidate, r1, "source_a", "a1")

        # Second insert — same entity, different source
        r2 = registry.resolve(candidate)
        assert r2.tier == ResolutionTier.T1_EXACT
        cid2 = registry.upsert(candidate, r2, "source_b", "b1")
        assert cid2 == cid1

        # Should have 2 sightings
        sightings = registry.sightings(cid1)
        assert len(sightings) == 2
        sources = {s["source_name"] for s in sightings}
        assert sources == {"source_a", "source_b"}

    def test_registry_merge(self, registry):
        """Manual merge moves sightings and records provenance."""
        # Create two separate entities
        c1 = {
            "last_name": "Smith", "first_name": "John",
            "dob": "1984-03-15", "gender": "M",
        }
        c2 = {
            "last_name": "Williams", "first_name": "Jane",
            "dob": "1990-05-20", "gender": "F",
        }
        r1 = registry.resolve(c1)
        cid1 = registry.upsert(c1, r1, "test", "s1")
        r2 = registry.resolve(c2)
        cid2 = registry.upsert(c2, r2, "test", "s2")

        # Merge cid2 into cid1
        registry.merge(cid1, cid2, reason="Manual review: same person")

        # cid2 should be gone
        assert registry.get_entity(cid2) is None

        # cid1 should have both sightings
        entity = registry.get_entity(cid1)
        assert len(entity["sightings"]) == 2
        assert cid2 in entity["merged_from"]

    def test_registry_merge_provenance(self, registry):
        """Merge history is recorded in the merges table."""
        c1 = {"last_name": "A", "first_name": "B", "dob": "2000-01-01", "gender": "M"}
        c2 = {"last_name": "C", "first_name": "D", "dob": "2000-01-01", "gender": "F"}
        r1 = registry.resolve(c1)
        cid1 = registry.upsert(c1, r1, "test", "s1")
        r2 = registry.resolve(c2)
        cid2 = registry.upsert(c2, r2, "test", "s2")

        registry.merge(cid1, cid2, reason="Duplicate confirmed")

        stats = registry.stats()
        assert stats["merges"] == 1

    def test_schema_mismatch_raises(self, tmp_path):
        """Wrong schema on open raises ValueError."""
        db_path = tmp_path / "test.db"

        # Create with inmate schema
        CanonicalRegistry(db_path, INMATE_SCHEMA).close()

        # Open with different schema — should fail
        with pytest.raises(ValueError, match="Schema mismatch"):
            CanonicalRegistry(db_path, MEMORY_SCHEMA)

    def test_stats(self, registry):
        """Stats returns counts."""
        c = {"last_name": "X", "first_name": "Y", "dob": "2000-01-01", "gender": "M"}
        r = registry.resolve(c)
        registry.upsert(c, r, "ocv", "s1")

        stats = registry.stats()
        assert stats["entities"] == 1
        assert stats["sightings"] == 1
        assert stats["merges"] == 0
        assert stats["sources"]["ocv"] == 1

    def test_entities_iterator(self, registry):
        """entities() iterates all entities."""
        for i in range(3):
            c = {"last_name": f"Name{i}", "first_name": "X", "dob": "2000-01-01", "gender": "M"}
            r = registry.resolve(c)
            registry.upsert(c, r, "test", f"s{i}")

        all_entities = list(registry.entities())
        assert len(all_entities) == 3

    def test_context_manager(self, tmp_path):
        """Registry works as context manager."""
        with CanonicalRegistry(tmp_path / "test.db", INMATE_SCHEMA) as reg:
            c = {"last_name": "Test", "first_name": "User", "dob": "2000-01-01", "gender": "M"}
            r = reg.resolve(c)
            reg.upsert(c, r, "test", "s1")
            assert reg.stats()["entities"] == 1


# ── Batch Tests ──────────────────────────────────────────────────────

class TestBatch:
    @pytest.fixture
    def registry(self, tmp_path):
        return CanonicalRegistry(tmp_path / "test.db", INMATE_SCHEMA)

    def test_batch_mixed(self, registry):
        """Batch with T1 + new entities."""
        records = [
            {"last_name": "Smith", "first_name": "John",
             "dob": "1984-03-15", "gender": "M", "source_id": "r1"},
            {"last_name": "Williams", "first_name": "Jane",
             "dob": "1990-05-20", "gender": "F", "source_id": "r2"},
            # Duplicate of first
            {"last_name": "Smith", "first_name": "John",
             "dob": "1984-03-15", "gender": "M", "source_id": "r3"},
        ]

        results = canonicalize_batch(records, registry, "batch_test")

        assert len(results) == 3
        # First two should be new entities (NO_MATCH)
        assert results[0][1].tier == ResolutionTier.NO_MATCH
        assert results[1][1].tier == ResolutionTier.NO_MATCH
        # Third should match first (T1)
        assert results[2][1].tier == ResolutionTier.T1_EXACT

        # Should have 2 unique entities
        assert registry.stats()["entities"] == 2
        assert registry.stats()["sightings"] == 3


# ── Bear Problem Test ────────────────────────────────────────────────

class TestFalseMerge:
    """Regression: same name, different non-fuzzy ESS fields must NOT merge."""

    def test_same_name_different_role_no_merge(self, tmp_path):
        """Two people named Aaron with different roles must stay separate."""
        schema = CanonicalSchema(
            name="person",
            ess_fields=["name", "role"],
            fuzzy_fields={"name": 0.85},
            context_fields=["project"],
        )
        registry = CanonicalRegistry(tmp_path / "test.db", schema)

        # Person 1: Aaron the founder
        c1 = {"name": "Aaron", "role": "founder", "project": "spiritwriter"}
        r1 = registry.resolve(c1)
        assert r1.tier == ResolutionTier.NO_MATCH
        cid1 = registry.upsert(c1, r1, "test", "s1")

        # Person 2: Aaron the engineer — MUST NOT merge
        c2 = {"name": "Aaron", "role": "engineer", "project": "mempalace"}
        r2 = registry.resolve(c2)
        assert r2.tier == ResolutionTier.NO_MATCH, (
            f"Expected NO_MATCH for different role, got {r2.tier.value}"
        )
        cid2 = registry.upsert(c2, r2, "test", "s2")
        assert cid1 != cid2

        # Same founder again — MUST match T1
        r3 = registry.resolve(c1)
        assert r3.tier == ResolutionTier.T1_EXACT

        assert registry.stats()["entities"] == 2

    def test_same_name_empty_role_fuzzy_ok(self, tmp_path):
        """Empty role should still fuzzy-match (no contradiction)."""
        schema = CanonicalSchema(
            name="person",
            ess_fields=["name", "role"],
            fuzzy_fields={"name": 0.85},
        )
        registry = CanonicalRegistry(tmp_path / "test.db", schema)

        c1 = {"name": "Aaron", "role": "founder"}
        r1 = registry.resolve(c1)
        registry.upsert(c1, r1, "test", "s1")

        # Empty role — no contradiction, fuzzy match is acceptable
        c2 = {"name": "Aaron", "role": ""}
        r2 = registry.resolve(c2)
        assert r2.tier in (ResolutionTier.T2_STRONG, ResolutionTier.T3_FUZZY), (
            f"Empty role should fuzzy-match, got {r2.tier.value}"
        )

    def test_case_insensitive_no_contradiction(self, tmp_path):
        """'Founder' vs 'FOUNDER' must not contradict."""
        schema = CanonicalSchema(
            name="person",
            ess_fields=["name", "role"],
            fuzzy_fields={"name": 0.85},
        )
        registry = CanonicalRegistry(tmp_path / "test.db", schema)

        c1 = {"name": "Aaron", "role": "Founder"}
        r1 = registry.resolve(c1)
        registry.upsert(c1, r1, "test", "s1")

        c2 = {"name": "Aaron", "role": "FOUNDER"}
        r2 = registry.resolve(c2)
        assert r2.tier == ResolutionTier.T1_EXACT

    def test_whitespace_only_no_contradiction(self, tmp_path):
        """Whitespace-only role must not contradict a real role."""
        schema = CanonicalSchema(
            name="person",
            ess_fields=["name", "role"],
            fuzzy_fields={"name": 0.85},
        )
        registry = CanonicalRegistry(tmp_path / "test.db", schema)

        c1 = {"name": "Aaron", "role": "founder"}
        r1 = registry.resolve(c1)
        registry.upsert(c1, r1, "test", "s1")

        c2 = {"name": "Aaron", "role": "   "}
        r2 = registry.resolve(c2)
        # Whitespace-only = effectively empty, should not contradict
        assert r2.tier != ResolutionTier.NO_MATCH

    def test_same_name_same_role_t1(self, tmp_path):
        """Same name + same role = T1 exact."""
        schema = CanonicalSchema(
            name="person",
            ess_fields=["name", "role"],
            fuzzy_fields={"name": 0.85},
        )
        registry = CanonicalRegistry(tmp_path / "test.db", schema)

        c1 = {"name": "Max", "role": "son"}
        r1 = registry.resolve(c1)
        registry.upsert(c1, r1, "test", "s1")

        c2 = {"name": "Max", "role": "son"}
        r2 = registry.resolve(c2)
        assert r2.tier == ResolutionTier.T1_EXACT


class TestBearProblem:
    def test_bear_problem(self):
        """'Bear' the dog vs 'bear' the animal → different ESS due to entity_type."""
        bear_dog = EntitySenseSig.compute(
            entity_name="Bear",
            entity_type="pet",
            defining_context="family dog, golden retriever",
        )
        bear_animal = EntitySenseSig.compute(
            entity_name="Bear",
            entity_type="animal",
            defining_context="large mammal, hibernates",
        )
        assert bear_dog != bear_animal
        assert bear_dog.digest != bear_animal.digest

    def test_bear_same_type(self):
        """Same name + same type + same context → same ESS."""
        bear1 = EntitySenseSig.compute(
            entity_name="Bear",
            entity_type="pet",
            defining_context="family dog",
        )
        bear2 = EntitySenseSig.compute(
            entity_name="Bear",
            entity_type="pet",
            defining_context="family dog",
        )
        assert bear1 == bear2

    def test_bear_registry(self, tmp_path):
        """Bear problem works end-to-end with memory schema registry."""
        registry = CanonicalRegistry(tmp_path / "test.db", MEMORY_SCHEMA)

        dog = {"entity_name": "Bear", "entity_type": "pet",
               "defining_context": "family dog"}
        animal = {"entity_name": "Bear", "entity_type": "animal",
                  "defining_context": "large mammal"}

        r1 = registry.resolve(dog)
        cid1 = registry.upsert(dog, r1, "memory", "m1")

        r2 = registry.resolve(animal)
        assert r2.tier != ResolutionTier.T1_EXACT  # should NOT be exact match
        cid2 = registry.upsert(animal, r2, "memory", "m2")

        # Different entities
        assert cid1 != cid2
        assert registry.stats()["entities"] == 2
