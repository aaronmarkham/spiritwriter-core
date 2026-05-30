"""DECISION atoms — choice + rationale.

DECISION atoms capture "we chose X because Y." The `text` field
carries the rationale prose; the (entity, key, value) triple captures
the decision itself in machine-queryable form.

What this shows:
- DECISION kind, one atom per choice
- Rationale in `text`, structured choice in (entity, key, value)
- Multiple decisions about the same project compose in one shard
- Lineage: a follow-up shard can supersede this one via parent_shard_id
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
                text="PostgreSQL chosen over SQLite for concurrent writes; "
                     "ACID guarantees needed under multi-worker ingest.",
                kind=AtomKind.DECISION,
                entity="myproject",
                key="database",
                value="postgresql",
            ),
            ShardAtom(
                text="FastAPI chosen over Flask for first-class async support "
                     "and built-in OpenAPI schema generation.",
                kind=AtomKind.DECISION,
                entity="myproject",
                key="web_framework",
                value="fastapi",
            ),
        ],
        scope="project:myproject",
        origin="example:02_decision",
        decay_class=DecayClass.PERMANENT,  # decisions are load-bearing
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored shard {ref.shard_id[:12]}... ({len(shard.atoms)} decisions)")
        print(f"\nHydrated context:\n")
        print(store.hydrate([ref]))


if __name__ == "__main__":
    main()
