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
        assert "job_packaged" in types
        assert "spawn_with_shards" in types

    def test_child_event_types(self):
        events = _load_events(self._traces / "child.jsonl")
        types = {e["type"] for e in events}
        assert "capability_checked" in types
        assert "shard_decrypted" in types
        assert "job_completed" in types

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
        assert "job_completed" in types
        assert "job_failed" not in types
        assert "capability_denied" not in types
        assert "budget_exceeded" not in types

    def test_run_b_has_governance_violations(self):
        events = _load_events(self._traces / "run_b.jsonl")
        types = [e["type"] for e in events]
        assert "capability_denied" in types
        assert "budget_exceeded" in types
        assert "job_failed" in types
        assert "job_completed" not in types

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


# ── Flavor doc worked examples ────────────────────────────────────────
#
# The hex values below mirror the worked examples in
# docs/substrate-flavor.md. Any drift in canonicalization, hashing,
# signing, or signing-payload composition will break these — which is
# exactly the alarm we want, because the doc is the contract a
# library-free implementer reads to be interoperable.
#
# When intentionally changing the wire format: regenerate by running
# `python examples/flavor_examples.py` and update both the doc and the
# expected values here in lockstep.


EXPECTED_FLAVOR_VALUES = {
    "root_pubkey": "4cb5abf6ad79fbf5abbccafcc269d85cd2651ed4b885b5869f241aedf0a5ba29",
    "orch_pubkey": "7422b9887598068e32c4448a949adb290d0f4e35b9e01b0ee5f1a1e600fe2674",
    "worker_pubkey": "f381626e41e7027ea431bfe3009e94bdd25a746beec468948d6c3c7c5dc9a54b",
    "worker_thumbprint": "c2b6bf688fb8be003dcf12ee147bfd0708d7931a786c0d42ba9f5381a722998f",
    "unsigned_shard_id": "7e25b712ff54f42e333da4c526de24177ffe9e6fc71dff549a38b46f25b58d9c",
    "root_cap_id": "0c5f215c1aa6d704c57c7eacda4ef3edd8c5b9c45b64a12a8410ae3b14ae9134",
    "orch_cap_id": "855c4b0ea32ceb294378f06fe61c79b34a16e4b9a3ecc4ebca8c2d7e4cb6a93f",
    "worker_cap_id": "0bb95a741cffeba06a377d564a9484c7305cf9dfb43318c97cccc5f508427aa6",
    "produced_shard_id": "2e23e1733453531bd6a640c500049767ea12ddd2333c8cb1acd5c50e222b37fe",
    "produced_signature": "c33490397c7c22705e869197aae112bf0a8494da134a1c412d32a19b5eb5fcbbe3b1b0a1840298ffcd31b44019467603f73146cff9ec1eed9545a7356c215408",
}


class TestFlavorDocExamples:
    """Pin the worked-example values used by docs/substrate-flavor.md.

    A failure here means external implementations following the doc
    will produce different bytes than this library — i.e., the wire
    format changed.
    """

    @pytest.fixture(scope="class")
    def computed(self):
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PrivateKey,
        )
        from spiritwriter.fabric.shard import (
            MemoryShard,
            ShardAtom,
            AtomKind,
            DecayClass,
            pubkey_thumbprint,
        )
        from spiritwriter.fabric.entitlement import (
            Capability,
            Caveat,
            CaveatType,
            create_entitlement,
            issue_delegated,
        )

        def _kp(seed_int: int) -> tuple[bytes, bytes]:
            seed = seed_int.to_bytes(32, "big")
            sk = Ed25519PrivateKey.from_private_bytes(seed)
            sk_b = sk.private_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PrivateFormat.Raw,
                encryption_algorithm=serialization.NoEncryption(),
            )
            pk_b = sk.public_key().public_bytes(
                encoding=serialization.Encoding.Raw,
                format=serialization.PublicFormat.Raw,
            )
            return sk_b, pk_b

        root_sk, root_pk = _kp(1)
        orch_sk, orch_pk = _kp(2)
        worker_sk, worker_pk = _kp(3)

        unsigned = MemoryShard(
            atoms=[ShardAtom(
                text="The Fugaku supercomputer is in Kobe, Japan.",
                kind=AtomKind.FACT,
                entity="Fugaku", key="location", value="Kobe, Japan",
            )],
            scope="sw:article:run-abc",
            origin="agent:builder-2",
            decay_class=DecayClass.PERMANENT,
            created_at="2026-05-10T12:00:00Z",
        )

        root = create_entitlement(
            granted_to="aaron", granted_by="self", shard_keys={},
            scopes=["sw:*"],
            capabilities=[Capability.SHARD_READ, Capability.SHARD_WRITE],
            secrets=[], budget_usd=100.0,
        )
        root.token_id = "00000000-0000-0000-0000-000000000001"
        root.created_at = "2026-05-10T12:00:00Z"
        root.subject_pubkey = root_pk
        root.caveats = [Caveat(CaveatType.MAX_DELEGATION_DEPTH, 3)]
        root.sign(root_sk)

        orch = issue_delegated(
            root, root_sk,
            subject_pubkey=orch_pk,
            granted_to="orchestrator:run-abc",
            scopes=["sw:article:run-abc:*"],
            capabilities=[Capability.SHARD_WRITE],
            caveats=[Caveat(CaveatType.EXPIRES_AT, "2026-05-10T13:00:00Z")],
        )
        orch.token_id = "00000000-0000-0000-0000-000000000002"
        orch.created_at = "2026-05-10T12:00:00Z"
        orch.signature = None
        orch.sign(root_sk)

        leaf = issue_delegated(
            orch, orch_sk,
            subject_pubkey=worker_pk,
            granted_to="worker:builder-2",
            capabilities=[Capability.SHARD_WRITE],
        )
        leaf.token_id = "00000000-0000-0000-0000-000000000003"
        leaf.created_at = "2026-05-10T12:00:00Z"
        leaf.signature = None
        leaf.sign(orch_sk)

        produced = MemoryShard(
            atoms=[ShardAtom(
                text="Fugaku is currently the world's fourth-fastest supercomputer.",
                kind=AtomKind.FACT,
                entity="Fugaku", key="ranking", value="4",
            )],
            scope="sw:article:run-abc:builder-2",
            origin="agent:builder-2",
            decay_class=DecayClass.PERMANENT,
            created_at="2026-05-10T12:30:00Z",
            cap_id=leaf.cap_id,
        )
        produced.sign(worker_sk)

        return {
            "root_pubkey": root_pk.hex(),
            "orch_pubkey": orch_pk.hex(),
            "worker_pubkey": worker_pk.hex(),
            "worker_thumbprint": pubkey_thumbprint(worker_pk),
            "unsigned_shard_id": unsigned.shard_id,
            "root_cap_id": root.cap_id,
            "orch_cap_id": orch.cap_id,
            "worker_cap_id": leaf.cap_id,
            "produced_shard_id": produced.shard_id,
            "produced_signature": produced.signature,
            # For verification round-trip:
            "_chain": [root, orch, leaf],
            "_root_pk_bytes": root_pk,
            "_worker_pk_bytes": worker_pk,
            "_produced": produced,
        }

    @pytest.mark.parametrize("name", list(EXPECTED_FLAVOR_VALUES.keys()))
    def test_pinned_value(self, computed, name):
        assert computed[name] == EXPECTED_FLAVOR_VALUES[name], (
            f"{name} drifted — re-run examples/flavor_examples.py and update "
            "docs/substrate-flavor.md and EXPECTED_FLAVOR_VALUES in tandem."
        )

    def test_full_verification_round_trip(self, computed):
        """End-to-end: chain verifies, authorizes the shard's scope at issue
        time, and the leaf signature validates against the worker's pubkey."""
        from spiritwriter.fabric.entitlement import (
            verify_chain as verify_cap_chain,
            authorize_chain,
        )
        chain = computed["_chain"]
        verify_cap_chain(chain, root_pubkeys=[computed["_root_pk_bytes"]])
        assert authorize_chain(
            chain,
            scope=computed["_produced"].scope,
            now_iso="2026-05-10T12:30:00Z",
        )
        computed["_produced"].verify(computed["_worker_pk_bytes"])
