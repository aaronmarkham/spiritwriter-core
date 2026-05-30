"""CHECKPOINT atom — base example (no trace integration yet).

CHECKPOINT atoms mark "agent reached step N" — the resume-point
pattern. This base example shows the shape; the next file
(07_checkpoint_with_trace.py) closes the loop by pinning the
checkpoint to a hash-chained trace event so resume can verify what
happened before it picked up.

What this shows:
- CHECKPOINT kind for pipeline progress
- (entity, key, value) encodes which run, which stage
- `source_ref` is a placeholder here; the next example fills it
  with a real trace_ref
- DecayClass.CHECKPOINT (4-hour TTL) is the natural fit
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from spiritwriter.fabric.shard import AtomKind, DecayClass, MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore


def build() -> MemoryShard:
    return MemoryShard(
        atoms=[
            ShardAtom(
                text="Completed stage 3 of 5 (transcript generated).",
                kind=AtomKind.CHECKPOINT,
                entity="run-abc-123",
                key="pipeline.stage",
                value="3",
                # source_ref omitted here; 07_checkpoint_with_trace
                # shows how to wire this to a real trace event.
            ),
        ],
        scope="run:abc-123",
        origin="example:06_checkpoint",
        decay_class=DecayClass.CHECKPOINT,
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored checkpoint shard {ref.shard_id[:12]}...")
        print(f"\nHydrated context:\n")
        print(store.hydrate([ref]))
        print("\n(For the trace-coupled version, see 07_checkpoint_with_trace.py.)")


if __name__ == "__main__":
    main()
