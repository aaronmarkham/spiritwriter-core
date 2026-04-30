"""Tests for Phalanx Phase 3: TraceEmitter integration + canonicalize hooks."""

from __future__ import annotations

import copy
import os

import pytest

from spiritwriter.fabric import (
    TraceEmitter,
    verify_chain,
    render_trace,
    CanonicalSchema,
    CanonicalRegistry,
    ResolutionTier,
)
from spiritwriter.fabric.visualize import render_simple_workflow


# ── 3a: TraceEmitter.get_events() and verify_chain() ───────────────


class TestGetEvents:
    def test_get_events_returns_emitted(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        em = TraceEmitter(run_id="r1", agent_id="a1", out_path=out)
        em.emit("alpha", x=1)
        em.emit("beta", x=2)
        em.emit("gamma", x=3)

        events = em.get_events()
        assert len(events) == 3
        assert events[0]["type"] == "alpha"
        assert events[1]["type"] == "beta"
        assert events[2]["type"] == "gamma"
        for e in events:
            assert isinstance(e, dict)
            assert "hash" in e

    def test_get_events_empty_file(self, tmp_path):
        out = str(tmp_path / "nonexistent.jsonl")
        em = TraceEmitter(run_id="r1", agent_id="a1", out_path=out)
        assert em.get_events() == []


class TestVerifyChain:
    def test_verify_chain_valid(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        em = TraceEmitter(run_id="r1", agent_id="a1", out_path=out)
        em.emit("a")
        em.emit("b")
        em.emit("c")
        events = em.get_events()
        assert verify_chain(events) is True

    def test_verify_chain_tampered(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        em = TraceEmitter(run_id="r1", agent_id="a1", out_path=out)
        em.emit("a")
        em.emit("b")
        events = em.get_events()
        events[1]["hash"] = "deadbeef"
        assert verify_chain(events) is False

    def test_verify_chain_broken_link(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        em = TraceEmitter(run_id="r1", agent_id="a1", out_path=out)
        em.emit("a")
        em.emit("b")
        em.emit("c")
        events = em.get_events()
        # Swap events 1 and 2 to break the chain linkage
        events[1], events[2] = events[2], events[1]
        assert verify_chain(events) is False

    def test_verify_chain_empty(self):
        assert verify_chain([]) is True


# ── 3c: metadata_fields on CanonicalSchema ──────────────────────────


def _make_schema(**overrides):
    defaults = dict(
        name="test",
        ess_fields=["last_name", "first_name", "dob"],
        fuzzy_fields={"last_name": 0.8, "first_name": 0.8},
        context_fields=["source"],
    )
    defaults.update(overrides)
    return CanonicalSchema(**defaults)


class TestMetadataFields:
    def test_metadata_fields_stored(self, tmp_path):
        schema = _make_schema(metadata_fields=["description", "risk"])
        db = tmp_path / "reg.db"
        reg = CanonicalRegistry(db, schema)

        candidate = {
            "last_name": "Doe",
            "first_name": "John",
            "dob": "1990-01-01",
            "source": "test",
            "description": "Some description",
            "risk": "high",
        }
        result = reg.resolve(candidate)
        cid = reg.upsert(candidate, result, "src", "id1")

        entity = reg.get_entity(cid)
        assert entity is not None
        assert entity["ess_fields"]["description"] == "Some description"
        assert entity["ess_fields"]["risk"] == "high"
        reg.close()

    def test_metadata_fields_not_in_resolution(self, tmp_path):
        schema = _make_schema(metadata_fields=["description"])
        db = tmp_path / "reg.db"
        reg = CanonicalRegistry(db, schema)

        # Register first entity
        c1 = {
            "last_name": "Doe",
            "first_name": "John",
            "dob": "1990-01-01",
            "description": "Version A",
        }
        r1 = reg.resolve(c1)
        reg.upsert(c1, r1, "src", "id1")

        # Resolve with different metadata but same ESS fields
        c2 = {
            "last_name": "Doe",
            "first_name": "John",
            "dob": "1990-01-01",
            "description": "Version B",
        }
        r2 = reg.resolve(c2)
        assert r2.tier == ResolutionTier.T1_EXACT
        reg.close()

    def test_metadata_fields_not_in_schema_hash(self):
        s1 = _make_schema(metadata_fields=[])
        s2 = _make_schema(metadata_fields=["description", "risk"])
        assert s1.schema_hash() == s2.schema_hash()


# ── 3b: CanonicalRegistry → TraceEmitter hooks ─────────────────────


class TestRegistryEmitterHooks:
    def test_resolve_emits_trace_event(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        em = TraceEmitter(run_id="r1", agent_id="a1", out_path=out)
        schema = _make_schema()
        db = tmp_path / "reg.db"
        reg = CanonicalRegistry(db, schema, emitter=em)

        candidate = {
            "last_name": "Doe",
            "first_name": "John",
            "dob": "1990-01-01",
        }
        reg.resolve(candidate)

        events = em.get_events()
        resolved_events = [e for e in events if e["type"] == "candidate_resolved"]
        assert len(resolved_events) == 1
        evt = resolved_events[0]
        assert evt["tier"] == "no_match"
        assert evt["confidence"] == 0.0
        reg.close()

    def test_merge_emits_trace_event(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        em = TraceEmitter(run_id="r1", agent_id="a1", out_path=out)
        schema = _make_schema()
        db = tmp_path / "reg.db"
        reg = CanonicalRegistry(db, schema, emitter=em)

        # Create two entities
        c1 = {"last_name": "Doe", "first_name": "John", "dob": "1990-01-01"}
        c2 = {"last_name": "Smith", "first_name": "Jane", "dob": "1985-05-05"}
        r1 = reg.resolve(c1)
        id1 = reg.upsert(c1, r1, "src", "id1")
        r2 = reg.resolve(c2)
        id2 = reg.upsert(c2, r2, "src", "id2")

        reg.merge(id1, id2, reason="duplicate")

        events = em.get_events()
        merged_events = [e for e in events if e["type"] == "entity_merged"]
        assert len(merged_events) == 1
        evt = merged_events[0]
        assert evt["keep_id"] == id1
        assert evt["discard_id"] == id2
        assert evt["tier"] == "manual"
        assert evt["reason"] == "duplicate"
        reg.close()

    def test_no_emitter_no_error(self, tmp_path):
        schema = _make_schema()
        db = tmp_path / "reg.db"
        reg = CanonicalRegistry(db, schema)  # no emitter

        c1 = {"last_name": "Doe", "first_name": "John", "dob": "1990-01-01"}
        c2 = {"last_name": "Smith", "first_name": "Jane", "dob": "1985-05-05"}
        r1 = reg.resolve(c1)
        id1 = reg.upsert(c1, r1, "src", "id1")
        r2 = reg.resolve(c2)
        id2 = reg.upsert(c2, r2, "src", "id2")
        reg.merge(id1, id2, reason="test")
        # No errors raised
        reg.close()


# ── 3d: render_trace() wrapper ──────────────────────────────────────


class TestRenderTrace:
    def test_render_trace_dispatches(self, tmp_path):
        out = str(tmp_path / "trace.jsonl")
        em = TraceEmitter(run_id="r1", agent_id="a1", out_path=out)
        em.emit("job_started", prompt="hello")
        em.emit("job_completed", spent_usd=1.0, token_id="t1", result_shard_id="s1")
        events = em.get_events()

        result_wrapper = render_trace(events, "workflow")
        result_direct = render_simple_workflow(events)
        assert result_wrapper == result_direct

    def test_render_trace_invalid_type(self):
        with pytest.raises(ValueError, match="Unknown diagram type"):
            render_trace([], "nonexistent")
