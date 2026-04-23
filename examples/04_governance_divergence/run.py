#!/usr/bin/env python3
"""Demo 4: Governance — when things go off the rails.

Packages a studio job with explicit entitlements (capabilities + budget).
Runs the same job twice:

  Run A (expected): subagent behaves, stays under budget, produces a
  well-formed result. Parent accepts it.

  Run B (off the rails): subagent tries to call a tool it doesn't have
  capability for, exceeds budget, and returns a malformed result. The
  entitlement/budget layer rejects the violations and emits governance
  events. The parent notices, emits subagent_failed, and falls back.

Usage:
    python examples/04_governance_divergence/run.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from spiritwriter.fabric.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass,
)
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.emitter import TraceEmitter, verify_chain
from spiritwriter.fabric.studio_job import StudioJobSpec, package_job
from spiritwriter.fabric.studio_runner import (
    hydrate_job, BudgetTracker, create_result_shard, StudioRunnerError,
)
from spiritwriter.fabric.entitlement import (
    Capability, validate_capability,
)
from spiritwriter.fabric.visualize import render_trace


# ── Run A: well-behaved subagent ────────────────────────────────────

def run_a_good_agent(task_text: str, store: ShardStore, trace_path: str) -> MemoryShard:
    """Subagent that follows the rules."""
    tracer = TraceEmitter(run_id="run-a", agent_id="worker-a", out_path=trace_path)
    job = hydrate_job(store, task_text, tracer=tracer)

    tracker = BudgetTracker(
        budget_usd=job.budget_usd,
        token_id=job.token.token_id,
        tracer=tracer,
    )

    # Check capabilities before acting
    can_search = validate_capability(job.token, Capability.WEB_SEARCH)
    tracer.capability_checked(
        token_id=job.token.token_id, capability=Capability.WEB_SEARCH, allowed=can_search,
    )

    can_read = validate_capability(job.token, Capability.SHARD_READ)
    tracer.capability_checked(
        token_id=job.token.token_id, capability=Capability.SHARD_READ, allowed=can_read,
    )

    # Do permitted work within budget
    tracker.record("web_search", 0.05)
    tracker.record("summarize", 0.03)

    # Build a proper result shard
    result = create_result_shard(
        job=job,
        results={
            "budget": tracker.summary(),
            "outputs": [{"type": "summary", "ref": "inline"}],
        },
        agent_id="worker-a",
    )
    result.atoms.append(ShardAtom(
        text="Analysis complete: the document describes a shard-based memory architecture.",
        kind=AtomKind.FACT,
        key="analysis",
    ))
    store.put(result)

    tracer.shard_created(
        shard_id=result.shard_id, scope=result.scope, atom_count=len(result.atoms),
    )
    tracer.studio_job_completed(
        token_id=job.token.token_id,
        result_shard_id=result.shard_id,
        spent_usd=tracker.spent,
    )
    return result


# ── Run B: misbehaving subagent ─────────────────────────────────────

def run_b_bad_agent(task_text: str, store: ShardStore, trace_path: str) -> MemoryShard | None:
    """Subagent that tries to exceed its entitlements."""
    tracer = TraceEmitter(run_id="run-b", agent_id="worker-b", out_path=trace_path)
    job = hydrate_job(store, task_text, tracer=tracer)

    tracker = BudgetTracker(
        budget_usd=job.budget_usd,
        token_id=job.token.token_id,
        tracer=tracer,
    )

    # Violation 1: try to use a capability it doesn't have
    can_upload = validate_capability(job.token, Capability.UPLOAD_YOUTUBE)
    tracer.capability_checked(
        token_id=job.token.token_id, capability=Capability.UPLOAD_YOUTUBE, allowed=can_upload,
    )
    if not can_upload:
        tracer.emit(
            "capability_denied",
            token_id=job.token.token_id,
            capability=Capability.UPLOAD_YOUTUBE,
            reason="Token does not grant upload:youtube",
        )

    can_exec = validate_capability(job.token, Capability.EXEC_RUN)
    tracer.capability_checked(
        token_id=job.token.token_id, capability=Capability.EXEC_RUN, allowed=can_exec,
    )
    if not can_exec:
        tracer.emit(
            "capability_denied",
            token_id=job.token.token_id,
            capability=Capability.EXEC_RUN,
            reason="Token does not grant exec:run",
        )

    # Do some legitimate work first
    tracker.record("web_search", 0.05)
    tracker.record("analyze_document", 0.10)

    # Violation 2: try to exceed the budget
    try:
        tracker.record("expensive_llm_call", 0.50)  # This blows the $0.25 budget
    except StudioRunnerError as exc:
        tracer.emit(
            "budget_exceeded",
            token_id=job.token.token_id,
            attempted_amount=0.50,
            already_spent=tracker.spent,
            budget_usd=tracker.budget_usd,
            error=str(exc),
        )
        tracer.studio_job_failed(
            token_id=job.token.token_id,
            error=f"Budget exceeded: {exc}",
            spent_usd=tracker.spent,
        )
        return None

    # Won't reach here because the budget check above will raise
    return None


# ── Main: parent runs both and compares ─────────────────────────────

def main() -> int:
    output_dir = Path(__file__).parent / "traces"
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        store = ShardStore(td)
        parent_trace = str(output_dir / "parent.jsonl")
        run_a_trace = str(output_dir / "run_a.jsonl")
        run_b_trace = str(output_dir / "run_b.jsonl")

        for p in [parent_trace, run_a_trace, run_b_trace]:
            Path(p).unlink(missing_ok=True)

        parent = TraceEmitter(
            run_id="governance-demo",
            agent_id="orchestrator",
            out_path=parent_trace,
        )

        # Build the source material
        content_atoms = [
            ShardAtom(
                text="Analyze this document about distributed agent memory systems.",
                kind=AtomKind.CONTEXT,
                key="document",
            ),
        ]

        # ── Run A: well-behaved agent with generous budget ──
        pkg_a = package_job(
            store=store,
            content_atoms=content_atoms,
            job_spec=StudioJobSpec(
                prompt="Analyze the document and produce a summary.",
                budget_usd=1.00,
            ),
            agent_id="orchestrator",
            granted_to="worker-a",
            capabilities=[
                Capability.SHARD_READ,
                Capability.SHARD_WRITE,
                Capability.WEB_SEARCH,
                Capability.WEB_FETCH,
            ],
            scope_prefix="run-a",
            tracer=parent,
        )
        parent.spawn_with_shards(
            child_agent_id="worker-a",
            shard_refs=[
                {"shard_id": pkg_a.content_shard_id, "scope": "run-a:content"},
                {"shard_id": pkg_a.task_shard_id, "scope": "run-a:task"},
            ],
            task="Analyze document (expected behavior)",
            child_run_id="run-a",
        )

        result_a = run_a_good_agent(pkg_a.spawn_task_text(), store, run_a_trace)
        parent.emit(
            "subagent_completed",
            child_agent_id="worker-a",
            child_run_id="run-a",
            result_shard_id=result_a.shard_id,
            status="accepted",
        )

        # ── Run B: misbehaving agent with tight budget + limited caps ──
        pkg_b = package_job(
            store=store,
            content_atoms=content_atoms,
            job_spec=StudioJobSpec(
                prompt="Analyze the document and produce a summary.",
                budget_usd=0.25,  # Tight budget — agent will try to exceed it
            ),
            agent_id="orchestrator",
            granted_to="worker-b",
            capabilities=[
                Capability.SHARD_READ,
                Capability.WEB_SEARCH,
                # Deliberately missing: EXEC_RUN, UPLOAD_YOUTUBE, SHARD_WRITE
            ],
            scope_prefix="run-b",
            tracer=parent,
        )
        parent.spawn_with_shards(
            child_agent_id="worker-b",
            shard_refs=[
                {"shard_id": pkg_b.content_shard_id, "scope": "run-b:content"},
                {"shard_id": pkg_b.task_shard_id, "scope": "run-b:task"},
            ],
            task="Analyze document (expected to misbehave)",
            child_run_id="run-b",
        )

        result_b = run_b_bad_agent(pkg_b.spawn_task_text(), store, run_b_trace)

        # Parent reads Run B's trace and notices the failures
        run_b_events = TraceEmitter(run_id="", agent_id="", out_path=run_b_trace).get_events()
        governance_issues = [
            e for e in run_b_events
            if e["type"] in ("capability_denied", "budget_exceeded", "studio_job_failed")
        ]

        parent.emit(
            "subagent_failed",
            child_agent_id="worker-b",
            child_run_id="run-b",
            governance_violations=len(governance_issues),
            violation_types=[e["type"] for e in governance_issues],
            status="rejected",
        )

        # Fallback: parent uses Run A's result instead
        parent.emit(
            "fallback_applied",
            reason="Run B governance violations detected",
            using_result_from="run-a",
            result_shard_id=result_a.shard_id,
        )

        # ── Verify all chains ──
        parent_events = parent.get_events()
        run_a_events = TraceEmitter(run_id="", agent_id="", out_path=run_a_trace).get_events()

        parent_ok = verify_chain(parent_events)
        run_a_ok = verify_chain(run_a_events)
        run_b_ok = verify_chain(run_b_events)

        print("== Demo 4: Governance Divergence ==\n")
        print(f"  Parent trace:  {len(parent_events)} events, chain valid: {parent_ok}")
        print(f"  Run A trace:   {len(run_a_events)} events, chain valid: {run_a_ok}")
        print(f"  Run B trace:   {len(run_b_events)} events, chain valid: {run_b_ok}")

        # Show Run A (good path)
        print("\n  --- Run A (expected behavior) ---")
        for e in run_a_events:
            t = e["type"]
            if t == "capability_checked":
                icon = "Y" if e["allowed"] else "N"
                print(f"    [{icon}] {t}: {e['capability']}")
            elif t == "budget_spent":
                print(f"    [$] {t}: ${e['amount']:.2f} ({e['label']})")
            elif t == "studio_job_completed":
                print(f"    [+] {t}: spent ${e['spent_usd']:.2f}")
            else:
                print(f"    [ ] {t}")

        # Show Run B (bad path)
        print("\n  --- Run B (off the rails) ---")
        for e in run_b_events:
            t = e["type"]
            if t == "capability_checked":
                icon = "Y" if e["allowed"] else "N"
                print(f"    [{icon}] {t}: {e['capability']}")
            elif t == "capability_denied":
                print(f"    [!] {t}: {e['capability']} -- {e['reason']}")
            elif t == "budget_spent":
                print(f"    [$] {t}: ${e['amount']:.2f} ({e['label']})")
            elif t == "budget_exceeded":
                print(f"    [!] {t}: tried ${e['attempted_amount']:.2f}, "
                      f"already spent ${e['already_spent']:.2f}, "
                      f"budget ${e['budget_usd']:.2f}")
            elif t == "studio_job_failed":
                print(f"    [X] {t}: {e['error'][:60]}")
            else:
                print(f"    [ ] {t}")

        # Show parent's response
        print("\n  --- Parent response ---")
        for e in parent_events:
            t = e["type"]
            if t == "subagent_failed":
                print(f"    [!] {t}: {e['governance_violations']} violations "
                      f"({', '.join(e['violation_types'])})")
            elif t == "fallback_applied":
                print(f"    [>] {t}: {e['reason']}, using {e['using_result_from']}")
            elif t == "subagent_completed":
                print(f"    [+] {t}: {e['child_agent_id']} ({e['status']})")

        # Generate Mermaid for both runs
        for name, events in [("run_a", run_a_events), ("run_b", run_b_events)]:
            mermaid = render_trace(events, diagram_type="workflow")
            mermaid_path = output_dir / f"{name}_workflow.mmd"
            mermaid_path.write_text(mermaid, encoding="utf-8")
            print(f"\n  Mermaid ({name}): {mermaid_path}")

        if not (parent_ok and run_a_ok and run_b_ok):
            print("\n  FAIL: chain verification failed")
            return 1

        print("\n  PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
