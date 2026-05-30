"""CONVENTION atoms — behavioral rules with no triple structure.

The "always X" / "never Y" pattern. CONVENTION atoms typically don't
need (entity, key, value) — the `text` is the whole rule. This is
the most-missed minimal shape in the docs.

What this shows:
- CONVENTION kind for project-wide rules
- ONLY `text` filled — no entity/key/value at all
- A shard of conventions composes naturally; each is one bullet
- How this differs from INSTRUCTION (which is scoped to a specific
  job/delegation): CONVENTION applies broadly, lives long
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
                text="Always run migrations before deploy.",
                kind=AtomKind.CONVENTION,
            ),
            ShardAtom(
                text="Never commit secrets — use keychain or env vars.",
                kind=AtomKind.CONVENTION,
            ),
            ShardAtom(
                text="All new modules ship with type hints and a docstring.",
                kind=AtomKind.CONVENTION,
            ),
        ],
        scope="project:myproject:conventions",
        origin="example:04_convention",
        decay_class=DecayClass.PERMANENT,
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored shard {ref.shard_id[:12]}... ({len(shard.atoms)} conventions)")
        print(f"\nHydrated context:\n")
        print(store.hydrate([ref]))


if __name__ == "__main__":
    main()
