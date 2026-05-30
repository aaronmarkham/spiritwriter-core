"""PREFERENCE atoms — structured user config.

PREFERENCE atoms hold user settings: display.theme, notification.email,
agent.tone, etc. The (entity, key, value) triple does the work;
`text` can simply repeat the value (or briefly describe it).

What this shows:
- PREFERENCE kind for user settings
- Scope is meaningful: `user:<id>:preferences` keeps it queryable per-user
- Multiple preferences for one user compose in one shard
- A consumer can query `entity = "aaron"` to get all of one user's prefs
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from spiritwriter.fabric.shard import AtomKind, MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore


def build() -> MemoryShard:
    return MemoryShard(
        atoms=[
            ShardAtom(
                text="dark",
                kind=AtomKind.PREFERENCE,
                entity="aaron", key="display.theme", value="dark",
            ),
            ShardAtom(
                text="weekly",
                kind=AtomKind.PREFERENCE,
                entity="aaron", key="notification.digest_frequency",
                value="weekly",
            ),
            ShardAtom(
                text="concise, technical",
                kind=AtomKind.PREFERENCE,
                entity="aaron", key="agent.response_style",
                value="concise-technical",
            ),
        ],
        scope="user:aaron:preferences",
        origin="example:03_preference",
    )


def main() -> None:
    shard = build()
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        ref = store.put(shard)
        print(f"Stored shard {ref.shard_id[:12]}... ({len(shard.atoms)} preferences)")
        print(f"\nHydrated context:\n")
        print(store.hydrate([ref]))


if __name__ == "__main__":
    main()
