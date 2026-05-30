"""The absolute-minimum atom — just `text`.

Worth documenting explicitly because users often think they need to
fill every field. `ShardAtom(text="...")` is a complete, valid atom.
Default kind is CONTEXT.

What this shows:
- The smallest possible atom
- Default kind = CONTEXT (defined by ShardAtom dataclass defaults)
- A shard with one minimal atom still has a valid shard_id and
  hydrates cleanly — no field bookkeeping required
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from spiritwriter.fabric.shard import MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore


def build() -> MemoryShard:
    return MemoryShard(
        atoms=[
            ShardAtom(text="The thing to remember."),
        ],
        scope="example:minimal",
        origin="example:12_minimal",
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        atom = shard.atoms[0]
        print(f"Stored minimal shard {ref.shard_id[:12]}...")
        print(f"Atom kind (defaulted): {atom.kind.value}")
        print(f"Atom entity/key/value: {atom.entity}/{atom.key}/{atom.value}")
        print(f"\nHydrated context:\n")
        print(store.hydrate([ref]))


if __name__ == "__main__":
    main()
