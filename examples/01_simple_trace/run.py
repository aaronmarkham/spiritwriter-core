#!/usr/bin/env python3
"""Demo 1: Simple trace — parent agent spawns one subagent via package_job.

Shows the core fabric plumbing:
  - ShardStore + ShardAtom basics
  - TraceEmitter with shard_created, spawn_with_shards, job_completed
  - Subagent emits its own child trace; parent references it by run_id
  - verify_chain() confirms integrity
  - render_trace() produces a Mermaid diagram

Usage:
    python examples/01_simple_trace/run.py
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
from spiritwriter.fabric.jobs import JobSpec, package_job
from spiritwriter.fabric.runner import (
    hydrate_job, BudgetTracker, create_result_shard,
)
from spiritwriter.fabric.visualize import render_trace


# ── The "subagent": a plain Python function, no LLM needed ─────────

def run_subagent(task_text: str, store: ShardStore, trace_path: str) -> MemoryShard:
    """Simulate a subagent that receives a packaged job and returns a result."""
    child_tracer = TraceEmitter(
        run_id="child-run-001",
        agent_id="summarizer",
        out_path=trace_path,
    )

    # Hydrate the job (validates entitlement, decrypts shards)
    job = hydrate_job(store, task_text, tracer=child_tracer)

    # Track spending
    tracker = BudgetTracker(
        budget_usd=job.budget_usd,
        token_id=job.token.token_id,
        tracer=child_tracer,
    )

    # Do the "work" — in a real agent this would be an LLM call
    tracker.record("summarize_document", 0.03)
    summary = (
        "The document describes a distributed memory system for AI agents "
        "using content-addressed shards, hash-chained provenance, and "
        "scoped entitlements for access control."
    )

    # Build result shard
    result = create_result_shard(
        job=job,
        results={
            "budget": tracker.summary(),
            "outputs": [{"type": "summary", "ref": "inline"}],
        },
        agent_id="summarizer",
    )
    result.atoms.append(ShardAtom(
        text=summary,
        kind=AtomKind.FACT,
        key="document_summary",
    ))

    # Store it and emit completion
    store.put(result)
    child_tracer.shard_created(
        shard_id=result.shard_id,
        scope=result.scope,
        atom_count=len(result.atoms),
    )
    child_tracer.job_completed(
        token_id=job.token.token_id,
        result_shard_id=result.shard_id,
        spent_usd=tracker.spent,
    )

    return result


# ── Main: parent agent orchestrates the job ─────────────────────────

def main(output_dir: Path | None = None) -> int:
    if output_dir is None:
        output_dir = Path(__file__).parent / "traces"
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        store = ShardStore(td)
        parent_trace = str(output_dir / "parent.jsonl")
        child_trace = str(output_dir / "child.jsonl")

        # Clear stale traces
        for p in [parent_trace, child_trace]:
            Path(p).unlink(missing_ok=True)

        parent = TraceEmitter(
            run_id="parent-run-001",
            agent_id="orchestrator",
            out_path=parent_trace,
        )

        # Step 1: create a request shard
        request_shard = MemoryShard(
            atoms=[
                ShardAtom(
                    text="Summarize the attached research paper on agent memory systems.",
                    kind=AtomKind.INSTRUCTION,
                    key="task",
                ),
                ShardAtom(
                    text="The paper proposes content-addressed shards as the unit of "
                         "distributable agent memory, with hash-chained provenance.",
                    kind=AtomKind.CONTEXT,
                    key="document_excerpt",
                ),
            ],
            scope="demo:request",
            origin="orchestrator",
            decay_class=DecayClass.SESSION,
            tags=["demo-request"],
        )
        store.put(request_shard)
        parent.shard_created(
            shard_id=request_shard.shard_id,
            scope=request_shard.scope,
            atom_count=len(request_shard.atoms),
        )

        # Step 2: package the job with entitlements
        pkg = package_job(
            store=store,
            content_atoms=request_shard.atoms,
            job_spec=JobSpec(
                prompt="Summarize this document in 2-3 sentences.",
                budget_usd=0.50,
            ),
            agent_id="orchestrator",
            granted_to="summarizer",
            scope_prefix="demo",
            tracer=parent,
        )

        # Step 3: spawn the subagent (recorded in trace)
        parent.spawn_with_shards(
            child_agent_id="summarizer",
            shard_refs=[
                {"shard_id": pkg.content_shard_id, "scope": "demo:content"},
                {"shard_id": pkg.task_shard_id, "scope": "demo:task"},
            ],
            task="Summarize document",
            child_run_id="child-run-001",
        )

        # Step 4: subagent runs (returns a result shard)
        result = run_subagent(pkg.spawn_task_text(), store, child_trace)

        # Step 5: parent records the result
        parent.emit(
            "subagent_completed",
            child_agent_id="summarizer",
            child_run_id="child-run-001",
            result_shard_id=result.shard_id,
        )
        parent.shard_resolved(
            shard_id=result.shard_id,
            by_agent="orchestrator",
        )

        # ── Verify both chains ──
        parent_events = parent.get_events()
        child_events = TraceEmitter(
            run_id="", agent_id="", out_path=child_trace,
        ).get_events()

        parent_ok = verify_chain(parent_events)
        child_ok = verify_chain(child_events)

        print("== Demo 1: Simple Trace ==\n")
        print(f"  Parent trace: {len(parent_events)} events, chain valid: {parent_ok}")
        print(f"  Child trace:  {len(child_events)} events, chain valid: {child_ok}")

        # Show event types
        print("\n  Parent events:")
        for e in parent_events:
            print(f"    {e['type']}")
        print("\n  Child events:")
        for e in child_events:
            print(f"    {e['type']}")

        # Generate Mermaid visualization
        mermaid = render_trace(parent_events, diagram_type="workflow")
        mermaid_path = output_dir / "workflow.mmd"
        mermaid_path.write_text(mermaid, encoding="utf-8")
        print(f"\n  Mermaid diagram: {mermaid_path}")

        # Verify the result shard is retrievable
        retrieved = store.get(result.shard_id)
        summary_atom = retrieved.get_atom("document_summary")
        print(f"\n  Result shard: {result.shard_id[:16]}...")
        print(f"  Summary: {summary_atom.text[:80]}...")

        if not (parent_ok and child_ok):
            print("\n  FAIL: chain verification failed")
            return 1

        print("\n  PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
