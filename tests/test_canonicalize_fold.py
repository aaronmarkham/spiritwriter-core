"""Tests for attribute folding, its dry-run twin, and the fold policy.

Covers the gap that folding closes: before it, a canonical entity's
stored fields were frozen at first sight — a later richer sighting
contributed nothing and a later contradicting one raised no signal.
"""

import json
import tempfile
from pathlib import Path

import pytest

from spiritwriter.fabric.canonicalize import (
    DEFAULT_POLICY,
    CanonicalRegistry,
    CanonicalSchema,
    FieldConflict,
    ResolutionPolicy,
    ResolutionReport,
    ResolutionTier,
    canonicalize_batch,
    field_conflicts,
    fold_entity_fields,
    scalars_equivalent,
)

SCHEMA = CanonicalSchema(
    name="person",
    ess_fields=["last_name", "first_name"],
    fuzzy_fields={"last_name": 0.85},
    context_fields=["city", "employer"],
    metadata_fields=["notes", "phone"],
)


@pytest.fixture
def registry():
    with tempfile.TemporaryDirectory() as td:
        yield CanonicalRegistry(Path(td) / "r.db", SCHEMA)


def _reg(td, **policy_kwargs):
    policy = ResolutionPolicy(**policy_kwargs) if policy_kwargs else DEFAULT_POLICY
    return CanonicalRegistry(Path(td) / "r.db", SCHEMA, policy=policy)


class TestScalarsEquivalent:
    def test_case_and_whitespace_insensitive(self):
        assert scalars_equivalent("Ada  Lovelace", "ada lovelace")
        assert scalars_equivalent("  x ", "x")

    def test_genuine_difference(self):
        assert not scalars_equivalent("Ada", "Grace")

    def test_none_handling(self):
        assert scalars_equivalent(None, None)
        assert not scalars_equivalent(None, "x")


class TestFoldPurity:
    def test_does_not_mutate_arguments(self):
        current = {"city": "Reno"}
        incoming = {"city": "Sparks"}
        before_current = dict(current)
        before_incoming = dict(incoming)

        fold_entity_fields(current, incoming, SCHEMA)

        assert current == before_current
        assert incoming == before_incoming

    def test_returns_a_new_dict(self):
        current = {"city": "Reno"}
        result = fold_entity_fields(current, {"city": "Reno"}, SCHEMA)
        assert result.fields is not current

    def test_deterministic_across_repeated_calls(self):
        current = {"city": "Reno", "notes": "First."}
        incoming = {"city": "Sparks", "employer": "ACME", "notes": "Second."}
        runs = [
            fold_entity_fields(current, incoming, SCHEMA).fields for _ in range(5)
        ]
        assert all(r == runs[0] for r in runs)


class TestFoldRules:
    def test_fills_empty_field(self):
        result = fold_entity_fields({"city": ""}, {"city": "Reno"}, SCHEMA)
        assert result.fields["city"] == "Reno"
        assert result.filled == ("city",)
        assert result.conflicts == ()
        assert result.changed

    def test_fills_missing_field(self):
        result = fold_entity_fields({}, {"employer": "ACME"}, SCHEMA)
        assert result.fields["employer"] == "ACME"
        assert result.filled == ("employer",)

    def test_fill_empty_can_be_disabled(self):
        policy = ResolutionPolicy(fill_empty=False)
        result = fold_entity_fields({"city": ""}, {"city": "Reno"}, SCHEMA, policy)
        assert result.fields["city"] == ""
        assert not result.changed

    def test_equivalent_values_are_not_a_conflict(self):
        result = fold_entity_fields({"city": "Reno"}, {"city": "  reno "}, SCHEMA)
        assert result.conflicts == ()
        assert not result.changed

    def test_genuine_conflict_keeps_first_by_default(self):
        result = fold_entity_fields({"city": "Reno"}, {"city": "Sparks"}, SCHEMA)
        assert result.fields["city"] == "Reno"
        assert len(result.conflicts) == 1
        assert result.conflicts[0] == FieldConflict("city", "Reno", "Sparks", "scalar")
        assert not result.changed

    def test_empty_incoming_never_overwrites(self):
        result = fold_entity_fields({"city": "Reno"}, {"city": "   "}, SCHEMA)
        assert result.fields["city"] == "Reno"
        assert result.conflicts == ()

    def test_undeclared_fields_are_ignored(self):
        result = fold_entity_fields({}, {"unknown_field": "x"}, SCHEMA)
        assert "unknown_field" not in result.fields


class TestIdentityFieldsAreNeverFolded:
    def test_identity_disagreement_reports_but_does_not_write(self):
        result = fold_entity_fields(
            {"last_name": "Smith"}, {"last_name": "Smythe"}, SCHEMA
        )
        assert result.fields["last_name"] == "Smith"
        assert not result.changed
        assert result.conflicts == (
            FieldConflict("last_name", "Smith", "Smythe", "identity"),
        )

    def test_identity_field_is_not_filled_when_empty(self):
        # Filling one would desynchronize the stored ess_digest.
        result = fold_entity_fields({}, {"last_name": "Smith"}, SCHEMA)
        assert "last_name" not in result.fields

    def test_richest_precedence_does_not_override_identity(self):
        policy = ResolutionPolicy(precedence="richest")
        result = fold_entity_fields(
            {"last_name": "Smith"},
            {"last_name": "Smythe", "city": "Reno", "employer": "ACME"},
            SCHEMA,
            policy,
        )
        assert result.fields["last_name"] == "Smith"


class TestRichestPrecedence:
    def test_richer_incoming_wins(self):
        policy = ResolutionPolicy(precedence="richest")
        result = fold_entity_fields(
            {"city": "Reno"},
            {"city": "Sparks", "employer": "ACME", "notes": "n"},
            SCHEMA,
            policy,
        )
        assert result.fields["city"] == "Sparks"
        assert result.conflicts[0].kept == "Sparks"

    def test_tie_falls_back_to_keep_first(self):
        policy = ResolutionPolicy(precedence="richest")
        result = fold_entity_fields({"city": "Reno"}, {"city": "Sparks"}, SCHEMA, policy)
        assert result.fields["city"] == "Reno"

    def test_poorer_incoming_loses(self):
        policy = ResolutionPolicy(precedence="richest")
        result = fold_entity_fields(
            {"city": "Reno", "employer": "ACME"}, {"city": "Sparks"}, SCHEMA, policy
        )
        assert result.fields["city"] == "Reno"


class TestCombineFields:
    def test_sentences_are_appended(self):
        policy = ResolutionPolicy(combine_fields={"notes"})
        result = fold_entity_fields(
            {"notes": "Seen downtown."}, {"notes": "Drives a truck."}, SCHEMA, policy
        )
        assert result.fields["notes"] == "Seen downtown. Drives a truck."
        assert result.combined == ("notes",)
        assert result.conflicts == ()

    def test_duplicate_sentences_are_not_repeated(self):
        policy = ResolutionPolicy(combine_fields={"notes"})
        result = fold_entity_fields(
            {"notes": "Seen downtown."}, {"notes": "seen  DOWNTOWN."}, SCHEMA, policy
        )
        assert result.fields["notes"] == "Seen downtown."
        assert not result.changed

    def test_truncation_respects_bound(self):
        policy = ResolutionPolicy(combine_fields={"notes"}, combine_max_length=20)
        result = fold_entity_fields(
            {"notes": "A" * 15 + "."}, {"notes": "B" * 40 + "."}, SCHEMA, policy
        )
        assert len(result.fields["notes"]) <= 20


class TestKeepAllConflicts:
    def test_dropped_values_are_stored(self):
        policy = ResolutionPolicy(conflicts="keep-all")
        result = fold_entity_fields({"city": "Reno"}, {"city": "Sparks"}, SCHEMA, policy)
        assert result.fields["__conflicts__"] == {"city": ["Sparks"]}
        assert result.changed

    def test_repeated_drop_is_not_duplicated(self):
        policy = ResolutionPolicy(conflicts="keep-all")
        first = fold_entity_fields({"city": "Reno"}, {"city": "Sparks"}, SCHEMA, policy)
        second = fold_entity_fields(
            first.fields, {"city": "Sparks"}, SCHEMA, policy
        )
        assert second.fields["__conflicts__"] == {"city": ["Sparks"]}

    def test_conflicts_key_is_not_counted_as_richness(self):
        policy = ResolutionPolicy(precedence="richest", conflicts="keep-all")
        current = {"city": "Reno", "__conflicts__": {"city": ["X"]}}
        result = fold_entity_fields(current, {"city": "Sparks"}, SCHEMA, policy)
        # One real field each side -> tie -> keep-first.
        assert result.fields["city"] == "Reno"


class TestPolicyValidation:
    def test_rejects_unknown_precedence(self):
        with pytest.raises(ValueError, match="precedence"):
            ResolutionPolicy(precedence="whatever")

    def test_rejects_unknown_conflicts(self):
        with pytest.raises(ValueError, match="conflicts"):
            ResolutionPolicy(conflicts="whatever")

    def test_rejects_nonpositive_max_length(self):
        with pytest.raises(ValueError, match="combine_max_length"):
            ResolutionPolicy(combine_max_length=0)

    def test_combine_fields_is_frozen(self):
        policy = ResolutionPolicy(combine_fields={"notes"})
        assert isinstance(policy.combine_fields, frozenset)

    def test_policy_is_hashable(self):
        assert {ResolutionPolicy(), ResolutionPolicy()} != set()


class TestInertPolicyWarnings:
    """A combine_field the fold can never reach should say so.

    Naming an unreachable field is accepted silently and simply does
    nothing, so a typo reads as "combining never applied" rather than as
    a mistake. Two distinct causes, each with its own message.
    """

    def test_warns_when_the_schema_does_not_declare_the_field(self, caplog):
        with tempfile.TemporaryDirectory() as td:
            with caplog.at_level("WARNING"):
                CanonicalRegistry(Path(td) / "r.db", SCHEMA,
                                  policy=ResolutionPolicy(combine_fields={"summary"}))
        assert "summary" in caplog.text
        assert "does not declare" in caplog.text

    def test_warns_when_the_field_is_an_identity_field(self, caplog):
        with tempfile.TemporaryDirectory() as td:
            with caplog.at_level("WARNING"):
                CanonicalRegistry(Path(td) / "r.db", SCHEMA,
                                  policy=ResolutionPolicy(combine_fields={"last_name"}))
        assert "last_name" in caplog.text
        assert "never folded" in caplog.text

    def test_no_warning_for_a_declared_foldable_field(self, caplog):
        with tempfile.TemporaryDirectory() as td:
            with caplog.at_level("WARNING"):
                CanonicalRegistry(Path(td) / "r.db", SCHEMA,
                                  policy=ResolutionPolicy(combine_fields={"notes"}))
        assert "combine_fields" not in caplog.text

    def test_no_warning_when_combine_fields_is_empty(self, caplog):
        with tempfile.TemporaryDirectory() as td:
            with caplog.at_level("WARNING"):
                CanonicalRegistry(Path(td) / "r.db", SCHEMA)
        assert "combine_fields" not in caplog.text

    def test_the_warning_is_accurate_the_field_really_is_inert(self):
        """Pin the behaviour the warning describes."""
        current = {"notes": "First sentence."}
        incoming = {"notes": "Second sentence."}
        undeclared = fold_entity_fields(
            current, incoming, SCHEMA,
            ResolutionPolicy(combine_fields={"summary"}))
        assert undeclared.combined == ()
        assert [c.field for c in undeclared.conflicts] == ["notes"]

        declared = fold_entity_fields(
            current, incoming, SCHEMA,
            ResolutionPolicy(combine_fields={"notes"}))
        assert declared.combined == ("notes",)
        assert declared.conflicts == ()


class TestDryRunTwin:
    def test_field_conflicts_matches_the_real_fold(self):
        current = {"city": "Reno", "employer": "ACME"}
        incoming = {"city": "Sparks", "employer": "Globex"}
        predicted = field_conflicts(current, incoming, SCHEMA)
        actual = list(fold_entity_fields(current, incoming, SCHEMA).conflicts)
        assert predicted == actual

    def test_field_conflicts_writes_nothing(self):
        current = {"city": "Reno"}
        field_conflicts(current, {"city": "Sparks"}, SCHEMA)
        assert current == {"city": "Reno"}


class TestRegistryFolding:
    def test_second_sighting_fills_empty_field(self, registry):
        first = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
        r1 = registry.resolve(first)
        cid = registry.upsert(first, r1, "src-a", "1")

        second = {
            "last_name": "Smith",
            "first_name": "Ada",
            "city": "Reno",
            "employer": "ACME",
        }
        r2 = registry.resolve(second)
        assert r2.tier == ResolutionTier.T1_EXACT
        registry.upsert(second, r2, "src-b", "2")

        entity = registry.get_entity(cid)
        assert entity["ess_fields"]["employer"] == "ACME"
        assert entity["source_count"] == 2

    def test_conflicting_value_keeps_stored_by_default(self, registry):
        first = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
        r1 = registry.resolve(first)
        cid = registry.upsert(first, r1, "src-a", "1")

        second = {"last_name": "Smith", "first_name": "Ada", "city": "Sparks"}
        r2 = registry.resolve(second)
        registry.upsert(second, r2, "src-b", "2")

        assert registry.get_entity(cid)["ess_fields"]["city"] == "Reno"

    def test_plan_predicts_the_write_exactly(self, registry):
        first = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
        r1 = registry.resolve(first)
        cid = registry.upsert(first, r1, "src-a", "1")

        second = {
            "last_name": "Smith",
            "first_name": "Ada",
            "city": "Reno",
            "employer": "ACME",
        }
        r2 = registry.resolve(second)
        planned = registry.plan(second, r2)
        assert planned is not None

        registry.upsert(second, r2, "src-b", "2")
        assert registry.get_entity(cid)["ess_fields"] == planned.fields

    def test_plan_writes_nothing(self, registry):
        first = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
        r1 = registry.resolve(first)
        cid = registry.upsert(first, r1, "src-a", "1")
        before = registry.get_entity(cid)

        second = {
            "last_name": "Smith",
            "first_name": "Ada",
            "employer": "ACME",
        }
        registry.plan(second, registry.resolve(second))
        assert registry.get_entity(cid) == before

    def test_plan_returns_none_for_new_entity(self, registry):
        record = {"last_name": "Nobody", "first_name": "New"}
        assert registry.plan(record, registry.resolve(record)) is None

    def test_stored_blob_stays_json_serializable(self, registry):
        first = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
        r1 = registry.resolve(first)
        cid = registry.upsert(first, r1, "src-a", "1")
        second = {"last_name": "Smith", "first_name": "Ada", "employer": "ACME"}
        registry.upsert(second, registry.resolve(second), "src-b", "2")

        row = registry._conn.execute(
            "SELECT ess_fields FROM entities WHERE canonical_id = ?", (cid,)
        ).fetchone()
        assert isinstance(json.loads(row["ess_fields"]), dict)


class TestFoldDoesNotContaminateIdentity:
    """The blob feeds resolution, so pin what folding may and may not reach.

    Both `_fuzzy_resolve` and `_context_resolve` filter the stored blob to
    `schema.ess_fields` before computing ESS overlap. Bookkeeping keys and
    folded non-identity fields must never leak into that computation.
    """

    def test_conflicts_key_never_reaches_ess_overlap(self):
        with tempfile.TemporaryDirectory() as td:
            registry = _reg(td, conflicts="keep-all")
            seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            cid = registry.upsert(seed, registry.resolve(seed), "a", "1")

            clash = {"last_name": "Smith", "first_name": "Ada", "city": "Sparks"}
            registry.upsert(clash, registry.resolve(clash), "b", "2")

            stored = registry.get_entity(cid)["ess_fields"]
            assert "__conflicts__" in stored  # the fold did record it

            # The same person still resolves T1 — the bookkeeping key did
            # not perturb the digest or the overlap.
            again = registry.resolve(
                {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            )
            assert again.tier == ResolutionTier.T1_EXACT
            assert again.canonical_id == cid

    def test_ess_digest_is_stable_across_folds(self):
        with tempfile.TemporaryDirectory() as td:
            registry = _reg(td)
            seed = {"last_name": "Smith", "first_name": "Ada"}
            cid = registry.upsert(seed, registry.resolve(seed), "a", "1")
            digest_before = registry._conn.execute(
                "SELECT ess_digest FROM entities WHERE canonical_id = ?", (cid,)
            ).fetchone()["ess_digest"]

            enrich = {
                "last_name": "Smith",
                "first_name": "Ada",
                "city": "Reno",
                "employer": "ACME",
                "notes": "n",
            }
            registry.upsert(enrich, registry.resolve(enrich), "b", "2")

            digest_after = registry._conn.execute(
                "SELECT ess_digest FROM entities WHERE canonical_id = ?", (cid,)
            ).fetchone()["ess_digest"]
            assert digest_before == digest_after

    def test_filling_a_context_field_feeds_later_context_resolution(self):
        """Documented consequence of fill_empty — pinned, not accidental."""
        with tempfile.TemporaryDirectory() as td:
            registry = _reg(td)
            seed = {"last_name": "Smith", "first_name": "Ada"}
            cid = registry.upsert(seed, registry.resolve(seed), "a", "1")
            assert "city" not in registry.get_entity(cid)["ess_fields"]

            enrich = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            registry.upsert(enrich, registry.resolve(enrich), "b", "2")
            assert registry.get_entity(cid)["ess_fields"]["city"] == "Reno"


class TestBatchDryRun:
    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            registry = _reg(td)
            seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno"}
            registry.upsert(seed, registry.resolve(seed), "src-a", "1")
            before = registry.stats()

            canonicalize_batch(
                [{"last_name": "Smith", "first_name": "Ada", "employer": "ACME"}],
                registry,
                "src-b",
                dry_run=True,
            )
            assert registry.stats() == before

    def test_dry_run_report_predicts_the_real_run(self):
        records = [
            {"last_name": "Smith", "first_name": "Ada", "employer": "ACME",
             "source_id": "2"},
        ]
        seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno",
                "source_id": "1"}

        with tempfile.TemporaryDirectory() as td:
            registry = _reg(td)
            registry.upsert(seed, registry.resolve(seed), "src-a", "1")
            dry = ResolutionReport()
            canonicalize_batch(records, registry, "src-b", dry_run=True, report=dry)

        with tempfile.TemporaryDirectory() as td:
            registry = _reg(td)
            registry.upsert(seed, registry.resolve(seed), "src-a", "1")
            wet = ResolutionReport()
            canonicalize_batch(records, registry, "src-b", report=wet)

        assert dry.to_dict()["fields_filled"] == wet.to_dict()["fields_filled"]
        assert dry.to_dict()["conflicts"] == wet.to_dict()["conflicts"]
        assert dry.to_dict()["tiers"] == wet.to_dict()["tiers"]

    def test_report_is_timestamp_free_and_diffable(self):
        seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno",
                "source_id": "1"}
        records = [{"last_name": "Smith", "first_name": "Ada", "city": "Sparks",
                    "source_id": "2"}]

        def run():
            with tempfile.TemporaryDirectory() as td:
                registry = _reg(td)
                registry.upsert(seed, registry.resolve(seed), "src-a", "1")
                report = ResolutionReport()
                canonicalize_batch(records, registry, "src-b", dry_run=True,
                                   report=report)
                return json.dumps(report.to_dict(), sort_keys=True)

        assert run() == run()

    def test_report_counts_tiers_and_conflicts(self):
        seed = {"last_name": "Smith", "first_name": "Ada", "city": "Reno",
                "source_id": "1"}
        with tempfile.TemporaryDirectory() as td:
            registry = _reg(td)
            registry.upsert(seed, registry.resolve(seed), "src-a", "1")
            report = ResolutionReport()
            canonicalize_batch(
                [
                    {"last_name": "Smith", "first_name": "Ada", "city": "Sparks",
                     "source_id": "2"},
                    {"last_name": "Brand", "first_name": "New", "source_id": "3"},
                ],
                registry,
                "src-b",
                dry_run=True,
                report=report,
            )

        assert report.records == 2
        assert report.dry_run is True
        assert report.tiers[ResolutionTier.T1_EXACT.value] == 1
        assert report.entities_created == 1
        assert [c["field"] for c in report.conflicts] == ["city"]
        assert report.identity_conflicts == 0

    def test_batch_without_report_still_writes(self):
        with tempfile.TemporaryDirectory() as td:
            registry = _reg(td)
            canonicalize_batch(
                [{"last_name": "Solo", "first_name": "Han", "source_id": "1"}],
                registry,
                "src",
            )
            assert registry.stats()["entities"] == 1
