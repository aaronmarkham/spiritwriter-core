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


def test_false_merge_rate_meets_target(tmp_path):
    """≤ 5% false-merge target from CMC spec — hard CMC-Lite invariant."""
    corpus = load_corpus("people")
    with CanonicalRegistry(tmp_path / "reg.db", corpus.schema) as registry:
        report = score(corpus, registry)
    assert report.false_merge_rate <= 0.05, (
        f"False-merge rate {report.false_merge_rate:.3f} exceeded target 0.05"
    )


def test_auto_merge_precision_is_perfect(tmp_path):
    """Among T1+T2 auto-merges, every pair must actually be same-entity.

    This is the meaningful CMC-Lite correctness invariant: the engine is
    allowed to be conservative (refuse to auto-merge), but it must NEVER
    auto-merge two entities that aren't actually the same.
    """
    from spiritwriter.fabric.canonicalize import ResolutionTier
    auto_merge = {ResolutionTier.T1_EXACT.value, ResolutionTier.T2_STRONG.value}

    corpus = load_corpus("people")
    with CanonicalRegistry(tmp_path / "reg.db", corpus.schema) as registry:
        report = score(corpus, registry)
    tp = sum(1 for p in report.pairs if p.same_entity and p.ess_tier in auto_merge)
    fp = sum(1 for p in report.pairs if not p.same_entity and p.ess_tier in auto_merge)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    assert precision >= 1.0, (
        f"Auto-merge precision dropped to {precision:.3f} (TP={tp}, FP={fp}); "
        f"any FP means CMC-Lite incorrectly merged entities that should "
        f"have stayed separate."
    )


def test_case_mutations_resolve_to_t1(tmp_path):
    """Concrete invariant: case variation MUST resolve at T1_EXACT."""
    corpus = load_corpus("people")
    with CanonicalRegistry(tmp_path / "reg.db", corpus.schema) as registry:
        report = score(corpus, registry)
    case_pairs = [p for p in report.pairs if p.family == "case"]
    assert case_pairs
    for p in case_pairs:
        assert p.ess_tier == ResolutionTier.T1_EXACT.value, (
            f"Case mutation landed at {p.ess_tier}, expected t1_exact: "
            f"{p.canonical} -> {p.mutated}"
        )


def test_negative_control_never_auto_merges(tmp_path):
    """False-merge canary: garbled ESS fields MUST NOT auto-merge."""
    corpus = load_corpus("people")
    with CanonicalRegistry(tmp_path / "reg.db", corpus.schema) as registry:
        report = score(corpus, registry)
    negs = [p for p in report.pairs if p.family == "negative_control"]
    assert negs
    auto_merge = {"t1_exact", "t2_strong"}
    for p in negs:
        assert p.ess_tier not in auto_merge, (
            f"Negative control auto-merged at {p.ess_tier}: {p.canonical} -> {p.mutated}"
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
