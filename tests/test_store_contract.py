"""ShardStore contract: a shard_id is a content address, so re-putting it is a no-op."""

import time

from spiritwriter.fabric.shard import MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore


def _rebuilt(**overrides):
    fields = dict(
        atoms=[ShardAtom(text="contract", key="k", value="v")],
        scope="test:contract",
        origin="agent:test",
    )
    fields.update(overrides)
    return MemoryShard(**fields)


def test_reput_of_rebuilt_shard_is_a_noop(tmp_path):
    store = ShardStore(tmp_path / "store")
    first = _rebuilt()
    store.put(first)
    path = store._shard_path(first.shard_id)
    before = path.read_bytes()

    time.sleep(0.01)
    second = _rebuilt(meta={"different": True})
    assert second.shard_id == first.shard_id

    assert store.put(second).shard_id == first.shard_id
    assert path.read_bytes() == before
    assert store.get(first.shard_id).meta == first.meta


def test_reput_with_different_parent_keeps_one_shard(tmp_path):
    store = ShardStore(tmp_path / "store")
    first = _rebuilt()
    store.put(first)
    store.put(_rebuilt(parent_shard_id="0" * 64))
    assert [s.shard_id for s in store.by_scope("test:contract")] == [first.shard_id]
