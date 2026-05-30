"""CHECKPOINT atom + trace integration — closing the loop.

The base example (06_checkpoint.py) shows the shape of a CHECKPOINT
atom. This one shows the *whole point* of checkpoints: tie them to a
hash-chained trace event so resume can verify what happened before
picking up.

What this shows:
- TraceEmitter writes a trace event for "stage 3 complete"
- The CHECKPOINT atom's `source_ref` pins to that event via
  `emitter.current_trace_ref()` ("chain:<run_id>#<event_hash>")
- A resume reader can follow the source_ref back into the trace,
  verify the chain is intact, and continue from the verified point
- The pattern: trace events are the audit log; CHECKPOINT atoms are
  the queryable index into them

Cross-link: docs/tracing.md § "Cap Context and Provenance Queries"
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from spiritwriter.fabric.emitter import TraceEmitter, verify_chain
from spiritwriter.fabric.shard import AtomKind, DecayClass, MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore


def build(trace_path: Path | None = None) -> tuple[MemoryShard, list[dict]]:
    """Build the checkpoint shard + the trace events it pins to.

    If trace_path is given, events are appended there; otherwise a
    temp file is used (caller is expected to manage the temp dir).
    """
    if trace_path is None:
        raise ValueError("trace_path required — emitter writes JSONL there")

    emitter = TraceEmitter(
        run_id="run-abc-123",
        agent_id="pipeline-orchestrator",
        out_path=trace_path,
    )

    # Pretend we've just finished stage 3 of the pipeline.
    emitter.emit("stage_completed", stage=3, total_stages=5,
                 stage_name="transcript_generation")
    trace_ref = emitter.current_trace_ref()  # "chain:run-abc-123#<hash>"

    # Capture the checkpoint atom pinning that trace event.
    shard = MemoryShard(
        atoms=[
            ShardAtom(
                text="Completed stage 3 of 5 (transcript generated).",
                kind=AtomKind.CHECKPOINT,
                entity="run-abc-123",
                key="pipeline.stage",
                value="3",
                source_ref=trace_ref,
            ),
        ],
        scope="run:abc-123",
        origin="example:07_checkpoint_with_trace",
        decay_class=DecayClass.CHECKPOINT,
        trace_ref=trace_ref,  # also pinned at the shard level
    )

    return shard, emitter.get_events()


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        tmp_path = Path(tmp)
        trace_path = tmp_path / "trace.jsonl"

        shard, events = build(trace_path=trace_path)

        store = ShardStore(tmp_path / "shards")
        ref = store.put(shard)

        print(f"Stored checkpoint shard {ref.shard_id[:12]}...")
        print(f"\nTrace events written ({len(events)}):")
        for ev in events:
            print(f"  - {ev['type']}: hash={ev['hash'][:12]}... "
                  f"prev={ev.get('prev_event_hash') or '(first)'}")

        # Verify chain integrity (a resume reader would do this).
        print(f"\nChain intact: {verify_chain(events)}")

        # Show the source_ref linkage.
        atom = shard.atoms[0]
        print(f"\nCheckpoint atom source_ref: {atom.source_ref}")
        print(f"Shard-level trace_ref:      {shard.trace_ref}")
        print("\nA resume reader: parses source_ref to find the run_id +")
        print("event_hash, looks up the trace JSONL, verify_chain()s it,")
        print("and continues from the verified point.")


if __name__ == "__main__":
    main()
