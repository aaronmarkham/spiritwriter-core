"""Focused tests for the provider-neutral shingled extraction primitive."""

from __future__ import annotations

import json
from dataclasses import FrozenInstanceError

import pytest

from spiritwriter.ingest.shingled import (
    CandidateValue,
    ConsensusStatus,
    ExtractionOutcome,
    ExtractionRecipe,
    ExtractionResponse,
    ExtractionRun,
    RunStatus,
    plan_chunks,
    run_shingled_extraction,
)


def _recipe(**overrides):
    values = {
        "extractor_id": "tests:scripted",
        "target_chars": 10,
        "overlap_chars": 3,
        "passes": 2,
        "consensus_threshold": 2,
        "extractor_version": "1",
    }
    values.update(overrides)
    return ExtractionRecipe.create(**values)


def test_chunk_plan_is_deterministic_and_carries_exact_source_offsets():
    text = "abcdefghijklmnopqrstuvwxyz"

    first = plan_chunks(text, source_ref="doc:a", target_chars=10, overlap_chars=3)
    second = plan_chunks(text, source_ref="doc:a", target_chars=10, overlap_chars=3)

    assert first == second
    assert [(c.start_offset, c.end_offset) for c in first] == [
        (0, 10),
        (7, 17),
        (14, 24),
        (21, 26),
    ]
    assert all(c.text == text[c.start_offset:c.end_offset] for c in first)
    assert all(
        left.end_offset - right.start_offset == 3
        for left, right in zip(first, first[1:])
    )
    assert len({c.chunk_id for c in first}) == len(first)


def test_chunk_plan_handles_unicode_empty_input_and_invalid_windows():
    chunks = plan_chunks("a🙂bc", source_ref="doc:unicode", target_chars=3, overlap_chars=1)
    assert [(c.start_offset, c.end_offset, c.text) for c in chunks] == [
        (0, 3, "a🙂b"),
        (2, 4, "bc"),
    ]
    assert plan_chunks("", source_ref="doc:empty") == ()

    with pytest.raises(ValueError, match="smaller"):
        plan_chunks("abc", source_ref="doc:a", target_chars=3, overlap_chars=3)
    with pytest.raises(ValueError, match="target_chars"):
        plan_chunks("abc", source_ref="doc:a", target_chars=0)


def test_recipe_is_immutable_and_config_has_canonical_identity():
    a = ExtractionRecipe.create(
        extractor_id="parser:x",
        extractor_config={"temperature": 0, "schema": {"b": 2, "a": 1}},
    )
    b = ExtractionRecipe.create(
        extractor_id="parser:x",
        extractor_config={"schema": {"a": 1, "b": 2}, "temperature": 0},
    )
    assert a.recipe_id == b.recipe_id
    assert a.to_dict() == b.to_dict()

    detached = a.extractor_config
    detached["temperature"] = 1
    assert a.extractor_config["temperature"] == 0
    with pytest.raises(FrozenInstanceError):
        a.passes = 3


def test_callback_receives_every_chunk_and_pass_with_provenance():
    seen = []

    def extractor(request):
        seen.append(request)
        return ExtractionResponse.create(
            candidates=[
                CandidateValue.create(
                    {"text": request.chunk.text},
                    consensus_key=f"chunk:{request.chunk.index}",
                )
            ]
        )

    recipe = _recipe(passes=3, consensus_threshold=2)
    run = run_shingled_extraction(
        source_ref="doc:a", text="abcdefghijklmnop", recipe=recipe, extractor=extractor
    )

    assert len(run.chunks) == 2
    assert len(seen) == len(run.chunks) * recipe.passes
    assert [(r.pass_index, r.chunk.index) for r in seen] == [
        (1, 0),
        (1, 1),
        (2, 0),
        (2, 1),
        (3, 0),
        (3, 1),
    ]
    assert run.status == RunStatus.COMPLETE
    assert not run.gaps
    assert all(record.fully_covered for record in run.coverage)
    assert all(d.status == ConsensusStatus.ACCEPTED for d in run.dispositions)


def test_consensus_counts_distinct_passes_and_never_deletes_candidates():
    def extractor(request):
        candidates = [
            CandidateValue.create(
                {"sighting": f"common-{request.pass_index}-a"},
                consensus_key="fact:common",
            ),
            CandidateValue.create(
                {"sighting": f"common-{request.pass_index}-b"},
                consensus_key="fact:common",
            ),
        ]
        if request.pass_index == 1:
            candidates.extend(
                [
                    CandidateValue.create(
                        {"sighting": "orphan"}, consensus_key="fact:orphan"
                    ),
                    CandidateValue.create({"sighting": "unkeyed"}),
                ]
            )
        return ExtractionResponse.create(candidates=candidates)

    recipe = _recipe(target_chars=100, overlap_chars=10, passes=3, consensus_threshold=2)
    run = run_shingled_extraction(
        source_ref="doc:a", text="one short source", recipe=recipe, extractor=extractor
    )

    assert len(run.candidates) == 8
    by_key = {d.consensus_key: d for d in run.dispositions if d.consensus_key}
    assert by_key["fact:common"].votes == 3
    assert len(by_key["fact:common"].candidate_ids) == 6
    assert by_key["fact:common"].status == ConsensusStatus.ACCEPTED
    assert by_key["fact:orphan"].votes == 1
    assert by_key["fact:orphan"].status == ConsensusStatus.REJECTED
    assert any(d.status == ConsensusStatus.INELIGIBLE for d in run.dispositions)
    assert {c.candidate_id for c in run.candidates} == {
        candidate_id
        for disposition in run.dispositions
        for candidate_id in disposition.candidate_ids
    }


def test_skipped_and_error_candidates_are_retained_but_do_not_vote():
    def extractor(request):
        outcome = (
            ExtractionOutcome.SKIPPED
            if request.pass_index == 1
            else ExtractionOutcome.ERROR
        )
        return ExtractionResponse.create(
            outcome=outcome,
            candidates=[
                CandidateValue.create(
                    {"reported": outcome.value}, consensus_key="fact:not-observed"
                )
            ],
            message="non-voting outcome",
        )

    run = run_shingled_extraction(
        source_ref="doc:a",
        text="source",
        recipe=_recipe(
            target_chars=100,
            overlap_chars=10,
            passes=2,
            consensus_threshold=1,
        ),
        extractor=extractor,
    )

    assert len(run.candidates) == 2
    assert {candidate.outcome for candidate in run.candidates} == {
        ExtractionOutcome.SKIPPED,
        ExtractionOutcome.ERROR,
    }
    disposition = run.dispositions[0]
    assert set(disposition.candidate_ids) == {
        candidate.candidate_id for candidate in run.candidates
    }
    assert disposition.votes == 0
    assert disposition.observed_passes == ()
    assert disposition.status == ConsensusStatus.INELIGIBLE


def test_invalid_callback_fields_become_error_outcomes_instead_of_breaking_run():
    def extractor(request):
        if request.pass_index == 1:
            return ExtractionResponse(raw_output=b"not text")
        return ExtractionResponse.create(
            candidates=[CandidateValue.create({"fact": 1}, consensus_key=7)]
        )

    run = run_shingled_extraction(
        source_ref="doc:a",
        text="source",
        recipe=_recipe(target_chars=100, overlap_chars=10),
        extractor=extractor,
    )

    assert [attempt.outcome for attempt in run.attempts] == [
        ExtractionOutcome.ERROR,
        ExtractionOutcome.ERROR,
    ]
    assert all("TypeError" in attempt.message for attempt in run.attempts)
    assert run.status == RunStatus.FAILED


def test_incomplete_outcomes_raw_output_candidates_coverage_and_gaps_are_retained():
    def extractor(request):
        if request.pass_index == 1:
            return ExtractionResponse.create(
                outcome=ExtractionOutcome.MALFORMED,
                raw_output='[{"broken":',
                message="invalid JSON at byte 11",
                candidates=[
                    CandidateValue.create(
                        {"salvaged": 1}, consensus_key="fact:salvaged"
                    )
                ],
            )
        if request.pass_index == 2:
            return ExtractionResponse.create(
                outcome=ExtractionOutcome.TRUNCATED,
                raw_output='[{"salvaged": 1}]',
                message="provider output limit reached",
                candidates=[
                    CandidateValue.create(
                        {"salvaged": 1}, consensus_key="fact:salvaged"
                    )
                ],
            )
        return ExtractionResponse.create(
            outcome=ExtractionOutcome.SKIPPED,
            message="caller policy excluded this pass",
        )

    recipe = _recipe(target_chars=100, overlap_chars=10, passes=3, consensus_threshold=2)
    run = run_shingled_extraction(
        source_ref="doc:a", text="source", recipe=recipe, extractor=extractor
    )

    assert [a.outcome for a in run.attempts] == [
        ExtractionOutcome.MALFORMED,
        ExtractionOutcome.TRUNCATED,
        ExtractionOutcome.SKIPPED,
    ]
    assert run.attempts[0].raw_output == '[{"broken":'
    assert len(run.candidates) == 2
    assert len(run.gaps) == 3
    assert [g.outcome for g in run.gaps] == [
        ExtractionOutcome.MALFORMED,
        ExtractionOutcome.TRUNCATED,
        ExtractionOutcome.SKIPPED,
    ]
    assert run.coverage[0].attempted_passes == (1, 2, 3)
    assert run.coverage[0].complete_passes == ()
    assert not run.coverage[0].fully_covered
    assert run.dispositions[0].status == ConsensusStatus.ACCEPTED
    assert run.status == RunStatus.PARTIAL


def test_callback_exception_and_wrong_return_become_auditable_gaps():
    def extractor(request):
        if request.pass_index == 1:
            raise RuntimeError("provider unavailable")
        return None

    run = run_shingled_extraction(
        source_ref="doc:a",
        text="source",
        recipe=_recipe(target_chars=100, overlap_chars=10),
        extractor=extractor,
    )

    assert [a.outcome for a in run.attempts] == [
        ExtractionOutcome.ERROR,
        ExtractionOutcome.MALFORMED,
    ]
    assert "RuntimeError: provider unavailable" in run.attempts[0].message
    assert "expected ExtractionResponse" in run.attempts[1].message
    assert len(run.gaps) == 2
    assert run.status == RunStatus.FAILED


def test_empty_source_has_explicit_gap_without_calling_extractor():
    called = False

    def extractor(request):
        nonlocal called
        called = True
        return ExtractionResponse()

    run = run_shingled_extraction(
        source_ref="doc:empty", text="", recipe=_recipe(), extractor=extractor
    )

    assert not called
    assert run.status == RunStatus.SKIPPED
    assert run.chunks == ()
    assert run.attempts == ()
    assert len(run.gaps) == 1
    assert run.gaps[0].outcome == ExtractionOutcome.SKIPPED
    assert run.gaps[0].start_offset == run.gaps[0].end_offset == 0


def test_duplicate_sightings_are_preserved_with_source_specific_ids():
    def extractor(request):
        return ExtractionResponse.create(
            candidates=[CandidateValue.create({"fact": "same"}, consensus_key="same")]
        )

    recipe = _recipe(target_chars=100, overlap_chars=10, passes=1, consensus_threshold=1)
    first = run_shingled_extraction(
        source_ref="doc:first", text="identical", recipe=recipe, extractor=extractor
    )
    second = run_shingled_extraction(
        source_ref="doc:second", text="identical", recipe=recipe, extractor=extractor
    )

    assert first.candidates[0].payload == second.candidates[0].payload
    assert first.candidates[0].candidate_id != second.candidates[0].candidate_id
    assert first.source_id != second.source_id


def test_run_serialization_and_ids_are_deterministic_and_verified():
    def extractor(request):
        return ExtractionResponse.create(
            candidates=[
                CandidateValue.create(
                    {"key": "value", "pass": request.pass_index},
                    consensus_key="key:value",
                    metadata={"z": 2, "a": 1},
                )
            ],
            metadata={"provider": "test"},
        )

    recipe = _recipe(target_chars=100, overlap_chars=10)
    first = run_shingled_extraction(
        source_ref="doc:a", text="source", recipe=recipe, extractor=extractor
    )
    second = run_shingled_extraction(
        source_ref="doc:a", text="source", recipe=recipe, extractor=extractor
    )

    assert first.run_id == second.run_id
    assert first.to_json() == second.to_json()
    assert first == ExtractionRun.from_json(first.to_json())
    assert first.to_json() == json.dumps(
        first.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )

    tampered = first.to_dict()
    tampered["source_length"] += 1
    with pytest.raises(ValueError):
        ExtractionRun.from_dict(tampered)
