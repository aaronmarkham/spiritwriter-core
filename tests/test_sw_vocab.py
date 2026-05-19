"""Tests for spiritwriter.sw_vocab — terminology canonicalization.

These tests use the actual drift Claude Chat produced (2026-05-17) as
the validation corpus. If they pass, the registry would have caught the
hallucinations before publication.
"""

from __future__ import annotations

import json

import pytest

from spiritwriter.sw_vocab import (
    canonical_term_list,
    extract_candidates,
    load_registry,
    validate_candidate,
    validate_doc,
    validate_text,
)
from spiritwriter.sw_vocab.seed import bundled_terms, seed


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
def seeded_registry(tmp_path):
    db = tmp_path / "vocab.db"
    return seed(db_path=db)


# ── Basic registry behavior ─────────────────────────────────────────


def test_seed_populates_registry(tmp_path):
    db = tmp_path / "vocab.db"
    registry = seed(db_path=db)
    terms = list(registry.entities())
    assert len(terms) == len(bundled_terms())


def test_seed_is_idempotent(tmp_path):
    db = tmp_path / "vocab.db"
    with seed(db_path=db) as r1:
        count_first = len(list(r1.entities()))
    with seed(db_path=db) as r2:
        count_second = len(list(r2.entities()))
    assert count_first == count_second


def test_seed_raises_stale_metadata_when_aliases_change(tmp_path):
    """The bug PR #54 review caught: editing aliases on an existing term
    silently no-ops on plain re-seed (T1_EXACT skips). Now we detect it
    and raise so the user knows to use --force."""
    from spiritwriter.sw_vocab.seed import StaleMetadataError, seed as do_seed

    db = tmp_path / "vocab.db"
    with do_seed(db_path=db):
        pass  # seed the baseline

    extra_edited = [{
        "term": "Entity Sense Signature",
        "category": "primitive",
        "definition": "Edited description",
        "abbreviation": "ESS",
        "defined_in": "spiritwriter/fabric/canonicalize.py",
        "aliases": ["BRAND NEW ALIAS"],  # change from the bundled aliases
    }]
    with pytest.raises(StaleMetadataError) as exc_info:
        do_seed(db_path=db, extra=extra_edited)
    assert "force=True" in str(exc_info.value) or "--force" in str(exc_info.value)


def test_seed_force_wipes_db_and_does_not_raise(tmp_path):
    """force=True skips the stale-metadata check and rebuilds from scratch.

    This proves the escape hatch works. The "edit aliases on an existing
    bundled term and have them picked up" workflow is documented as
    `--force` and is exercised by the bundled-edit flow at the JSON level
    (test-wise, see how _wipe_db deletes the DB before rebuild).
    """
    from spiritwriter.sw_vocab.seed import seed as do_seed

    db = tmp_path / "vocab.db"
    with do_seed(db_path=db):
        pass  # baseline seed

    # Edit the *test-only* term via extra. With force, this rebuilds the
    # DB cleanly without hitting the stale-metadata check.
    extra = [{
        "term": "Test New Canonical",
        "category": "primitive",
        "definition": "A test-only term to verify force flow.",
        "aliases": ["test new", "tnc"],
    }]
    with do_seed(db_path=db, extra=extra, force=True) as r:
        issue = validate_candidate("tnc", r)
    assert issue is not None
    assert issue["issue"] == "known_drift"
    assert issue["canonical"] == "Test New Canonical"


def test_seed_rejects_invented_term_without_prefix(tmp_path):
    """Convention enforcement at seed time, not just in tests."""
    from spiritwriter.sw_vocab.seed import seed as do_seed

    db = tmp_path / "vocab.db"
    extra_bad = [{
        "term": "Something Invented",  # missing INVENTED: prefix
        "category": "invented",
        "definition": "An invented thing without the right prefix",
        "aliases": [],
    }]
    with pytest.raises(ValueError, match="INVENTED:"):
        do_seed(db_path=db, extra=extra_bad)


def test_seed_rejects_deferred_term_without_prefix(tmp_path):
    """Same convention check for deferred terms."""
    from spiritwriter.sw_vocab.seed import seed as do_seed

    db = tmp_path / "vocab.db"
    extra_bad = [{
        "term": "Something Deferred",
        "category": "deferred",
        "definition": "Deferred without prefix",
        "aliases": [],
    }]
    with pytest.raises(ValueError, match="DEFERRED:"):
        do_seed(db_path=db, extra=extra_bad)


def test_canonical_term_list_groups_invented_first(seeded_registry):
    listing = canonical_term_list(seeded_registry)
    # Invented terms should appear before primitives in the sorted listing.
    invented_pos = listing.find("[invented")
    primitive_pos = listing.find("[primitive")
    assert invented_pos != -1
    assert primitive_pos != -1
    assert invented_pos < primitive_pos


# ── The actual Claude Chat drift ────────────────────────────────────


def test_catches_ess_drift(seeded_registry):
    """The headline hallucination — 'Entity Semantic Scoring' for ESS."""
    issue = validate_candidate("Entity Semantic Scoring", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "known_drift"
    assert issue["canonical"] == "Entity Sense Signature"


def test_accepts_canonical_ess(seeded_registry):
    """The canonical form should validate cleanly."""
    issue = validate_candidate("Entity Sense Signature", seeded_registry)
    assert issue is None


def test_catches_sw_cap_invented(seeded_registry):
    """SW-CAP doesn't exist — should be flagged as invented."""
    issue = validate_candidate("SW-CAP", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "invented_term"
    assert "Spiritwriter Substrate" in issue["note"] or "capability" in issue["note"]


def test_catches_capability_shard_invented(seeded_registry):
    issue = validate_candidate("capability shards", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "invented_term"


def test_catches_bootstrap_shard_invented(seeded_registry):
    issue = validate_candidate("bootstrap shards", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "invented_term"


def test_catches_dual_key_invented(seeded_registry):
    issue = validate_candidate("dual-key sealed-box", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "invented_term"


def test_catches_otel_invented(seeded_registry):
    issue = validate_candidate("OpenTelemetry span attributes", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "invented_term"


def test_catches_trust_epochs_deferred(seeded_registry):
    """trust epochs is in the spec but marked deferred — must not be claimed."""
    issue = validate_candidate("trust epochs", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "deferred_term"


def test_catches_revocation_deferred(seeded_registry):
    issue = validate_candidate("revocation", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "deferred_term"


# ── Casing / formatting variants ───────────────────────────────────


def test_case_insensitive_alias_match(seeded_registry):
    """Lowercased / weird-cased variants still resolve."""
    issue = validate_candidate("entity semantic scoring", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "known_drift"


def test_shingles_resolves_to_phalanx(seeded_registry):
    """Phalanx was renamed from shingles — old name should flag drift."""
    issue = validate_candidate("shingles", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "known_drift"
    assert issue["canonical"] == "Phalanx"


def test_unknown_term_flagged(seeded_registry):
    issue = validate_candidate("ZeitghostFoo", seeded_registry)
    assert issue is not None
    assert issue["issue"] == "unknown_term"


# ── Markdown extraction ─────────────────────────────────────────────


def test_extract_candidates_finds_bolded():
    text = "We use **Entity Sense Signature** to resolve entities."
    assert "Entity Sense Signature" in extract_candidates(text)


def test_extract_candidates_finds_inline_code():
    text = "Call `CanonicalRegistry` to start."
    assert "CanonicalRegistry" in extract_candidates(text)


def test_extract_candidates_deduplicates():
    text = "**Foo** and `Foo` and **Foo**."
    assert extract_candidates(text) == ["Foo"]


# ── Document validation ────────────────────────────────────────────


def test_validate_text_flags_drift_in_prose(seeded_registry):
    """The Claude Chat draft would have been caught here."""
    fake_chat_output = """
    # Spiritwriter Tracing

    The SW-CAP v0.1 spec defines capability shards as the bootstrap
    mechanism. Each shard uses **Entity Semantic Scoring** to resolve
    duplicates. Trust epochs handle key rotation. We integrate with
    OpenTelemetry span attributes for provenance.
    """
    issues = validate_text(fake_chat_output, seeded_registry)
    issue_types = {(i["issue"], i["term"].lower()) for i in issues}

    # Bolded canonical-form drift
    assert ("known_drift", "entity semantic scoring") in issue_types

    # Free-form invented / deferred terms caught by substring scan
    found_invented = any(
        i["issue"] == "invented_term" and "sw-cap" in i["term"].lower()
        for i in issues
    )
    found_deferred = any(
        i["issue"] == "deferred_term" and "trust epoch" in i["term"].lower()
        for i in issues
    )
    assert found_invented, f"SW-CAP should be flagged invented in: {issues}"
    assert found_deferred, f"trust epochs should be flagged deferred in: {issues}"


def test_validate_text_clean_passes(seeded_registry):
    """A doc using only canonical terms produces no issues."""
    good_text = """
    The **Entity Sense Signature** is computed by `CanonicalRegistry`.
    `MemoryShard` instances are independent of `capability` tokens.
    """
    issues = validate_text(good_text, seeded_registry)
    assert issues == []


def test_validate_doc_against_validation_writeup(seeded_registry, tmp_path):
    """The validation writeup in C:/tmp uses many invented terms inside
    quote-callouts. Validating it should flag the invented forms — the
    intent IS to catalogue the drift, but a tool that didn't catch them
    here wouldn't catch them anywhere."""
    doc_path = tmp_path / "writeup.md"
    doc_path.write_text(
        "Claude Chat invented `SW-CAP` and `capability shards` and "
        "claimed trust epochs were implemented.\n",
        encoding="utf-8",
    )
    issues = validate_doc(doc_path, seeded_registry)
    issue_types = {i["issue"] for i in issues}
    assert "invented_term" in issue_types
    assert "deferred_term" in issue_types


# ── Opt-out directives ──────────────────────────────────────────────


def test_opt_out_allow_invented_suppresses_invented_substring(seeded_registry):
    """A doc with allow-invented can quote SW-CAP in prose without flagging."""
    text = """<!-- vocab: allow-invented -->
    This SKILL doc explains that SW-CAP and capability shards are
    hallucinations to avoid.
    """
    issues = validate_text(text, seeded_registry)
    invented = [i for i in issues if i["issue"] == "invented_term"]
    assert invented == []


def test_opt_out_allow_deferred_suppresses_deferred_substring(seeded_registry):
    text = """<!-- vocab: allow-deferred -->
    Trust epochs and revocation sets are explicitly deferred future work.
    """
    issues = validate_text(text, seeded_registry)
    deferred = [i for i in issues if i["issue"] == "deferred_term"]
    assert deferred == []


def test_opt_out_allow_all_suppresses_both(seeded_registry):
    text = """<!-- vocab: allow-all -->
    Future work: SW-CAP, capability shards, trust epochs, revocation sets.
    """
    issues = validate_text(text, seeded_registry)
    assert [i for i in issues if i["issue"] == "invented_term"] == []
    assert [i for i in issues if i["issue"] == "deferred_term"] == []


def test_opt_out_disable_skips_everything(seeded_registry):
    text = """<!-- vocab: disable -->
    Anything goes: **Entity Semantic Scoring**, SW-CAP, trust epochs.
    """
    issues = validate_text(text, seeded_registry)
    assert issues == []


def test_opt_out_does_not_suppress_canonical_drift(seeded_registry):
    """allow-invented should still flag known_drift on canonical terms.
    The opt-out only excuses the OPTED-OUT CATEGORIES, not real drift."""
    text = """<!-- vocab: allow-invented -->
    SW-CAP is invented and that's fine to mention.
    But **Entity Semantic Scoring** is still wrong terminology.
    """
    issues = validate_text(text, seeded_registry)
    drift = [i for i in issues if i["issue"] == "known_drift"]
    assert len(drift) == 1
    assert drift[0]["canonical"] == "Entity Sense Signature"


def test_opt_out_suppresses_marked_deferred(seeded_registry):
    """A doc explaining deferred features may bold/code them
    (e.g. substrate-flavor.md backticks `shards.spiritwriter.ai`).
    The opt-out should cover marked mentions, not just prose."""
    text = """<!-- vocab: allow-deferred -->
    Future additions include `shards.spiritwriter.ai` and **trust epochs**.
    """
    issues = validate_text(text, seeded_registry)
    assert [i for i in issues if i["issue"] == "deferred_term"] == []


# ── Schema integrity ────────────────────────────────────────────────


def test_seed_data_has_no_duplicate_terms():
    """Every canonical term must be unique."""
    terms = bundled_terms()
    canonical_names = [t["term"] for t in terms]
    assert len(canonical_names) == len(set(canonical_names))


def test_seed_data_aliases_dont_collide_with_canonicals():
    """An alias for term A must not BE term B's canonical name —
    that would create resolution ambiguity."""
    terms = bundled_terms()
    canonicals = {t["term"].lower() for t in terms}
    for t in terms:
        for alias in t.get("aliases", []):
            assert alias.lower() not in canonicals or alias.lower() == t["term"].lower(), (
                f"Alias '{alias}' for term '{t['term']}' collides with another canonical."
            )


def test_seed_data_all_have_required_fields():
    terms = bundled_terms()
    for t in terms:
        assert "term" in t, f"Missing term in: {t}"
        assert "category" in t, f"Missing category in: {t}"
        assert "definition" in t, f"Missing definition in: {t}"


def test_no_case_redundant_aliases():
    """Aliases differing only in case are redundant — lookup is
    case-insensitive, so adding both bulks the JSON without affecting
    behavior. Catch them at load time so the seed file stays tidy."""
    terms = bundled_terms()
    for t in terms:
        seen: set[str] = set()
        for alias in t.get("aliases", []):
            lc = alias.lower()
            assert lc not in seen, (
                f"Term {t['term']!r} has case-redundant aliases including "
                f"{alias!r} — lookup is case-insensitive, drop one."
            )
            seen.add(lc)


def test_invented_entries_use_invented_prefix():
    """Convention: invented terms start with 'INVENTED:' for clarity in
    raw JSON browsing. The category field is the source of truth, but
    the prefix helps anyone reading the seed JSON without context."""
    terms = bundled_terms()
    for t in terms:
        if t["category"] == "invented":
            assert t["term"].startswith("INVENTED:"), (
                f"Invented term '{t['term']}' should start with INVENTED:"
            )
        if t["category"] == "deferred":
            assert t["term"].startswith("DEFERRED:"), (
                f"Deferred term '{t['term']}' should start with DEFERRED:"
            )
