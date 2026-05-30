"""Mixed-kind shard — composition example.

A real-world shard often contains multiple atom kinds for one entity:
facts about it, decisions made about it, conventions for it, ambient
context. This is normal, not exceptional.

What this shows:
- One shard, one entity, multiple AtomKinds
- How `scope` ties them together for retrieval
- The implicit relationship via shared `entity` field — no FK needed
- A hydrated view that gives an agent everything-about-the-project in
  one go
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
                text="Project Foo is a CLI tool for batch image processing.",
                kind=AtomKind.FACT,
                entity="myproject:foo", key="description",
                value="CLI image-processing tool",
            ),
            ShardAtom(
                text="FastAPI chosen for the REST gateway; click for CLI.",
                kind=AtomKind.DECISION,
                entity="myproject:foo", key="web_stack",
                value="fastapi+click",
            ),
            ShardAtom(
                text="Run pytest before every push; CI re-runs on PR.",
                kind=AtomKind.CONVENTION,
                entity="myproject:foo", key="testing.policy",
                value="pre-push-and-ci",
            ),
            ShardAtom(
                text="Active sprint is focused on the v0.3 release: "
                     "batch retry semantics and progress reporting.",
                kind=AtomKind.CONTEXT,
                entity="myproject:foo",
            ),
        ],
        scope="project:foo",
        origin="example:11_mixed_kind",
        decay_class=DecayClass.STABLE,
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored mixed-kind shard {ref.shard_id[:12]}... ({len(shard.atoms)} atoms)")
        kinds = sorted({a.kind.value for a in shard.atoms})
        print(f"Kinds present: {kinds}")
        print(f"\nHydrated context (the agent sees all kinds together):\n")
        print(store.hydrate([ref]))


if __name__ == "__main__":
    main()
