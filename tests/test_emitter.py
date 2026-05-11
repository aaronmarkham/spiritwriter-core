"""Tests for TraceEmitter cap-context integration + provenance queries.

The legacy `TestTraceEmitter` smoke tests live in test_shard.py; this
file covers the chained-capability integration added on top of them:

  - constructor accepts cap_id / cap_chain / subject_thumbprint / role
  - emitted events carry those fields automatically (sticky defaults)
  - per-event kwargs still override
  - current_trace_ref() returns the chain-position pointer used by
    MemoryShard.trace_ref
  - the events_by_* / events_under_chain provenance filters work
  - back-compat: emitters built without cap context produce identical
    events to the pre-feature version
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spiritwriter.fabric.emitter import (
    TraceEmitter,
    verify_chain,
    events_by_cap,
    events_by_signer,
    events_by_role,
    events_under_chain,
)


# === Cap context on emitters ============================================


class TestCapContextAttached:
    def test_no_context_emits_no_cap_fields(self, tmp_path):
        """Legacy emitters (no cap context) must produce events identical
        in shape to the pre-feature emitter — otherwise downstream tools
        parsing old trace logs break."""
        e = TraceEmitter(run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"))
        evt = e.emit("noop")
        assert "cap_id" not in evt
        assert "cap_chain" not in evt
        assert "subject_thumbprint" not in evt
        assert "role" not in evt

    def test_cap_id_attached_to_every_event(self, tmp_path):
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_id="cap:abc",
        )
        evt1 = e.emit("step1")
        evt2 = e.emit("step2")
        assert evt1["cap_id"] == "cap:abc"
        assert evt2["cap_id"] == "cap:abc"

    def test_full_context_attached(self, tmp_path):
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_id="cap:leaf",
            cap_chain=["cap:root", "cap:orch", "cap:leaf"],
            subject_thumbprint="abc123" * 10 + "abcd",  # 64 hex chars
            role="builder",
        )
        evt = e.emit("action")
        assert evt["cap_id"] == "cap:leaf"
        assert evt["cap_chain"] == ["cap:root", "cap:orch", "cap:leaf"]
        assert evt["subject_thumbprint"].startswith("abc123")
        assert evt["role"] == "builder"

    def test_per_event_override(self, tmp_path):
        """A single event can override the emitter's sticky cap context.
        Useful when one action runs under a different cap than the
        emitter's default."""
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_id="cap:default", role="builder",
        )
        evt = e.emit("special", cap_id="cap:other", role="inspector")
        assert evt["cap_id"] == "cap:other"
        assert evt["role"] == "inspector"

    def test_cap_chain_is_isolated_copy(self, tmp_path):
        """Mutating the list passed to the constructor must not leak
        into emitted events — otherwise a caller-side reorder would
        retroactively change every prior event's cap_chain field."""
        chain = ["cap:root", "cap:leaf"]
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_chain=chain,
        )
        evt = e.emit("step")
        chain.append("cap:rogue")
        assert evt["cap_chain"] == ["cap:root", "cap:leaf"]

    def test_chain_integrity_preserved(self, tmp_path):
        """Adding cap context must not break hash-chain verification —
        the chain hashes everything except `hash`/`sig`, so cap fields
        are part of the chain and any tampering still gets caught."""
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_id="cap:abc", role="builder",
        )
        e.emit("step1")
        e.emit("step2")
        e.emit("step3")
        events = e.get_events()
        assert verify_chain(events) is True

    def test_cap_id_tampering_breaks_chain(self, tmp_path):
        """Cap fields are inside the hash — flipping cap_id post-hoc
        invalidates the chain. This is the property that makes provenance
        queries trustworthy."""
        path = tmp_path / "t.jsonl"
        e = TraceEmitter(run_id="r", agent_id="a", out_path=str(path), cap_id="cap:real")
        e.emit("step")
        # Tamper on disk
        data = json.loads(path.read_text(encoding="utf-8").strip())
        data["cap_id"] = "cap:forged"
        path.write_text(json.dumps(data) + "\n", encoding="utf-8")
        loaded = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
        assert verify_chain(loaded) is False


# === current_trace_ref() ================================================


class TestCurrentTraceRef:
    def test_none_before_any_event(self, tmp_path):
        e = TraceEmitter(run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"))
        assert e.current_trace_ref() is None

    def test_ref_after_emit(self, tmp_path):
        e = TraceEmitter(run_id="run-abc", agent_id="a", out_path=str(tmp_path / "t.jsonl"))
        evt = e.emit("step")
        ref = e.current_trace_ref()
        assert ref is not None
        assert ref.startswith("chain:run-abc#")
        assert ref.endswith(evt["hash"])

    def test_ref_advances_with_each_event(self, tmp_path):
        e = TraceEmitter(run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"))
        e.emit("step1")
        ref1 = e.current_trace_ref()
        e.emit("step2")
        ref2 = e.current_trace_ref()
        assert ref1 != ref2
        assert ref1 is not None and ref2 is not None

    def test_shard_trace_ref_round_trip(self, tmp_path):
        """The canonical use case: stamp a shard's trace_ref with the
        current chain position so 'which event was this shard emitted
        under?' is answerable."""
        from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind

        e = TraceEmitter(run_id="run-x", agent_id="a", out_path=str(tmp_path / "t.jsonl"))
        evt = e.emit("about_to_create_shard")
        shard = MemoryShard(
            atoms=[ShardAtom(text="produced under traced operation", kind=AtomKind.FACT)],
            scope="test:scope",
            origin="agent:test",
            trace_ref=e.current_trace_ref(),
        )
        assert shard.trace_ref == f"chain:run-x#{evt['hash']}"


# === Provenance query helpers ===========================================


class TestProvenanceQueries:
    @pytest.fixture
    def mixed_events(self, tmp_path):
        """A trace from one worker + a trace from another, both
        emitted under the same orchestrator's authority."""
        e1 = TraceEmitter(
            run_id="run-x", agent_id="builder-2", out_path=str(tmp_path / "b.jsonl"),
            cap_id="cap:builder-2",
            cap_chain=["cap:root", "cap:orch", "cap:builder-2"],
            subject_thumbprint="builder2-thumb",
            role="builder",
        )
        e1.emit("step1")
        e1.emit("step2")

        e2 = TraceEmitter(
            run_id="run-x", agent_id="inspector-1", out_path=str(tmp_path / "i.jsonl"),
            cap_id="cap:inspector-1",
            cap_chain=["cap:root", "cap:orch", "cap:inspector-1"],
            subject_thumbprint="inspector1-thumb",
            role="inspector",
        )
        e2.emit("review")

        # Both event streams combined as you'd see in a merged trace store
        return e1.get_events() + e2.get_events()

    def test_events_by_cap_filters_to_leaf(self, mixed_events):
        b = events_by_cap(mixed_events, "cap:builder-2")
        assert len(b) == 2
        assert all(e["cap_id"] == "cap:builder-2" for e in b)

    def test_events_by_signer_finds_only_that_key(self, mixed_events):
        i = events_by_signer(mixed_events, "inspector1-thumb")
        assert len(i) == 1
        assert i[0]["type"] == "review"

    def test_events_by_role(self, mixed_events):
        builders = events_by_role(mixed_events, "builder")
        inspectors = events_by_role(mixed_events, "inspector")
        assert len(builders) == 2
        assert len(inspectors) == 1

    def test_events_under_chain_catches_descendants(self, mixed_events):
        """Querying by the orchestrator cap should return every worker's
        events, regardless of role — they all descended from it."""
        under_orch = events_under_chain(mixed_events, "cap:orch")
        assert len(under_orch) == 3  # 2 builder + 1 inspector

    def test_events_under_chain_root_catches_everything(self, mixed_events):
        under_root = events_under_chain(mixed_events, "cap:root")
        assert len(under_root) == 3

    def test_events_under_chain_misses_unrelated(self, mixed_events):
        under_other = events_under_chain(mixed_events, "cap:never-issued")
        assert under_other == []

    def test_events_under_chain_falls_back_to_cap_id(self, tmp_path):
        """When events have cap_id but no cap_chain (e.g., emitter built
        with cap_id alone), ancestor queries should still match the
        leaf — a degraded-but-honest fallback rather than silently
        returning nothing."""
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_id="cap:leaf",  # no cap_chain
        )
        e.emit("step")
        events = e.get_events()
        assert events_under_chain(events, "cap:leaf") == events
        assert events_under_chain(events, "cap:something-else") == []

    def test_empty_input_returns_empty(self):
        assert events_by_cap([], "cap:x") == []
        assert events_by_signer([], "thumb") == []
        assert events_by_role([], "builder") == []
        assert events_under_chain([], "cap:x") == []

    def test_filters_preserve_order(self, mixed_events):
        """Order matters: chain verification depends on event sequence,
        and humans reading provenance reports expect chronological
        order. Filter operations must not reorder."""
        b = events_by_cap(mixed_events, "cap:builder-2")
        ts = [e["ts"] for e in b]
        assert ts == sorted(ts)
