"""FACT atoms — knowledge ingestion.

Atomizing a single sentence into multiple FACT atoms about an entity.
Each atom captures one (entity, key, value) triple. Shared `entity`
string lets downstream entity resolution dedupe them later.

What this shows:
- Multiple atoms in one shard, all FACT kind
- (entity, key, value) triple fully filled
- Optional `source_ref` pointing at the source document
- `text` carries the natural-language form; the triple carries the
  machine-queryable form
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from spiritwriter.fabric.shard import AtomKind, MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore


def build() -> MemoryShard:
    """Build the shard. Importable by the test suite."""
    return MemoryShard(
        atoms=[
            ShardAtom(
                text="The Fugaku supercomputer is in Kobe, Japan.",
                kind=AtomKind.FACT,
                entity="Fugaku",
                key="location",
                value="Kobe, Japan",
                source_ref="doi:10.1145/3458817.3476188",
            ),
            ShardAtom(
                text="Fugaku is operated by RIKEN.",
                kind=AtomKind.FACT,
                entity="Fugaku",
                key="operator",
                value="RIKEN",
                source_ref="doi:10.1145/3458817.3476188",
            ),
            ShardAtom(
                text="Fugaku ranks 4th on the TOP500 list (2025).",
                kind=AtomKind.FACT,
                entity="Fugaku",
                key="top500_rank_2025",
                value="4",
                source_ref="https://top500.org/lists/top500/2025/06/",
            ),
        ],
        scope="sw:hpc:fugaku",
        origin="example:01_fact",
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored shard {ref.shard_id[:12]}... with {len(shard.atoms)} atoms")
        print(f"\nHydrated context:\n")
        print(store.hydrate([ref]))


if __name__ == "__main__":
    main()
