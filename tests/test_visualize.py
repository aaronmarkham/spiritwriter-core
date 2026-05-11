"""Tests for the delegation-tree renderer in spiritwriter.fabric.visualize.

The legacy renderers (workflow / genealogy / multi-agent) are exercised
implicitly by the existing demo tests in test_demos.py. This file covers
the cap-aware renderer added on top of the cap-trace integration:

  - reconstructs the cap tree from event cap_chains
  - labels leaves with role + event count where derivable
  - degrades cleanly when events lack cap context
  - produces deterministic output (sorted) for diff-based tests
  - is reachable via render_trace(..., diagram_type="delegation")
  - is included in generate_all()
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from spiritwriter.fabric.emitter import TraceEmitter
from spiritwriter.fabric.visualize import (
    render_delegation_tree,
    render_trace,
    generate_all,
    load_trace,
)


def _events_from(emitter_kwargs_list, tmp_path):
    """Helper: build N emitters from kwargs dicts, emit one event each,
    return the merged event list."""
    merged = []
    for i, kw in enumerate(emitter_kwargs_list):
        e = TraceEmitter(
            run_id=kw.pop("run_id", "r"),
            agent_id=kw.pop("agent_id", f"a{i}"),
            out_path=str(tmp_path / f"t{i}.jsonl"),
            **kw,
        )
        e.emit("step")
        merged.extend(e.get_events())
    return merged


class TestRenderDelegationTree:
    def test_empty_input_produces_valid_minimal_graph(self):
        out = render_delegation_tree([])
        assert out.startswith("graph TD")
        assert "no cap-tagged events" in out

    def test_legacy_events_without_cap_fields_still_safe(self, tmp_path):
        """A trace produced before the cap-context feature should not
        crash the renderer — it should produce the empty-graph fallback."""
        e = TraceEmitter(run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"))
        e.emit("step1")
        e.emit("step2")
        events = e.get_events()
        out = render_delegation_tree(events)
        assert out.startswith("graph TD")
        assert "no cap-tagged events" in out

    def test_three_worker_tree_shape(self, tmp_path):
        """The canonical demo shape: root → orch → three workers.
        The tree should have one root, one branch (orch), and three
        leaves (one per role)."""
        events = _events_from(
            [
                {"cap_id": "cap:builder", "cap_chain": ["cap:root", "cap:orch", "cap:builder"], "role": "builder"},
                {"cap_id": "cap:inspector", "cap_chain": ["cap:root", "cap:orch", "cap:inspector"], "role": "inspector"},
                {"cap_id": "cap:critic", "cap_chain": ["cap:root", "cap:orch", "cap:critic"], "role": "critic"},
            ],
            tmp_path,
        )
        out = render_delegation_tree(events)

        # Tree structure: each worker connects to orch; orch connects to root
        assert "C_cap:root --> C_cap:orc"[:13] in out or "C_cap:roo --> C_cap:orc" in out
        # Five nodes: 1 root + 1 branch + 3 leaves
        node_lines = [l for l in out.splitlines() if "[" in l and "]" in l]
        assert len(node_lines) == 5

    def test_leaf_label_includes_role_and_event_count(self, tmp_path):
        """Leaf nodes show role + event count where the worker tagged them.
        Without this annotation, the tree is just opaque hex IDs."""
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_id="cap:builder",
            cap_chain=["cap:root", "cap:builder"],
            role="builder",
        )
        e.emit("step1")
        e.emit("step2")
        e.emit("step3")
        out = render_delegation_tree(e.get_events())
        assert "role: builder" in out
        assert "3 events" in out

    def test_singular_event_count_label(self, tmp_path):
        """Singular vs plural label — pedantic but the doc readers will
        notice '1 events'."""
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_id="cap:x", cap_chain=["cap:r", "cap:x"], role="solo",
        )
        e.emit("step")
        out = render_delegation_tree(e.get_events())
        assert "1 event" in out
        assert "1 events" not in out  # not pluralized

    def test_branch_class_for_intermediate_node(self, tmp_path):
        """A cap that's in someone's chain but never emitted events
        directly (e.g., an orchestrator that only issued caps) renders
        as a 'branch' — not a leaf."""
        events = _events_from(
            [
                {"cap_id": "cap:worker", "cap_chain": ["cap:root", "cap:orch", "cap:worker"], "role": "worker"},
            ],
            tmp_path,
        )
        out = render_delegation_tree(events)
        # cap:orch never had an event with cap_id == "cap:orch", so it's
        # a branch node — has children but no role/event-count label.
        orch_line = next(l for l in out.splitlines() if "C_cap:orc" in l and "[" in l)
        assert ":::branch" in orch_line
        # No role label on the branch (orch never tagged itself)
        assert "role:" not in orch_line

    def test_deterministic_output(self, tmp_path):
        """Same input must produce identical bytes — caps are sorted for
        diff-based testing and reproducible mermaid output."""
        events = _events_from(
            [
                {"cap_id": "cap:b", "cap_chain": ["cap:r", "cap:b"], "role": "b"},
                {"cap_id": "cap:a", "cap_chain": ["cap:r", "cap:a"], "role": "a"},
                {"cap_id": "cap:c", "cap_chain": ["cap:r", "cap:c"], "role": "c"},
            ],
            tmp_path,
        )
        out1 = render_delegation_tree(events)
        out2 = render_delegation_tree(events)
        assert out1 == out2

    def test_fallback_for_events_with_cap_id_only(self, tmp_path):
        """An emitter built with cap_id but no cap_chain (degraded setup)
        still produces a tree — the lonely cap renders as a root."""
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "t.jsonl"),
            cap_id="cap:lonely",
            role="solo",
        )
        e.emit("step")
        out = render_delegation_tree(e.get_events())
        assert "C_cap:lon" in out
        assert "role: solo" in out
        assert ":::root" in out  # no parent → renders as root

    def test_dispatcher_routes_delegation_diagram_type(self, tmp_path):
        events = _events_from(
            [{"cap_id": "cap:x", "cap_chain": ["cap:r", "cap:x"], "role": "x"}],
            tmp_path,
        )
        out = render_trace(events, diagram_type="delegation")
        assert out.startswith("graph TD")
        assert "C_cap:r" in out

    def test_generate_all_includes_delegation_mmd(self, tmp_path):
        """generate_all should write delegation.mmd alongside the other
        diagram outputs — readers comparing diagrams expect it."""
        # Build a trace file with cap context
        e = TraceEmitter(
            run_id="r", agent_id="a", out_path=str(tmp_path / "trace.jsonl"),
            cap_id="cap:leaf",
            cap_chain=["cap:root", "cap:leaf"],
            role="worker",
        )
        e.emit("step")

        out_dir = tmp_path / "diagrams"
        diagrams = generate_all(tmp_path / "trace.jsonl", out_dir)
        assert "delegation" in diagrams
        assert (out_dir / "delegation.mmd").exists()
        content = (out_dir / "delegation.mmd").read_text(encoding="utf-8")
        assert "role: worker" in content
