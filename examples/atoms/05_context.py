"""CONTEXT atoms — free-form context for prompts.

CONTEXT is the catch-all kind for "here's what you need to know."
Like CONVENTION, it usually doesn't need (entity, key, value) —
the `text` is the whole thing. Use CONTEXT when the content is
ambient knowledge an agent should have, not a rule it must follow.

What this shows:
- CONTEXT kind for prompt-engineering use cases
- ONLY `text` filled — no triple structure
- How it composes through ShardStore.hydrate() into XML-tagged
  context the agent sees as a prompt prefix
- Difference from CONVENTION (rule) vs CONTEXT (background info)
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
                text="The user is a senior Python engineer working on "
                     "infrastructure tooling. Prefers terse, "
                     "implementation-detail answers over conceptual overviews.",
                kind=AtomKind.CONTEXT,
            ),
            ShardAtom(
                text="Current project is a CLI tool that wraps a REST API; "
                     "uses click for arg parsing and rich for output.",
                kind=AtomKind.CONTEXT,
            ),
        ],
        scope="agent:current-session:context",
        origin="example:05_context",
        decay_class=DecayClass.SESSION,  # ambient context expires quickly
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored shard {ref.shard_id[:12]}... ({len(shard.atoms)} context atoms)")
        print(f"\nHydrated context (this is what an LLM would receive):\n")
        print(store.hydrate([ref]))


if __name__ == "__main__":
    main()
