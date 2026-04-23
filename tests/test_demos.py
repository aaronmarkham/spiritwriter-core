"""Tests for the four fabric demo examples.

Each demo's main() should:
  - Exit 0 (all assertions pass internally)
  - Produce trace files in its output directory
  - Have a verifiable hash chain
  - Contain the expected event types
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from spiritwriter.fabric.emitter import verify_chain


DEMO_DIR = Path(__file__).parent.parent / "examples"


def _load_demo(subdir: str):
    """Import a demo module whose directory name starts with a digit."""
    run_path = DEMO_DIR / subdir / "run.py"
    spec = importlib.util.spec_from_file_location(f"demo_{subdir}", run_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_events(jsonl_path: Path) -> list[dict]:
    """Load trace events from a JSONL file."""
    events = []
    with open(jsonl_path, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                events.append(json.loads(line))
    return events


# ── Demo 1: Simple Trace ───────────────────────────────────────────


class TestDemo01SimpleTrace:
    @pytest.fixture(autouse=True, scope="class")
    def run_demo(self, tmp_path_factory):
        traces = tmp_path_factory.mktemp("demo01")
        demo = _load_demo("01_simple_trace")
        rc = demo.main(output_dir=traces)
        TestDemo01SimpleTrace._traces = traces
        TestDemo01SimpleTrace._rc = rc

    def test_main_exits_zero(self):
        assert self._rc == 0

    def test_parent_trace_created(self):
        path = self._traces / "parent.jsonl"
        assert path.exists(), "parent.jsonl should be created"
        events = _load_events(path)
        assert len(events) >= 4

    def test_child_trace_created(self):
        path = self._traces / "child.jsonl"
        assert path.exists(), "child.jsonl should be created"
        events = _load_events(path)
        assert len(events) >= 4

    def test_parent_chain_verifies(self):
        events = _load_events(self._traces / "parent.jsonl")
        assert verify_chain(events)

    def test_child_chain_verifies(self):
        events = _load_events(self._traces / "child.jsonl")
        assert verify_chain(events)

    def test_parent_event_types(self):
        events = _load_events(self._traces / "parent.jsonl")
        types = {e["type"] for e in events}
        assert "shard_created" in types
        assert "entitlement_granted" in types
        assert "studio_job_packaged" in types
        assert "spawn_with_shards" in types

    def test_child_event_types(self):
        events = _load_events(self._traces / "child.jsonl")
        types = {e["type"] for e in events}
        assert "capability_checked" in types
        assert "shard_decrypted" in types
        assert "studio_job_completed" in types

    def test_mermaid_generated(self):
        path = self._traces / "workflow.mmd"
        assert path.exists()
        content = path.read_text()
        assert "graph TD" in content


# ── Demo 2: Todo Fan-Out ───────────────────────────────────────────


class TestDemo02TodoFanout:
    @pytest.fixture(autouse=True, scope="class")
    def run_demo(self, tmp_path_factory):
        traces = tmp_path_factory.mktemp("demo02")
        demo = _load_demo("02_todo_fanout")
        rc = demo.main(output_dir=traces)
        TestDemo02TodoFanout._traces = traces
        TestDemo02TodoFanout._rc = rc

    def test_main_exits_zero(self):
        assert self._rc == 0

    def test_parent_trace_created(self):
        path = self._traces / "parent.jsonl"
        assert path.exists()
        events = _load_events(path)
        assert len(events) >= 10, "Should have events for 4 subagents + setup + assembly"

    def test_all_child_traces_created(self):
        child_files = list(self._traces.glob("child_*.jsonl"))
        assert len(child_files) == 4, f"Expected 4 child traces, got {len(child_files)}"

    def test_parent_chain_verifies(self):
        events = _load_events(self._traces / "parent.jsonl")
        assert verify_chain(events)

    def test_all_child_chains_verify(self):
        for child_file in self._traces.glob("child_*.jsonl"):
            events = _load_events(child_file)
            assert verify_chain(events), f"Chain failed for {child_file.name}"

    def test_fanout_spawn_events(self):
        events = _load_events(self._traces / "parent.jsonl")
        spawn_events = [e for e in events if e["type"] == "spawn_with_shards"]
        assert len(spawn_events) == 4

    def test_assembly_shard_created(self):
        events = _load_events(self._traces / "parent.jsonl")
        shard_created = [e for e in events if e["type"] == "shard_created"]
        # At least 2: the todo shard + the assembly shard
        assert len(shard_created) >= 2


# ── Demo 3: Skills and Tools ──────────────────────────────────────


class TestDemo03SkillsAndTools:
    @pytest.fixture(autouse=True, scope="class")
    def run_demo(self, tmp_path_factory):
        traces = tmp_path_factory.mktemp("demo03")
        demo = _load_demo("03_skills_and_tools")
        rc = demo.main(output_dir=traces)
        TestDemo03SkillsAndTools._traces = traces
        TestDemo03SkillsAndTools._rc = rc

    def test_main_exits_zero(self):
        assert self._rc == 0

    def test_trace_created(self):
        path = self._traces / "agent.jsonl"
        assert path.exists()

    def test_chain_verifies(self):
        events = _load_events(self._traces / "agent.jsonl")
        assert verify_chain(events)

    def test_skill_events_present(self):
        events = _load_events(self._traces / "agent.jsonl")
        skill_events = [e for e in events if e["type"] == "skill_invoked"]
        assert len(skill_events) == 4
        skill_names = {e["skill_name"] for e in skill_events}
        assert skill_names == {"search_flights", "check_weather", "search_hotels", "draft_itinerary"}

    def test_tool_events_present(self):
        events = _load_events(self._traces / "agent.jsonl")
        tool_called = [e for e in events if e["type"] == "tool_called"]
        tool_result = [e for e in events if e["type"] == "tool_result"]
        assert len(tool_called) == 3  # flights, weather, hotels (not itinerary)
        assert len(tool_result) == 3

    def test_tool_events_have_hashes(self):
        events = _load_events(self._traces / "agent.jsonl")
        for e in events:
            if e["type"] == "tool_called":
                assert "argument_hash" in e
                assert len(e["argument_hash"]) == 64  # SHA-256 hex
            elif e["type"] == "tool_result":
                assert "output_hash" in e
                assert len(e["output_hash"]) == 64

    def test_draft_itinerary_has_no_tool_calls(self):
        """draft_itinerary is a pure synthesis skill — no tool_called between its events."""
        events = _load_events(self._traces / "agent.jsonl")
        # Find the draft_itinerary skill_invoked index
        draft_idx = next(
            i for i, e in enumerate(events)
            if e["type"] == "skill_invoked" and e["skill_name"] == "draft_itinerary"
        )
        # Next event should be skill_result (no tool_called in between)
        assert events[draft_idx + 1]["type"] == "skill_result"


# ── Demo 4: Governance Divergence ──────────────────────────────────


class TestDemo04GovernanceDivergence:
    @pytest.fixture(autouse=True, scope="class")
    def run_demo(self, tmp_path_factory):
        traces = tmp_path_factory.mktemp("demo04")
        demo = _load_demo("04_governance_divergence")
        rc = demo.main(output_dir=traces)
        TestDemo04GovernanceDivergence._traces = traces
        TestDemo04GovernanceDivergence._rc = rc

    def test_main_exits_zero(self):
        assert self._rc == 0

    def test_all_traces_created(self):
        assert (self._traces / "parent.jsonl").exists()
        assert (self._traces / "run_a.jsonl").exists()
        assert (self._traces / "run_b.jsonl").exists()

    def test_all_chains_verify(self):
        for name in ["parent.jsonl", "run_a.jsonl", "run_b.jsonl"]:
            events = _load_events(self._traces / name)
            assert verify_chain(events), f"Chain failed for {name}"

    def test_run_a_completes_successfully(self):
        events = _load_events(self._traces / "run_a.jsonl")
        types = {e["type"] for e in events}
        assert "studio_job_completed" in types
        assert "studio_job_failed" not in types
        assert "capability_denied" not in types
        assert "budget_exceeded" not in types

    def test_run_b_has_governance_violations(self):
        events = _load_events(self._traces / "run_b.jsonl")
        types = [e["type"] for e in events]
        assert "capability_denied" in types
        assert "budget_exceeded" in types
        assert "studio_job_failed" in types
        assert "studio_job_completed" not in types

    def test_run_b_capability_denied_details(self):
        events = _load_events(self._traces / "run_b.jsonl")
        denied = [e for e in events if e["type"] == "capability_denied"]
        assert len(denied) == 2
        denied_caps = {e["capability"] for e in denied}
        assert "upload:youtube" in denied_caps
        assert "exec:run" in denied_caps

    def test_run_b_budget_exceeded_details(self):
        events = _load_events(self._traces / "run_b.jsonl")
        exceeded = [e for e in events if e["type"] == "budget_exceeded"]
        assert len(exceeded) == 1
        assert exceeded[0]["attempted_amount"] == 0.50
        assert exceeded[0]["budget_usd"] == 0.25

    def test_parent_detects_failure_and_falls_back(self):
        events = _load_events(self._traces / "parent.jsonl")
        types = [e["type"] for e in events]
        assert "subagent_completed" in types
        assert "subagent_failed" in types
        assert "fallback_applied" in types

    def test_mermaid_diagrams_generated(self):
        assert (self._traces / "run_a_workflow.mmd").exists()
        assert (self._traces / "run_b_workflow.mmd").exists()
