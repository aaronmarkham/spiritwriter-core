"""Tests for conflict variants and the anti-merge (split-on-discriminator).

Two features that only matter once folding exists:

* ``conflicts="variants"`` reifies suppressed values as queryable rows
  instead of burying them in a JSON key.
* ``split_on_conflict`` lets the registry *refuse* a match — the one thing
  a merge-only resolver can never do — when a thin ESS collapses records
  that a discriminator field proves are different.
"""

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest

from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry,
    CanonicalSchema,
    ResolutionPolicy,
    ResolutionReport,
    ResolutionTier,
    canonicalize_batch,
    skolem_digest,
)

SCHEMA = CanonicalSchema(
    name="person",
    ess_fields=["last_name", "first_name"],
    fuzzy_fields={},
    context_fields=["city"],
    metadata_fields=["ssn", "notes"],
)


def _reg(td, **policy_kwargs):
    return CanonicalRegistry(
        Path(td) / "r.db", SCHEMA, policy=ResolutionPolicy(**policy_kwargs)
    )


def _reg_at(path, **policy_kwargs):
    return CanonicalRegistry(path, SCHEMA, policy=ResolutionPolicy(**policy_kwargs))


# ── Conflict variants ────────────────────────────────────────────────


class TestConflictVariants:
    def test_suppressed_value_becomes_a_queryable_row(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, conflicts="variants")
            seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            cid = reg.upsert(seed, reg.resolve(seed), "a", "1")

            clash = {"last_name": "Smith", "first_name": "Ada", "city": "Sparks"}
            reg.upsert(clash, reg.resolve(clash), "b", "2")

            variants = reg.variants(cid)
            assert len(variants) == 1
            assert variants[0]["fields"] == {"city": "Sparks"}
            assert variants[0]["source_name"] == "b"
            assert variants[0]["source_id"] == "2"

    def test_canonical_blob_matches_keep_first(self):
        """variants mode reifies elsewhere; the entity itself is untouched."""
        seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
        clash = {"last_name": "Smith", "first_name": "Ada", "city": "Sparks"}

        blobs = {}
        for mode in ("keep-first", "variants"):
            with tempfile.TemporaryDirectory() as td:
                reg = _reg(td, conflicts=mode)
                cid = reg.upsert(seed, reg.resolve(seed), "a", "1")
                reg.upsert(clash, reg.resolve(clash), "b", "2")
                blobs[mode] = reg.get_entity(cid)["ess_fields"]

        assert blobs["variants"] == blobs["keep-first"]
        assert "__conflicts__" not in blobs["variants"]

    def test_two_sources_dropping_the_same_value_stay_two_rows(self):
        """The content-bearing source stamp — the whole point of the design.

        Without mixing source into variant_id these collapse to one row and
        the fact that *two* sources disagreed is lost.
        """
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, conflicts="variants")
            seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            cid = reg.upsert(seed, reg.resolve(seed), "a", "1")

            for source, sid in (("b", "2"), ("c", "3")):
                clash = {"last_name": "Smith", "first_name": "Ada", "city": "Sparks"}
                reg.upsert(clash, reg.resolve(clash), source, sid)

            variants = reg.variants(cid)
            assert len(variants) == 2
            assert {v["source_name"] for v in variants} == {"b", "c"}
            assert all(v["fields"] == {"city": "Sparks"} for v in variants)

    def test_reingesting_the_same_sighting_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, conflicts="variants")
            seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            cid = reg.upsert(seed, reg.resolve(seed), "a", "1")

            clash = {"last_name": "Smith", "first_name": "Ada", "city": "Sparks"}
            for _ in range(3):
                reg.upsert(clash, reg.resolve(clash), "b", "2")

            assert len(reg.variants(cid)) == 1

    def test_no_conflict_means_no_variant(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, conflicts="variants")
            seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            cid = reg.upsert(seed, reg.resolve(seed), "a", "1")
            same = {"last_name": "Smith", "first_name": "Ada", "city": "reno"}
            reg.upsert(same, reg.resolve(same), "b", "2")
            assert reg.variants(cid) == []

    def test_stats_counts_variants(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, conflicts="variants")
            seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            reg.upsert(seed, reg.resolve(seed), "a", "1")
            clash = {"last_name": "Smith", "first_name": "Ada", "city": "Sparks"}
            reg.upsert(clash, reg.resolve(clash), "b", "2")
            assert reg.stats()["variants"] == 1


# ── The anti-merge ───────────────────────────────────────────────────


class TestSplitOnConflict:
    def test_disabled_by_default(self):
        """Zero behavior change unless a discriminator is declared."""
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td)
            a = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            cid = reg.upsert(a, reg.resolve(a), "a", "1")
            b = {"last_name": "Smith", "first_name": "Ada", "ssn": "999"}
            result = reg.resolve(b)
            assert result.tier == ResolutionTier.T1_EXACT
            assert reg.upsert(b, result, "b", "2") == cid
            assert reg.stats()["entities"] == 1

    def test_discriminator_conflict_refuses_the_merge(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            a = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            cid_a = reg.upsert(a, reg.resolve(a), "a", "1")

            b = {"last_name": "Smith", "first_name": "Ada", "ssn": "999"}
            result = reg.resolve(b)
            assert result.tier == ResolutionTier.NO_MATCH
            assert result.split_from == cid_a
            assert [c.field for c in result.split_conflicts] == ["ssn"]
            assert "refused" in result.notes

            cid_b = reg.upsert(b, result, "b", "2")
            assert cid_b != cid_a
            assert reg.stats()["entities"] == 2

    def test_split_is_recorded_with_both_values(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            a = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            cid_a = reg.upsert(a, reg.resolve(a), "a", "1")
            b = {"last_name": "Smith", "first_name": "Ada", "ssn": "999"}
            cid_b = reg.upsert(b, reg.resolve(b), "b", "2")

            rows = reg.splits()
            assert len(rows) == 1
            assert rows[0]["kept_id"] == cid_a
            assert rows[0]["split_id"] == cid_b
            assert rows[0]["field"] == "ssn"
            assert {rows[0]["kept_value"], rows[0]["split_value"]} == {"111", "999"}
            assert reg.stats()["splits"] == 1

    def test_agreeing_discriminator_still_merges(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            a = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            cid = reg.upsert(a, reg.resolve(a), "a", "1")
            b = {"last_name": "Smith", "first_name": "Ada", "ssn": "111",
                 "city": "Reno"}
            result = reg.resolve(b)
            assert result.tier == ResolutionTier.T1_EXACT
            assert reg.upsert(b, result, "b", "2") == cid
            assert reg.stats()["entities"] == 1

    def test_missing_discriminator_never_splits(self):
        """Absence is not disagreement — a partial record must still match."""
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            a = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            cid = reg.upsert(a, reg.resolve(a), "a", "1")
            b = {"last_name": "Smith", "first_name": "Ada"}
            result = reg.resolve(b)
            assert result.tier == ResolutionTier.T1_EXACT
            assert reg.upsert(b, result, "b", "2") == cid


class TestSkolemDigestPreventsRefusion:
    """The load-bearing property: a split must survive re-ingest.

    Without mixing the discriminator into the stored digest, re-resolving
    the split record recomputes the *base* ESS, matches the entity it was
    deliberately split from, and silently re-fuses the two.
    """

    def test_reingesting_the_split_record_lands_on_its_own_entity(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            a = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            cid_a = reg.upsert(a, reg.resolve(a), "a", "1")
            b = {"last_name": "Smith", "first_name": "Ada", "ssn": "999"}
            cid_b = reg.upsert(b, reg.resolve(b), "b", "2")

            again = reg.resolve(dict(b))
            assert again.tier == ResolutionTier.T1_EXACT
            assert again.canonical_id == cid_b
            assert reg.upsert(dict(b), again, "b", "3") == cid_b
            assert reg.stats()["entities"] == 2

    def test_original_record_still_routes_to_the_original(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            a = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            cid_a = reg.upsert(a, reg.resolve(a), "a", "1")
            b = {"last_name": "Smith", "first_name": "Ada", "ssn": "999"}
            reg.upsert(b, reg.resolve(b), "b", "2")

            again = reg.resolve(dict(a))
            assert again.canonical_id == cid_a

    def test_a_third_distinct_value_splits_again(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            ids = set()
            for i, ssn in enumerate(["111", "999", "555"]):
                rec = {"last_name": "Smith", "first_name": "Ada", "ssn": ssn}
                ids.add(reg.upsert(rec, reg.resolve(rec), "s", str(i)))
            assert len(ids) == 3
            assert reg.stats()["entities"] == 3

    def test_skolem_digest_is_deterministic_and_distinct(self):
        base = "a" * 64
        one = skolem_digest(base, {"ssn": "999"})
        assert one == skolem_digest(base, {"ssn": "999"})
        assert one != skolem_digest(base, {"ssn": "111"})
        assert one != base

    def test_ess_base_is_preserved_on_a_split_entity(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            a = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            reg.upsert(a, reg.resolve(a), "a", "1")
            b = {"last_name": "Smith", "first_name": "Ada", "ssn": "999"}
            cid_b = reg.upsert(b, reg.resolve(b), "b", "2")

            row = reg._conn.execute(
                "SELECT ess_digest, ess_base FROM entities WHERE canonical_id = ?",
                (cid_b,),
            ).fetchone()
            # Same identity fields -> same base; skolemized digest differs.
            assert row["ess_base"] == reg._compute_ess(b).digest
            assert row["ess_digest"] != row["ess_base"]


class TestSplitReporting:
    def test_batch_report_counts_refusals(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            seed = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            reg.upsert(seed, reg.resolve(seed), "a", "1")

            report = ResolutionReport()
            canonicalize_batch(
                [{"last_name": "Smith", "first_name": "Ada", "ssn": "999",
                  "source_id": "2"}],
                reg,
                "b",
                dry_run=True,
                report=report,
            )
            assert report.splits_refused == 1
            assert [c["reason"] for c in report.conflicts] == ["discriminator"]

    def test_dry_run_does_not_create_the_split_entity(self):
        with tempfile.TemporaryDirectory() as td:
            reg = _reg(td, split_on_conflict={"ssn"})
            seed = {"last_name": "Smith", "first_name": "Ada", "ssn": "111"}
            reg.upsert(seed, reg.resolve(seed), "a", "1")
            before = reg.stats()

            canonicalize_batch(
                [{"last_name": "Smith", "first_name": "Ada", "ssn": "999",
                  "source_id": "2"}],
                reg,
                "b",
                dry_run=True,
            )
            assert reg.stats() == before


class TestPolicyValidation:
    def test_variants_is_a_valid_conflicts_mode(self):
        assert ResolutionPolicy(conflicts="variants").conflicts == "variants"

    def test_split_on_conflict_is_frozen(self):
        policy = ResolutionPolicy(split_on_conflict={"ssn"})
        assert isinstance(policy.split_on_conflict, frozenset)

    def test_rejects_a_field_that_is_both_combined_and_a_discriminator(self):
        with pytest.raises(ValueError, match="cannot be both"):
            ResolutionPolicy(combine_fields={"ssn"}, split_on_conflict={"ssn"})

    def test_warns_when_a_discriminator_is_not_declared_by_the_schema(self, caplog):
        """An undeclared discriminator is never stored, so it never splits."""
        with tempfile.TemporaryDirectory() as td:
            with caplog.at_level("WARNING"):
                _reg(td, split_on_conflict={"passport_no"})
        assert "passport_no" in caplog.text
        assert "never stored" in caplog.text

    def test_no_warning_for_a_declared_discriminator(self, caplog):
        with tempfile.TemporaryDirectory() as td:
            with caplog.at_level("WARNING"):
                _reg(td, split_on_conflict={"ssn"})
        assert "never stored" not in caplog.text


class TestMigration:
    def test_registry_written_before_ess_base_still_opens(self):
        """Old DBs predate the column; CREATE TABLE IF NOT EXISTS won't add it."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "legacy.db"
            conn = sqlite3.connect(str(path))
            conn.executescript(
                """
                CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE entities (
                    canonical_id TEXT PRIMARY KEY,
                    ess_digest TEXT NOT NULL,
                    ess_fields TEXT NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    source_count INTEGER DEFAULT 1,
                    merged_from TEXT,
                    UNIQUE(ess_digest)
                );
                """
            )
            conn.execute(
                "INSERT INTO _meta VALUES ('schema_hash', ?)", (SCHEMA.schema_hash(),)
            )
            conn.execute(
                "INSERT INTO entities VALUES (?, ?, ?, ?, ?, 1, NULL)",
                ("old1", "deadbeef", json.dumps({"last_name": "Smith"}),
                 "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
            )
            conn.commit()
            conn.close()

            reg = CanonicalRegistry(path, SCHEMA)
            columns = {
                r["name"]
                for r in reg._conn.execute("PRAGMA table_info(entities)").fetchall()
            }
            assert "ess_base" in columns

            backfilled = reg._conn.execute(
                "SELECT ess_base FROM entities WHERE canonical_id = 'old1'"
            ).fetchone()["ess_base"]
            assert backfilled == "deadbeef"

    def test_backfill_repairs_null_ess_base_written_by_an_older_version(self):
        """The downgrade cycle: migrated -> older writer -> upgrade.

        An older spiritwriter does not know ``ess_base`` and inserts NULL
        there. A migration that only backfilled when *adding* the column
        skipped the repair on the next open, and since ``find_by_ess``
        matches on ``ess_base`` the row became permanently unfindable —
        the next ingest minted a duplicate instead of matching it.
        """
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "cycle.db"
            reg = CanonicalRegistry(path, SCHEMA)
            first = {"last_name": "Smith", "first_name": "Ada"}
            reg.upsert(first, reg.resolve(first), "a", "1")
            reg.close()

            # Simulate the older writer: it has no ess_base in its INSERT.
            conn = sqlite3.connect(str(path))
            conn.execute(
                "INSERT INTO entities "
                "(canonical_id, ess_digest, ess_fields, first_seen, last_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    "legacy1",
                    "digest-written-by-old-version",
                    json.dumps({"last_name": "Jones", "first_name": "Ada"}),
                    "2026-01-01T00:00:00Z",
                    "2026-01-01T00:00:00Z",
                ),
            )
            conn.commit()
            nulls = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE ess_base IS NULL"
            ).fetchone()[0]
            conn.close()
            assert nulls == 1, "precondition: the old writer left a NULL"

            # Upgrade again — the backfill must repair it.
            reg = CanonicalRegistry(path, SCHEMA)
            remaining = reg._conn.execute(
                "SELECT COUNT(*) as c FROM entities WHERE ess_base IS NULL"
            ).fetchone()["c"]
            assert remaining == 0
            assert (
                reg._conn.execute(
                    "SELECT ess_base FROM entities WHERE canonical_id = 'legacy1'"
                ).fetchone()["ess_base"]
                == "digest-written-by-old-version"
            )

    def test_backfill_does_not_disturb_a_split_entity(self):
        """The repair must not overwrite a deliberately skolemized digest."""
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "split.db"
            reg = _reg_at(path, split_on_conflict={"ssn"})
            for i, ssn in enumerate(["111", "999"]):
                rec = {"last_name": "Smith", "first_name": "Ada", "ssn": ssn}
                reg.upsert(rec, reg.resolve(rec), "s", str(i))
            before = reg._conn.execute(
                "SELECT canonical_id, ess_digest, ess_base FROM entities "
                "ORDER BY canonical_id"
            ).fetchall()
            reg.close()

            reopened = CanonicalRegistry(path, SCHEMA)
            after = reopened._conn.execute(
                "SELECT canonical_id, ess_digest, ess_base FROM entities "
                "ORDER BY canonical_id"
            ).fetchall()
            assert [tuple(r) for r in before] == [tuple(r) for r in after]

    def test_migration_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "r.db"
            first = CanonicalRegistry(path, SCHEMA)
            rec = {"last_name": "Smith", "first_name": "Ada"}
            cid = first.upsert(rec, first.resolve(rec), "a", "1")
            first.close()

            second = CanonicalRegistry(path, SCHEMA)
            assert second.resolve(rec).canonical_id == cid
            assert second.stats()["entities"] == 1
