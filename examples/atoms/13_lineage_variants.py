"""Parent atom with child variants — the zeitghost bias-news pattern.

One canonical article + multiple bias-rewritten variants. All variants
share the same `scope` and `entity` (the article's URL hash), and link
back to the parent shard via `parent_shard_id`. A consumer reads the
parent and any variant; the lineage chain is reconstructible for audit.

What this shows:
- The "same entity, multiple expressions" pattern
- `parent_shard_id` as the schema-level way to express "this is a
  derivation of that" — no inter-atom FK needed
- Same scope + entity across all variants → downstream
  CanonicalRegistry would treat them as one canonical entity
- How a bias-slider consumer picks a variant by reading the
  `bias_variant` ENTITY atom
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from spiritwriter.fabric.shard import AtomKind, DecayClass, MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore


def _article_entity(url: str) -> str:
    """Stable per-article entity key for cross-variant linkage."""
    return f"article:{hashlib.sha256(url.encode()).hexdigest()[:16]}"


def build_parent(url: str, headline: str, body: str) -> MemoryShard:
    entity = _article_entity(url)
    return MemoryShard(
        atoms=[
            ShardAtom(text=headline, kind=AtomKind.FACT,
                      entity=entity, key="headline", value=headline),
            ShardAtom(text=body, kind=AtomKind.FACT,
                      entity=entity, key="body", value=body),
            ShardAtom(text=url, kind=AtomKind.FACT,
                      entity=entity, key="url", value=url),
        ],
        scope="sw:article:zeitghost",
        origin="zeitghost-ingest",
        decay_class=DecayClass.STABLE,
    )


def build_variant(parent: MemoryShard, url: str,
                  bias_label: str, rewritten_body: str) -> MemoryShard:
    """A bias-rewritten child of the parent article."""
    entity = _article_entity(url)
    return MemoryShard(
        atoms=[
            ShardAtom(text=rewritten_body, kind=AtomKind.FACT,
                      entity=entity, key="body", value=rewritten_body),
            ShardAtom(text=bias_label, kind=AtomKind.ENTITY,
                      entity=entity, key="bias_variant", value=bias_label),
        ],
        scope="sw:article:zeitghost",
        origin="zeitghost-bias-rewriter",
        parent_shard_id=parent.shard_id,
        decay_class=DecayClass.STABLE,
    )


def build() -> list[MemoryShard]:
    """Build parent + 2 variants. Returns the full lineage as a list."""
    url = "https://example.com/news/2026-05-30/policy-vote"
    parent = build_parent(
        url=url,
        headline="Council passes new policy",
        body="The council voted 7-2 to adopt the new policy after debate.",
    )
    left = build_variant(parent, url, "left",
        "After months of grassroots organizing, the council adopted "
        "a progressive new policy by a 7-2 vote.")
    right = build_variant(parent, url, "right",
        "Despite opposition, the council pushed through a controversial "
        "new policy on a 7-2 vote.")
    return [parent, left, right]


def main() -> None:
    shards = build()
    parent = shards[0]
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        store = ShardStore(Path(tmp))
        refs = [store.put(s) for s in shards]
        print(f"Stored {len(shards)} shards (1 parent + {len(shards)-1} variants):")
        for s, r in zip(shards, refs):
            parent_ref = f" (parent={s.parent_shard_id[:12]}...)" if s.parent_shard_id else ""
            print(f"  - {r.shard_id[:12]}... · origin={s.origin}{parent_ref}")
        print(f"\nAll share scope: {parent.scope}")
        print(f"All share entity: {parent.atoms[0].entity}")
        print(f"\nHydrated parent + left variant (what a left-slider consumer sees):\n")
        print(store.hydrate([refs[0], refs[1]]))


if __name__ == "__main__":
    main()
