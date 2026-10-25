"""Focused tests for the provider-neutral shingled extraction primitive."""

from __future__ import annotations

import hashlib
import json
from dataclasses import FrozenInstanceError

import pytest

from spiritwriter.ingest.shingled import (
    CandidateValue,
    ConsensusStatus,
    ExtractionCheckpoint,
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
    assert all(c.text == text[c.start_offset : c.end_offset] for c in first)
    assert all(
        left.end_offset - right.start_offset == 3
        for left, right in zip(first, first[1:])
    )
    assert len({c.chunk_id for c in first}) == len(first)


def test_chunk_plan_handles_unicode_empty_input_and_invalid_windows():
    chunks = plan_chunks(
        "a🙂bc", source_ref="doc:unicode", target_chars=3, overlap_chars=1
    )
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

    recipe = _recipe(
        target_chars=100, overlap_chars=10, passes=3, consensus_threshold=2
    )
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

    recipe = _recipe(
        target_chars=100, overlap_chars=10, passes=3, consensus_threshold=2
    )
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

    recipe = _recipe(
        target_chars=100, overlap_chars=10, passes=1, consensus_threshold=1
    )
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


def test_ineligible_candidates_do_not_imply_partial_or_receive_unkeyed_votes():
    def mixed_extractor(request):
        outcome = (
            ExtractionOutcome.SKIPPED
            if request.pass_index == 1
            else ExtractionOutcome.ERROR
        )
        return ExtractionResponse.create(
            outcome=outcome,
            candidates=[CandidateValue.create({"outcome": outcome.value})],
        )

    mixed = run_shingled_extraction(
        source_ref="doc:mixed",
        text="source",
        recipe=_recipe(target_chars=100, overlap_chars=10),
        extractor=mixed_extractor,
    )
    assert mixed.status == RunStatus.FAILED
    assert len(mixed.candidates) == 2
    assert all(disposition.votes == 0 for disposition in mixed.dispositions)
    assert all(not disposition.observed_passes for disposition in mixed.dispositions)

    skipped = run_shingled_extraction(
        source_ref="doc:skipped",
        text="source",
        recipe=_recipe(
            target_chars=100,
            overlap_chars=10,
            passes=1,
            consensus_threshold=1,
        ),
        extractor=lambda request: ExtractionResponse.create(
            outcome=ExtractionOutcome.SKIPPED,
            candidates=[CandidateValue.create({"retained": True})],
        ),
    )
    assert skipped.status == RunStatus.SKIPPED
    assert skipped.dispositions[0].votes == 0


def test_partial_status_requires_an_eligible_observation():
    def run_for(outcome, with_candidate=False):
        return run_shingled_extraction(
            source_ref=f"doc:{outcome.value}:{with_candidate}",
            text="source",
            recipe=_recipe(
                target_chars=100,
                overlap_chars=10,
                passes=1,
                consensus_threshold=1,
            ),
            extractor=lambda request: ExtractionResponse.create(
                outcome=outcome,
                candidates=(
                    [CandidateValue.create({"salvaged": True})]
                    if with_candidate
                    else []
                ),
            ),
        )

    assert run_for(ExtractionOutcome.MALFORMED).status == RunStatus.FAILED
    assert run_for(ExtractionOutcome.MALFORMED, True).status == RunStatus.PARTIAL
    assert run_for(ExtractionOutcome.TRUNCATED).status == RunStatus.PARTIAL
    assert run_for(ExtractionOutcome.ERROR, True).status == RunStatus.FAILED


@pytest.mark.parametrize(
    "field",
    [
        "target_chars",
        "overlap_chars",
        "passes",
        "consensus_threshold",
        "max_chunks",
        "max_attempts",
        "max_raw_output_bytes",
        "max_total_raw_output_bytes",
    ],
)
@pytest.mark.parametrize("invalid", [True, False, 1.0, "1"])
def test_recipe_integer_fields_reject_booleans_and_non_integers(field, invalid):
    values = {
        "target_chars": 100,
        "overlap_chars": 10,
        "passes": 2,
        "consensus_threshold": 1,
        "max_chunks": 10,
        "max_attempts": 20,
        "max_raw_output_bytes": 10,
        "max_total_raw_output_bytes": 20,
    }
    values[field] = invalid
    with pytest.raises((TypeError, ValueError), match="integer"):
        ExtractionRecipe.create(extractor_id="test", **values)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"target_chars": True},
        {"overlap_chars": False},
        {"max_chunks": 1.0},
    ],
)
def test_chunk_planner_rejects_non_integer_parameters(kwargs):
    with pytest.raises(TypeError, match="integer"):
        plan_chunks("source", source_ref="doc:a", **kwargs)


def test_planning_bounds_stride_chunks_and_attempts_before_callbacks():
    with pytest.raises(ValueError, match="at least 10%"):
        plan_chunks(
            "abcdefghijkl", source_ref="doc:a", target_chars=11, overlap_chars=10
        )

    assert (
        len(
            plan_chunks(
                "abcdefghijklmnopq",
                source_ref="doc:a",
                target_chars=10,
                overlap_chars=2,
                max_chunks=2,
            )
        )
        == 2
    )
    with pytest.raises(ValueError, match="max_chunks"):
        plan_chunks(
            "abcdefghijklmnopqrs",
            source_ref="doc:a",
            target_chars=10,
            overlap_chars=2,
            max_chunks=1,
        )

    called = False

    def extractor(request):
        nonlocal called
        called = True
        return ExtractionResponse()

    with pytest.raises(ValueError, match="max_attempts"):
        run_shingled_extraction(
            source_ref="doc:a",
            text="abcdefghijklmnop",
            recipe=_recipe(
                passes=3,
                consensus_threshold=2,
                max_chunks=2,
                max_attempts=5,
            ),
            extractor=extractor,
        )
    assert not called


def test_raw_output_is_bounded_per_attempt_and_across_run():
    outputs = iter(["ééé", "abcd"])
    run = run_shingled_extraction(
        source_ref="doc:raw",
        text="source",
        recipe=_recipe(
            target_chars=100,
            overlap_chars=10,
            passes=2,
            max_raw_output_bytes=5,
            max_total_raw_output_bytes=7,
        ),
        extractor=lambda request: ExtractionResponse.create(raw_output=next(outputs)),
    )

    first, second = run.attempts
    assert first.raw_output == "éé"
    assert first.raw_output_bytes == 6
    assert first.raw_output_hash == hashlib.sha256("ééé".encode()).hexdigest()
    assert first.raw_output_truncated
    assert second.raw_output == "abc"
    assert second.raw_output_bytes == 4
    assert second.raw_output_truncated
    assert sum(len(attempt.raw_output.encode()) for attempt in run.attempts) <= 7
    assert ExtractionRun.from_json(run.to_json()) == run


def test_checkpoint_sink_receives_ordered_complete_attempt_records():
    checkpoints = []
    duplicate = CandidateValue.create({"fact": "same"}, consensus_key="same")

    run = run_shingled_extraction(
        source_ref="doc:checkpoints",
        text="abcdefghijklmnop",
        recipe=_recipe(passes=1, consensus_threshold=1),
        extractor=lambda request: ExtractionResponse.create(
            outcome=(
                ExtractionOutcome.TRUNCATED
                if request.chunk.index == 1
                else ExtractionOutcome.COMPLETE
            ),
            candidates=[duplicate, duplicate],
            raw_output="raw",
        ),
        checkpoint_sink=checkpoints.append,
    )

    assert [item.sequence for item in checkpoints] == [1, 2]
    assert all(item.total_attempts == 2 for item in checkpoints)
    assert [item.attempt.attempt_id for item in checkpoints] == [
        attempt.attempt_id for attempt in run.attempts
    ]
    assert checkpoints[0].gap is None
    assert checkpoints[1].gap == run.gaps[0]
    assert [candidate.ordinal for candidate in checkpoints[0].candidates] == [0, 1]
    assert len({candidate.candidate_id for candidate in checkpoints[0].candidates}) == 2
    assert checkpoints[0].attempt.candidate_ids == tuple(
        candidate.candidate_id for candidate in checkpoints[0].candidates
    )
    assert checkpoints[0].checkpoint_id == checkpoints[0].checkpoint_id
    assert ExtractionCheckpoint.from_json(checkpoints[0].to_json()) == checkpoints[0]
    assert ExtractionRun.from_json(run.to_json()) == run


def test_checkpoint_sink_failures_propagate_without_provider_reclassification():
    def failing_sink(checkpoint):
        raise OSError("checkpoint unavailable")

    with pytest.raises(OSError, match="checkpoint unavailable"):
        run_shingled_extraction(
            source_ref="doc:a",
            text="source",
            recipe=_recipe(target_chars=100, overlap_chars=10),
            extractor=lambda request: ExtractionResponse(),
            checkpoint_sink=failing_sink,
        )


@pytest.mark.parametrize(
    "raw",
    [
        '{"a":1,"a":2}',
        '{"nested":{"a":1,"a":2}}',
        '{"value":NaN}',
        '{"value":Infinity}',
        '{"value":-Infinity}',
    ],
)
def test_json_text_fields_reject_duplicate_keys_and_non_finite_values(raw):
    with pytest.raises(ValueError, match="strict JSON"):
        CandidateValue(payload_json=raw)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: CandidateValue.create({"value": float("nan")}),
        lambda: CandidateValue.create({}, metadata={"value": float("inf")}),
        lambda: ExtractionRecipe.create(
            extractor_id="test", extractor_config={"value": float("-inf")}
        ),
        lambda: CandidateValue.create({1: "non-string key"}),
    ],
)
def test_programmatic_json_values_are_strict(factory):
    with pytest.raises((TypeError, ValueError)):
        factory()


def _strict_persistence_fixture():
    return run_shingled_extraction(
        source_ref="doc:persisted",
        text="source",
        recipe=_recipe(target_chars=100, overlap_chars=10),
        extractor=lambda request: ExtractionResponse.create(
            outcome=(
                ExtractionOutcome.MALFORMED
                if request.pass_index == 1
                else ExtractionOutcome.COMPLETE
            ),
            candidates=[
                CandidateValue.create(
                    {"pass": request.pass_index}, consensus_key="fact:key"
                )
            ],
            message="salvaged" if request.pass_index == 1 else None,
        ),
    )


@pytest.mark.parametrize(
    ("path", "field"),
    [
        ((), "run_id"),
        (("recipe",), "recipe_id"),
        (("chunks", 0), "chunk_id"),
        (("chunks", 0), "text_hash"),
        (("attempts", 0), "attempt_id"),
        (("candidates", 0), "candidate_id"),
        (("dispositions", 0), "disposition_id"),
        (("coverage", 0), "fully_covered"),
        (("gaps", 0), "gap_id"),
    ],
)
def test_persisted_deserialization_requires_integrity_fields(path, field):
    data = json.loads(_strict_persistence_fixture().to_json())
    target = data
    for component in path:
        target = target[component]
    del target[field]
    with pytest.raises(ValueError, match="missing required"):
        ExtractionRun.from_dict(data)


@pytest.mark.parametrize(
    "path",
    [
        (),
        ("recipe",),
        ("chunks", 0),
        ("attempts", 0),
        ("candidates", 0),
        ("dispositions", 0),
        ("coverage", 0),
        ("gaps", 0),
    ],
)
def test_persisted_deserialization_rejects_unknown_fields_at_every_level(path):
    data = json.loads(_strict_persistence_fixture().to_json())
    target = data
    for component in path:
        target = target[component]
    target["unexpected"] = True
    with pytest.raises(ValueError, match="unknown persisted"):
        ExtractionRun.from_dict(data)


def test_persisted_json_rejects_duplicate_keys_non_objects_and_bad_id_shape():
    run = _strict_persistence_fixture()
    duplicate = '{"run_id":"' + run.run_id + '",' + run.to_json()[1:]
    with pytest.raises(ValueError, match="duplicate JSON object key"):
        ExtractionRun.from_json(duplicate)
    with pytest.raises(ValueError, match="must contain an object"):
        ExtractionRun.from_json("[]")

    data = run.to_dict()
    data["run_id"] = "not-a-hash"
    with pytest.raises(ValueError, match="SHA-256"):
        ExtractionRun.from_dict(data)


def test_checkpoint_deserialization_is_strict_and_verifies_nested_ids():
    checkpoints = []
    run_shingled_extraction(
        source_ref="doc:checkpoint-load",
        text="source",
        recipe=_recipe(target_chars=100, overlap_chars=10),
        extractor=lambda request: ExtractionResponse.create(
            candidates=[CandidateValue.create({"fact": "value"})]
        ),
        checkpoint_sink=checkpoints.append,
    )
    checkpoint = checkpoints[0]

    missing_id = json.loads(checkpoint.to_json())
    del missing_id["checkpoint_id"]
    with pytest.raises(ValueError, match="missing required"):
        ExtractionCheckpoint.from_dict(missing_id)

    unknown = json.loads(checkpoint.to_json())
    unknown["unexpected"] = True
    with pytest.raises(ValueError, match="unknown persisted"):
        ExtractionCheckpoint.from_dict(unknown)

    nested_tamper = json.loads(checkpoint.to_json())
    nested_tamper["attempt"]["attempt_id"] = "0" * 64
    with pytest.raises(ValueError, match="attempt_id mismatch"):
        ExtractionCheckpoint.from_dict(nested_tamper)


def test_invalid_unicode_raw_output_is_escaped_without_losing_candidates():
    checkpoints = []
    run = run_shingled_extraction(
        source_ref="doc:unicode-output",
        text="source",
        recipe=_recipe(target_chars=100, overlap_chars=10),
        extractor=lambda request: ExtractionResponse.create(
            raw_output="before\ud800after",
            candidates=[CandidateValue.create({"retained": True})],
        ),
        checkpoint_sink=checkpoints.append,
    )

    assert len(run.candidates) == 2
    assert all(attempt.raw_output == "before\\ud800after" for attempt in run.attempts)
    assert all(attempt.raw_output_escaped for attempt in run.attempts)
    assert all(checkpoint.candidates for checkpoint in checkpoints)
    assert ExtractionRun.from_json(run.to_json()) == run
