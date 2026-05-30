"""INSTRUCTION atom — base example (no delegation integration yet).

INSTRUCTION atoms encode "do X" / "constraint: Y" for a sub-agent.
This base example shows the shape; the next file
(09_instruction_delegation.py) closes the loop by packaging
instructions into a real job with content + entitlement + trace events.

What this shows:
- INSTRUCTION kind for delegation directives
- (entity, key, value) encodes which job, which constraint
- Difference from CONVENTION: CONVENTION is broad-and-permanent;
  INSTRUCTION is scoped-to-this-job
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
                text="Summarize in 3 paragraphs; no bullet lists.",
                kind=AtomKind.INSTRUCTION,
                entity="job-xyz",
                key="output.format",
                value="prose-only",
            ),
            ShardAtom(
                text="Cite each fact with [source_ref] markers.",
                kind=AtomKind.INSTRUCTION,
                entity="job-xyz",
                key="output.citations",
                value="inline",
            ),
        ],
        scope="job:xyz:task",
        origin="example:08_instruction",
        decay_class=DecayClass.ACTIVE,  # instructions outlive the job briefly for audit
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored instruction shard {ref.shard_id[:12]}... ({len(shard.atoms)} instructions)")
        print(f"\nHydrated context (what the sub-agent would see):\n")
        print(store.hydrate([ref]))
        print("\n(For the full delegation flow, see 09_instruction_delegation.py.)")


if __name__ == "__main__":
    main()
