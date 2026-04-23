#!/usr/bin/env python3
"""Demo 2: Todo fan-out — parent splits a compound request into parallel subtasks.

Shows:
  - Multi-child fan-out with distinct run_ids
  - Content-addressing: result atoms carry source_ref for lineage
  - Tree visualization: request -> todos -> N spawns -> N results -> assembly
  - ShardStore.get() to hydrate referenced shards from disk

Usage:
    python examples/02_todo_fanout/run.py
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
    hydrate_job, BudgetTracker, create_result_shard,
)
from spiritwriter.fabric.visualize import render_trace


INPUT_DOC = Path(__file__).parent / "input.md"

# The four sections we'll fan out to subagents
TODOS = [
    ("summarize_section_a", "Summarize Section A: Content Addressing"),
    ("summarize_section_b", "Summarize Section B: Provenance Tracking"),
    ("extract_entities_c", "Extract key entities from Section C: Access Control"),
    ("extract_entities_d", "Extract key entities from Section D: Entity Resolution"),
]

# Canned "subagent" outputs (no LLM needed)
MOCK_RESULTS = {
    "summarize_section_a": "Content-addressed storage uses SHA-256 hashes as IDs. "
        "Same content = same address, providing built-in deduplication and integrity.",
    "summarize_section_b": "Hash-chained event logs create tamper-evident audit trails. "
        "Each event links to the previous via hash, making insertion or deletion detectable.",
    "extract_entities_c": "Entities: EntitlementToken, EncryptedShard, Capability "
        "(read/write/execute), BudgetCeiling, SubAgent.",
    "extract_entities_d": "Entities: CanonicalIdentity, EntitySenseSig, "
        "ResolutionTier (T1-T4), FuzzyScore, CrossDocumentMention.",
}


def run_subagent(
    task_text: str,
    store: ShardStore,
    trace_path: str,
    todo_key: str,
    todo_atom_hash: str,
    run_id: str,
) -> MemoryShard:
    """Simulate a subagent processing one todo item."""
    child = TraceEmitter(run_id=run_id, agent_id=f"worker-{todo_key}", out_path=trace_path)
    job = hydrate_job(store, task_text, tracer=child)

    tracker = BudgetTracker(
        budget_usd=job.budget_usd,
        token_id=job.token.token_id,
        tracer=child,
    )
    tracker.record(todo_key, 0.02)

    result = create_result_shard(
        job=job,
        results={
            "budget": tracker.summary(),
            "outputs": [{"type": "text", "ref": "inline"}],
        },
        agent_id=f"worker-{todo_key}",
    )
    # Add the actual result with source_ref pointing back to the todo atom
    result.atoms.append(ShardAtom(
        text=MOCK_RESULTS[todo_key],
        kind=AtomKind.FACT,
        key=f"result.{todo_key}",
        source_ref=todo_atom_hash,
    ))
    store.put(result)

    child.shard_created(
        shard_id=result.shard_id,
        scope=result.scope,
        atom_count=len(result.atoms),
    )
    child.studio_job_completed(
        token_id=job.token.token_id,
        result_shard_id=result.shard_id,
        spent_usd=tracker.spent,
    )

    return result


def main(output_dir: Path | None = None) -> int:
    if output_dir is None:
        output_dir = Path(__file__).parent / "traces"
    output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        store = ShardStore(td)
        parent_trace = str(output_dir / "parent.jsonl")
        Path(parent_trace).unlink(missing_ok=True)

        parent = TraceEmitter(
            run_id="fanout-parent-001",
            agent_id="orchestrator",
            out_path=parent_trace,
        )

        # ── Step 1: Read input document ──
        doc_text = INPUT_DOC.read_text(encoding="utf-8")

        # ── Step 2: Create the todo-list shard ──
        todo_atoms = []
        for key, description in TODOS:
            todo_atoms.append(ShardAtom(
                text=description,
                kind=AtomKind.INSTRUCTION,
                key=key,
                entity="todo",
            ))

        todo_shard = MemoryShard(
            atoms=todo_atoms,
            scope="demo:todos",
            origin="orchestrator",
            decay_class=DecayClass.SESSION,
            tags=["todo-list"],
        )
        store.put(todo_shard)
        parent.shard_created(
            shard_id=todo_shard.shard_id,
            scope=todo_shard.scope,
            atom_count=len(todo_shard.atoms),
        )

        # ── Step 3: Fan out — one subagent per todo ──
        result_shards = []

        for i, (key, description) in enumerate(TODOS):
            child_trace = str(output_dir / f"child_{key}.jsonl")
            Path(child_trace).unlink(missing_ok=True)

            # Package a job with the document content
            content_atoms = [
                ShardAtom(text=doc_text, kind=AtomKind.CONTEXT, key="document"),
            ]

            pkg = package_job(
                store=store,
                content_atoms=content_atoms,
                job_spec=StudioJobSpec(
                    prompt=description,
                    budget_usd=0.25,
                ),
                agent_id="orchestrator",
                granted_to=f"worker-{key}",
                scope_prefix=f"demo-{key}",
                tracer=parent,
            )

            # Record the spawn
            todo_atom = todo_shard.atoms[i]
            parent.spawn_with_shards(
                child_agent_id=f"worker-{key}",
                shard_refs=[
                    {"shard_id": pkg.content_shard_id, "scope": f"demo-{key}:content"},
                    {"shard_id": pkg.task_shard_id, "scope": f"demo-{key}:task"},
                ],
                task=description,
                child_run_id=f"child-{key}",
                todo_shard_id=todo_shard.shard_id,
                todo_atom_key=key,
            )

            # Run the subagent
            result = run_subagent(
                task_text=pkg.spawn_task_text(),
                store=store,
                trace_path=child_trace,
                todo_key=key,
                todo_atom_hash=todo_atom.content_hash,
                run_id=f"child-{key}",
            )
            result_shards.append(result)

            parent.emit(
                "subagent_completed",
                child_agent_id=f"worker-{key}",
                child_run_id=f"child-{key}",
                result_shard_id=result.shard_id,
            )

        # ── Step 4: Assemble — collect all results into one shard ──
        assembly_atoms = []
        for result in result_shards:
            for atom in result.atoms:
                if atom.key and atom.key.startswith("result."):
                    assembly_atoms.append(ShardAtom(
                        text=atom.text,
                        kind=AtomKind.FACT,
                        key=atom.key,
                        source_ref=result.shard_id,
                    ))

        assembly_shard = MemoryShard(
            atoms=assembly_atoms,
            scope="demo:assembly",
            origin="orchestrator",
            decay_class=DecayClass.STABLE,
            tags=["assembly"],
            meta={
                "todo_shard_id": todo_shard.shard_id,
                "result_shard_ids": [r.shard_id for r in result_shards],
            },
        )
        store.put(assembly_shard)
        parent.shard_created(
            shard_id=assembly_shard.shard_id,
            scope=assembly_shard.scope,
            atom_count=len(assembly_shard.atoms),
        )

        # ── Verify ──
        parent_events = parent.get_events()
        parent_ok = verify_chain(parent_events)

        child_ok_all = True
        for key, _ in TODOS:
            child_trace = str(output_dir / f"child_{key}.jsonl")
            events = TraceEmitter(run_id="", agent_id="", out_path=child_trace).get_events()
            if not verify_chain(events):
                child_ok_all = False

        print("== Demo 2: Todo Fan-Out ==\n")
        print(f"  Parent trace: {len(parent_events)} events, chain valid: {parent_ok}")
        print(f"  Child traces: all valid: {child_ok_all}")

        print(f"\n  Todo shard: {todo_shard.shard_id[:16]}... ({len(todo_shard.atoms)} todos)")
        for i, r in enumerate(result_shards):
            print(f"  Result {i+1}:    {r.shard_id[:16]}...")
        print(f"  Assembly:    {assembly_shard.shard_id[:16]}... ({len(assembly_shard.atoms)} atoms)")

        # Show lineage: assembly atoms trace back to result shards
        print("\n  Lineage (assembly atom -> source shard):")
        for atom in assembly_shard.atoms:
            src = atom.source_ref[:16] if atom.source_ref else "?"
            print(f"    {atom.key}: -> {src}...")

        # Hydrate the assembly to show what a downstream agent would see
        retrieved = store.get(assembly_shard.shard_id)
        print(f"\n  Hydrated context:\n{retrieved.hydrate_context()}")

        # Mermaid
        mermaid = render_trace(parent_events, diagram_type="workflow")
        mermaid_path = output_dir / "workflow.mmd"
        mermaid_path.write_text(mermaid, encoding="utf-8")
        print(f"\n  Mermaid diagram: {mermaid_path}")

        if not (parent_ok and child_ok_all):
            print("\n  FAIL")
            return 1

        print("\n  PASS")
        return 0


if __name__ == "__main__":
    sys.exit(main())
