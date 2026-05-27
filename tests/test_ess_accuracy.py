"""Tests for the ESS accuracy harness.

Smoke tests that the harness loads corpora, runs mutations, scores
pairs, and produces a valid report. Per-corpus number stability isn't
asserted here — that's what the report artifacts are for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spiritwriter.fabric.canonicalize import (
    CanonicalRegistry,
    CanonicalSchema,
    ResolutionTier,
)

from benchmarks.eval.ess_accuracy.baselines import jaccard_score
from benchmarks.eval.ess_accuracy.corpus import load_corpus
from benchmarks.eval.ess_accuracy.metrics import (
    render_markdown,
    score,
)
from benchmarks.eval.ess_accuracy.mutations import (
    UNIVERSAL_FAMILIES,
    Mutation,
    MutationFamily,
)


# ── Universal mutation rules ────────────────────────────────────────


def test_universal_families_registered():
    names = {f.name for f in UNIVERSAL_FAMILIES}
    assert "case" in names
    assert "whitespace" in names
    assert "typo_substitution" in names
    assert "typo_insertion" in names
    assert "unicode_normalization" in names
    assert "negative_control" in names


def test_case_mutations_produce_t1_expected():
    family = next(f for f in UNIVERSAL_FAMILIES if f.name == "case")
    record = {"name": "Martinez", "id": 1}
    muts = family.generate(record, ess_fields=["name"])
    assert muts
    for m in muts:
        assert m.same_entity is True
        assert m.expected_tier_min == "t1_exact"


def test_negative_control_marks_different_entity():
    family = next(f for f in UNIVERSAL_FAMILIES if f.name == "negative_control")
    record = {"name": "Martinez", "id": 1}
    muts = family.generate(record, ess_fields=["name"])
    assert muts
    for m in muts:
        assert m.same_entity is False
        assert m.expected_tier_min is None


def test_typo_substitution_preserves_length():
    family = next(f for f in UNIVERSAL_FAMILIES if f.name == "typo_substitution")
    record = {"name": "Martinez"}
    muts = family.generate(record, ess_fields=["name"])
    for m in muts:
        assert len(m.mutated["name"]) == len(record["name"])
        assert m.mutated["name"] != record["name"]


def test_unicode_normalization_strips_diacritics():
    family = next(f for f in UNIVERSAL_FAMILIES if f.name == "unicode_normalization")
    record = {"name": "María"}
    muts = family.generate(record, ess_fields=["name"])
    assert muts
    assert all("í" not in m.mutated["name"] for m in muts)


def test_mutation_is_deterministic():
    """Same record + same family must produce identical mutations across runs."""
    family = next(f for f in UNIVERSAL_FAMILIES if f.name == "typo_substitution")
    record = {"name": "Martinez", "id": 1}
    a = family.generate(record, ess_fields=["name"])
    b = family.generate(record, ess_fields=["name"])
    assert [m.mutated for m in a] == [m.mutated for m in b]


def test_mutation_is_deterministic_across_processes(tmp_path):
    """Seeding must NOT depend on Python's per-process hash randomization.

    Running the same generator under two different PYTHONHASHSEED values
    must produce byte-identical mutations. Regression test for using
    ``hash()`` (randomized per process) vs a stable digest.
    """
    import json
    import subprocess
    import sys

    script = tmp_path / "gen.py"
    script.write_text(
        "import json, sys\n"
        f"sys.path.insert(0, {repr(str(Path(__file__).resolve().parent.parent))})\n"
        "from benchmarks.eval.ess_accuracy.mutations import UNIVERSAL_FAMILIES\n"
        "fam = next(f for f in UNIVERSAL_FAMILIES if f.name == 'typo_substitution')\n"
        "muts = fam.generate({'name': 'Martinez', 'id': 1}, ess_fields=['name'])\n"
        "print(json.dumps([m.mutated for m in muts], sort_keys=True))\n",
        encoding="utf-8",
    )

    def _run(seed: str) -> str:
        env = {"PYTHONHASHSEED": seed, "PATH": __import__("os").environ.get("PATH", "")}
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True, env=env, check=True,
        )
        return result.stdout.strip()

    out_a = _run("0")
    out_b = _run("12345")
    assert out_a == out_b, (
        f"Mutations differ across PYTHONHASHSEED values:\n  seed=0: {out_a}\n"
        f"  seed=12345: {out_b}"
    )


# ── Jaccard baseline ────────────────────────────────────────────────


def test_jaccard_identical_records():
    a = {"name": "Martinez", "first": "Carlos"}
    assert jaccard_score(a, a, ["name", "first"]) == 1.0


def test_jaccard_disjoint_records():
    a = {"name": "Smith"}
    b = {"name": "Williams"}
    assert jaccard_score(a, b, ["name"]) == 0.0


def test_jaccard_partial_overlap():
    a = {"text": "the quick brown fox"}
    b = {"text": "the lazy brown dog"}
    score_val = jaccard_score(a, b, ["text"])
    # Intersection: {the, brown} = 2. Union: {the, quick, brown, fox, lazy, dog} = 6.
    assert score_val == pytest.approx(2 / 6, abs=0.01)


# ── Corpus loader ───────────────────────────────────────────────────


def test_loads_people_corpus():
    corpus = load_corpus("people")
    assert corpus.name == "people"
    assert corpus.schema.name == "person"
    assert "last_name" in corpus.schema.ess_fields
    assert len(corpus.entities) >= 30
    # Universal families plus people-specific
    family_names = {f.name for f in corpus.families}
    assert "case" in family_names
    assert "surname_duplication" in family_names  # the frio-derived rule
    # Schema's jaccard_fields excludes DOB
    assert corpus.jaccard_fields == ["last_name", "first_name"]


def test_load_corpus_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_corpus(str(tmp_path / "does_not_exist"))


def test_load_corpus_from_explicit_path(tmp_path):
    # Build a minimal corpus on the fly and load it by path.
    (tmp_path / "schema.json").write_text(json.dumps({
        "name": "test_schema",
        "ess_fields": ["k"],
        "fuzzy_fields": {"k": 0.80},
    }), encoding="utf-8")
    (tmp_path / "entities.json").write_text(json.dumps([
        {"k": "alpha"}, {"k": "beta"},
    ]), encoding="utf-8")
    corpus = load_corpus(str(tmp_path))
    assert corpus.schema.name == "test_schema"
    assert len(corpus.entities) == 2


# ── End-to-end scoring ──────────────────────────────────────────────


def test_score_produces_report_with_expected_fields(tmp_path):
    corpus = load_corpus("people")
    with CanonicalRegistry(tmp_path / "reg.db", corpus.schema) as registry:
        report = score(corpus, registry)

    assert report.corpus_name == "people"
    assert report.n_entities == len(corpus.entities)
    assert report.n_pairs > 0
    assert report.n_same > 0
    assert report.n_different > 0  # negative_control produces these
    assert 0.0 <= report.recall_t1 <= 1.0
    assert 0.0 <= report.recall_t1_t2 <= 1.0
    assert 0.0 <= report.recall_any_tier <= 1.0
    assert 0.0 <= report.false_merge_rate <= 1.0
    # CMC-Lite: T1+T2 auto-merge MUST hold false-merge close to zero
    assert report.false_merge_rate == 0.0


# The cross-corpus marketing claim is "5 corpora" — make CI defend it
# by parametrizing the precision/false-merge tests over every shipped
# corpus, not just `people`. Add a new corpus to data/<name>/ and CI
# will defend the invariants on it automatically.
SHIPPED_CORPORA = ["people", "case_only", "inmate_clean", "publications"]


@pytest.mark.parametrize("corpus_name", SHIPPED_CORPORA)
def test_false_merge_rate_meets_target(tmp_path, corpus_name):
    """≤ 5% false-merge target from CMC spec — hard CMC-Lite invariant."""
    corpus = load_corpus(corpus_name)
    with CanonicalRegistry(tmp_path / f"{corpus_name}.db", corpus.schema) as registry:
        report = score(corpus, registry)
    assert report.false_merge_rate <= 0.05, (
        f"[{corpus_name}] False-merge rate {report.false_merge_rate:.3f} "
        f"exceeded target 0.05"
    )


@pytest.mark.parametrize("corpus_name", SHIPPED_CORPORA)
def test_auto_merge_precision_is_perfect(tmp_path, corpus_name):
    """Among T1+T2 auto-merges, every pair must actually be same-entity.

    The meaningful CMC-Lite correctness invariant: the engine is allowed
    to be conservative (refuse to auto-merge), but it must NEVER auto-merge
    two entities that aren't actually the same. Parametrized so CI defends
    the cross-corpus claim, not just the `people` baseline.
    """
    auto_merge = {ResolutionTier.T1_EXACT.value, ResolutionTier.T2_STRONG.value}
    corpus = load_corpus(corpus_name)
    with CanonicalRegistry(tmp_path / f"{corpus_name}.db", corpus.schema) as registry:
        report = score(corpus, registry)
    tp = sum(1 for p in report.pairs if p.same_entity and p.ess_tier in auto_merge)
    fp = sum(1 for p in report.pairs if not p.same_entity and p.ess_tier in auto_merge)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    assert precision >= 1.0, (
        f"[{corpus_name}] Auto-merge precision dropped to {precision:.3f} "
        f"(TP={tp}, FP={fp}); any FP means CMC-Lite incorrectly merged "
        f"entities that should have stayed separate."
    )


@pytest.mark.parametrize("corpus_name", SHIPPED_CORPORA)
def test_case_mutations_resolve_to_t1(tmp_path, corpus_name):
    """Concrete invariant: case variation MUST resolve at T1_EXACT
    on every corpus that exercises it."""
    corpus = load_corpus(corpus_name)
    with CanonicalRegistry(tmp_path / f"{corpus_name}.db", corpus.schema) as registry:
        report = score(corpus, registry)
    case_pairs = [p for p in report.pairs if p.family == "case"]
    assert case_pairs, f"[{corpus_name}] no case mutations generated"
    for p in case_pairs:
        assert p.ess_tier == ResolutionTier.T1_EXACT.value, (
            f"[{corpus_name}] Case mutation landed at {p.ess_tier}, "
            f"expected t1_exact: {p.canonical} -> {p.mutated}"
        )


@pytest.mark.parametrize("corpus_name", SHIPPED_CORPORA)
def test_negative_control_never_auto_merges(tmp_path, corpus_name):
    """False-merge canary: garbled ESS fields MUST NOT auto-merge."""
    corpus = load_corpus(corpus_name)
    with CanonicalRegistry(tmp_path / f"{corpus_name}.db", corpus.schema) as registry:
        report = score(corpus, registry)
    negs = [p for p in report.pairs if p.family == "negative_control"]
    assert negs
    auto_merge = {"t1_exact", "t2_strong"}
    for p in negs:
        assert p.ess_tier not in auto_merge, (
            f"Negative control auto-merged at {p.ess_tier}: {p.canonical} -> {p.mutated}"
        )


# ── Falsification battery families ─────────────────────────────────


def test_garbled_all_fields_marks_different_entity():
    """Universal `garbled_all_fields` family — every mutation is a
    no-overlap negative case; same_entity must be False."""
    family = next(f for f in UNIVERSAL_FAMILIES if f.name == "garbled_all_fields")
    record = {"name": "Martinez", "first": "Carlos"}
    muts = family.generate(record, ess_fields=["name", "first"])
    assert muts, "garbled_all_fields produced no mutations on a record with string ESS fields"
    for m in muts:
        assert m.same_entity is False
        assert m.expected_tier_min is None


@pytest.mark.parametrize("corpus_name", SHIPPED_CORPORA)
def test_garbled_all_fields_never_auto_merges(tmp_path, corpus_name):
    """No-overlap negative canary: when ALL ESS fields are garbled,
    the engine must land at NO_MATCH (or at worst T4 via context-field
    overlap). Auto-merge is a hard failure."""
    corpus = load_corpus(corpus_name)
    with CanonicalRegistry(tmp_path / f"{corpus_name}.db", corpus.schema) as registry:
        report = score(corpus, registry)
    garbled = [p for p in report.pairs if p.family == "garbled_all_fields"]
    assert garbled, f"[{corpus_name}] no garbled_all_fields mutations generated"
    auto_merge = {"t1_exact", "t2_strong"}
    for p in garbled:
        assert p.ess_tier not in auto_merge, (
            f"[{corpus_name}] garbled_all_fields auto-merged at {p.ess_tier}: "
            f"{p.canonical} -> {p.mutated}"
        )


def test_dob_typo_produces_valid_date():
    """Date arithmetic in `dob_typo` (people corpus) handles a normal
    ISO date and produces a parseable +1-day result."""
    import importlib.util
    from pathlib import Path
    import datetime

    repo = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "people_mutations_for_test",
        repo / "benchmarks/eval/ess_accuracy/data/people/mutations.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    record = {"first_name": "James", "last_name": "Smith",
              "dob": "1982-04-14", "gender": "M"}
    muts = mod._gen_dob_typo(record, ess_fields=["last_name", "first_name", "dob"])
    assert len(muts) == 1
    new_dob = muts[0].mutated["dob"]
    # Should parse + be exactly 1 day later
    assert datetime.date.fromisoformat(new_dob) == \
        datetime.date.fromisoformat("1982-04-14") + datetime.timedelta(days=1)
    assert muts[0].same_entity is True
    # Other fields untouched
    assert muts[0].mutated["last_name"] == "Smith"
    assert muts[0].mutated["first_name"] == "James"


def test_dob_typo_skips_malformed_date():
    """Date arithmetic in `dob_typo` rejects malformed input cleanly
    rather than crashing."""
    import importlib.util
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "people_mutations_for_test_bad_date",
        repo / "benchmarks/eval/ess_accuracy/data/people/mutations.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    for bad in ["", "not-a-date", "1982-13-01", "1982/04/14"]:
        record = {"first_name": "X", "last_name": "Y", "dob": bad}
        muts = mod._gen_dob_typo(record, ess_fields=["last_name", "first_name", "dob"])
        assert muts == [], f"dob_typo should skip malformed {bad!r}"


def test_realistic_collision_family_produces_pairs_per_shipped_corpus(tmp_path):
    """Silent-degradation guard: each shipped corpus that ships a
    `realistic_collision` family must produce at least one mutation.

    If someone edits entities.json and the collision-pair dict keys go
    stale, this catches it at test time rather than letting the
    campaign quietly report 'zero false merges' on fewer hostile
    pairs than the marketing copy implies.
    """
    for corpus_name in ["people", "inmate_clean", "publications"]:
        corpus = load_corpus(corpus_name)
        # Only count if the corpus has registered this family at all
        family_names = {f.name for f in corpus.families}
        if "realistic_collision" not in family_names:
            continue
        with CanonicalRegistry(
            tmp_path / f"{corpus_name}.db", corpus.schema
        ) as registry:
            report = score(corpus, registry)
        collision_pairs = [p for p in report.pairs if p.family == "realistic_collision"]
        assert collision_pairs, (
            f"[{corpus_name}] realistic_collision family registered but "
            f"produced ZERO mutations across all entities. The "
            f"hand-curated collision-pair dict has gone stale — likely "
            f"an entity was edited in entities.json without updating "
            f"the collision keys in mutations.py."
        )
        # All collision pairs must be labeled different-entity
        for p in collision_pairs:
            assert p.same_entity is False, (
                f"[{corpus_name}] realistic_collision generated a "
                f"same_entity=True pair — wrong label"
            )


@pytest.mark.parametrize("corpus_name", ["people", "inmate_clean", "publications"])
def test_realistic_collision_never_auto_merges(tmp_path, corpus_name):
    """The killer false-merge test. Hand-picked different-entity pairs
    sharing 2/3 ESS fields MUST NOT auto-merge. If this ever fails, the
    `precision = 1.000` headline marketing claim is broken."""
    corpus = load_corpus(corpus_name)
    family_names = {f.name for f in corpus.families}
    if "realistic_collision" not in family_names:
        pytest.skip(f"{corpus_name} doesn't ship a realistic_collision family")
    with CanonicalRegistry(tmp_path / f"{corpus_name}.db", corpus.schema) as registry:
        report = score(corpus, registry)
    collisions = [p for p in report.pairs if p.family == "realistic_collision"]
    auto_merge = {"t1_exact", "t2_strong"}
    for p in collisions:
        assert p.ess_tier not in auto_merge, (
            f"[{corpus_name}] realistic_collision auto-merged at "
            f"{p.ess_tier}: {p.canonical} -> {p.mutated}. "
            f"Precision claim broken."
        )


def test_silent_degradation_warning_fires_on_empty_family(tmp_path):
    """The score() guard should warn when a registered family produces
    zero mutations across all entities (catches stale collision-dict
    keys after entities.json edits)."""
    import warnings

    # Build a minimal corpus with an extra family that never matches
    (tmp_path / "schema.json").write_text(json.dumps({
        "name": "mini",
        "ess_fields": ["label"],
        "fuzzy_fields": {"label": 0.85},
    }), encoding="utf-8")
    (tmp_path / "entities.json").write_text(json.dumps([
        {"label": "Alpha"}, {"label": "Beta"},
    ]), encoding="utf-8")
    (tmp_path / "mutations.py").write_text(
        "from benchmarks.eval.ess_accuracy.mutations import MutationFamily\n"
        "def _gen_never(record, ess_fields):\n"
        "    return []  # never produces anything\n"
        "FAMILIES = [MutationFamily('always_empty', _gen_never, '')]\n",
        encoding="utf-8",
    )
    corpus = load_corpus(str(tmp_path), allow_untrusted_mutations=True)
    with CanonicalRegistry(tmp_path / "reg.db", corpus.schema) as registry:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            score(corpus, registry)
    assert any("always_empty" in str(w.message) for w in caught), (
        "score() should warn when a registered family produces 0 mutations"
    )


def test_report_markdown_renders():
    """Smoke: markdown output is non-empty + contains headline keywords."""
    from benchmarks.eval.ess_accuracy.metrics import AccuracyReport

    report = AccuracyReport(
        corpus_name="test",
        schema_name="test",
        n_entities=10,
        n_pairs=100,
        n_same=80,
        n_different=20,
        recall_t1=0.5,
        recall_t1_t2=0.6,
        recall_any_tier=0.95,
        false_merge_rate=0.0,
        jaccard_match_rate=0.7,
        jaccard_false_merge_rate=0.2,
        ess_minus_jaccard=-0.1,
        jaccard_fields=["name"],
        per_tier_calibration={
            "t1_exact": {"n": 50, "stated_confidence": 0.95, "actual_precision": 1.0},
        },
        per_family={
            "case": {
                "n": 50, "n_same": 50, "n_different": 0,
                "recall_t1_t2": 1.0, "recall_any_tier": 1.0,
                "false_merge_rate": 0.0, "tier_distribution": {"t1_exact": 50},
            },
        },
        spiritwriter_version="0.7.2",
    )
    md = render_markdown(report)
    assert "ESS Accuracy Report" in md
    assert "Recall@any-tier" in md
    assert "False-merge rate" in md
    assert "Per-tier calibration" in md
    assert "Per-family breakdown" in md


# ── Custom corpus integration ───────────────────────────────────────


def test_custom_corpus_with_only_universal_families(tmp_path):
    """No mutations.py file means universal families only."""
    (tmp_path / "schema.json").write_text(json.dumps({
        "name": "mini",
        "ess_fields": ["label"],
        "fuzzy_fields": {"label": 0.80},
    }), encoding="utf-8")
    (tmp_path / "entities.json").write_text(json.dumps([
        {"label": "Alphabet"},
        {"label": "Wikipedia"},
        {"label": "Galileo"},
    ]), encoding="utf-8")

    corpus = load_corpus(str(tmp_path))
    assert {f.name for f in corpus.families} == {f.name for f in UNIVERSAL_FAMILIES}

    with CanonicalRegistry(tmp_path / "reg.db", corpus.schema) as registry:
        report = score(corpus, registry)
    assert report.n_pairs > 0


def _write_custom_corpus_with_mutations(tmp_path):
    """Helper: build a corpus dir with schema, entities, and a mutations.py."""
    (tmp_path / "schema.json").write_text(json.dumps({
        "name": "mini",
        "ess_fields": ["label"],
        "fuzzy_fields": {"label": 0.80},
    }), encoding="utf-8")
    (tmp_path / "entities.json").write_text(json.dumps([
        {"label": "Alphabet"},
    ]), encoding="utf-8")
    (tmp_path / "mutations.py").write_text(
        "from benchmarks.eval.ess_accuracy.mutations import Mutation, MutationFamily\n"
        "def _gen_reverse(record, ess_fields):\n"
        "    out = []\n"
        "    for f in ess_fields:\n"
        "        v = record.get(f)\n"
        "        if isinstance(v, str):\n"
        "            new = dict(record); new[f] = v[::-1]\n"
        "            out.append(Mutation(\n"
        "                family='reverse', mutated=new, canonical=record,\n"
        "                same_entity=True, expected_tier_min='t3_fuzzy',\n"
        "                notes='reversed'))\n"
        "    return out\n"
        "FAMILIES = [MutationFamily('reverse', _gen_reverse, 'Reverse the string')]\n",
        encoding="utf-8",
    )


def test_custom_corpus_with_domain_mutations_requires_opt_in(tmp_path):
    """mutations.py from outside the shipped data/ tree is refused by default."""
    _write_custom_corpus_with_mutations(tmp_path)

    with pytest.raises(PermissionError, match="untrusted location"):
        load_corpus(str(tmp_path))


def test_custom_corpus_with_domain_mutations_loads_when_opted_in(tmp_path):
    """With explicit allow_untrusted_mutations=True the mutations.py loads."""
    _write_custom_corpus_with_mutations(tmp_path)

    corpus = load_corpus(str(tmp_path), allow_untrusted_mutations=True)
    family_names = {f.name for f in corpus.families}
    assert "reverse" in family_names
    assert "case" in family_names  # universal still present


def test_shipped_corpus_loads_mutations_without_opt_in():
    """Corpora under the shipped data/ tree are trusted by default."""
    corpus = load_corpus("people")  # ships with mutations.py
    family_names = {f.name for f in corpus.families}
    assert "surname_duplication" in family_names  # people-specific
    assert "case" in family_names  # universal
