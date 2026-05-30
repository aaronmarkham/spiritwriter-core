"""ENTITY atoms — canonical entity records for resolution.

ENTITY atoms represent "this is the canonical form of X" — the
shape that drives `CanonicalRegistry.ess_fields` for downstream
entity resolution (see docs/entity-resolution.md for the resolver).

What this shows:
- ENTITY kind for canonical entity declarations
- Full (entity, key, value) — the defining fields the registry uses
- One atom per defining attribute; together they form the entity's
  canonical signature
- How the same `entity` string ties them together
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from spiritwriter.fabric.shard import AtomKind, DecayClass, MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore


def build() -> MemoryShard:
    canonical_id = "person:carlos-rodriguez-1985"
    return MemoryShard(
        atoms=[
            ShardAtom(
                text="Carlos Rodriguez",
                kind=AtomKind.ENTITY,
                entity=canonical_id, key="full_name", value="Carlos Rodriguez",
            ),
            ShardAtom(
                text="b. 1985-03-12",
                kind=AtomKind.ENTITY,
                entity=canonical_id, key="dob", value="1985-03-12",
            ),
            ShardAtom(
                text="Male",
                kind=AtomKind.ENTITY,
                entity=canonical_id, key="gender", value="M",
            ),
        ],
        scope="entity:person:canonical",
        origin="example:10_entity",
        decay_class=DecayClass.PERMANENT,
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored canonical entity shard {ref.shard_id[:12]}...")
        print(f"\nHydrated context:\n")
        print(store.hydrate([ref]))
        print("\nDownstream: CanonicalRegistry can be configured with")
        print("ess_fields = ['full_name', 'dob'] to deduplicate sightings")
        print("of this person across other sources. See docs/entity-resolution.md.")


if __name__ == "__main__":
    main()
