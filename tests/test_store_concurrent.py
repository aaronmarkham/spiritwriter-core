"""Tests for ShardStore index durability under concurrent writers.

Parallel processes (e.g. one per agent hook) share one store root. Without
an atomic write and a cross-process lock, the index loses updates and can be
torn into valid JSON followed by leftover bytes.

  - many spawned processes `put` distinct shards; none may be lost
  - a failure while saving leaves the previous index intact
  - a corrupt index is rebuilt from the shard files
"""

from __future__ import annotations

import json
import multiprocessing as mp

import pytest

from spiritwriter.fabric import store as store_mod
from spiritwriter.fabric.shard import AtomKind, MemoryShard, ShardAtom
from spiritwriter.fabric.store import ShardStore

N_PROCS = 8
PER_PROC = 25


def _shard(text: str, scope: str = "project:test") -> MemoryShard:
    return MemoryShard(
        atoms=[ShardAtom(text=text, kind=AtomKind.FACT)],
        scope=scope,
        origin="agent:test",
    )


def _worker(root: str, proc: int, n: int) -> None:
    store = ShardStore(root)
    for i in range(n):
        store.put(_shard(f"proc {proc} fact {i}", scope=f"project:p{proc % 3}"))


class TestConcurrentPut:
    def test_many_processes_lose_no_shards(self, tmp_path):
        ctx = mp.get_context("spawn")
        procs = [
            ctx.Process(target=_worker, args=(str(tmp_path), p, PER_PROC))
            for p in range(N_PROCS)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=120)
            assert p.exitcode == 0

        store = ShardStore(tmp_path)
        index = json.loads(store.index_path.read_text(encoding="utf-8"))
        indexed = {sid for sids in index.values() for sid in sids}
        assert store.count() == N_PROCS * PER_PROC
        assert len(indexed) == N_PROCS * PER_PROC
        assert indexed == {s.shard_id for s in store.iter_all()}


class TestAtomicSave:
    def test_failed_dump_keeps_old_index(self, tmp_path, monkeypatch):
        store = ShardStore(tmp_path)
        store.put(_shard("first"))
        before = store.index_path.read_bytes()

        def boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(store_mod.json, "dumps", boom)
        with pytest.raises(RuntimeError):
            store.put(_shard("second"))
        monkeypatch.undo()

        assert store.index_path.read_bytes() == before
        assert store.count() == 1
        assert [p.name for p in tmp_path.glob("index.json.*.tmp")] == []

    def test_failed_replace_keeps_old_index_and_cleans_temp(self, tmp_path, monkeypatch):
        store = ShardStore(tmp_path)
        store.put(_shard("first"))
        before = store.index_path.read_bytes()

        def boom(*args, **kwargs):
            raise OSError("replace failed")

        monkeypatch.setattr(store_mod.os, "replace", boom)
        with pytest.raises(OSError):
            store.put(_shard("second"))
        monkeypatch.undo()

        assert store.index_path.read_bytes() == before
        assert [p.name for p in tmp_path.glob("index.json.*.tmp")] == []


class TestIndexRecovery:
    def test_torn_index_is_rebuilt_from_shards(self, tmp_path, caplog):
        store = ShardStore(tmp_path)
        a = _shard("a", scope="project:a")
        b = _shard("b", scope="project:b")
        store.put(a)
        store.put(b)
        # Valid JSON followed by leftover bytes: the "Extra data" shape.
        store.index_path.write_text(
            store.index_path.read_text(encoding="utf-8") + "\n  ]\n}",
            encoding="utf-8",
        )

        with caplog.at_level("WARNING", logger=store_mod.logger.name):
            assert sorted(store.list_scopes()) == ["project:a", "project:b"]

        assert "rebuilding" in caplog.text
        assert store.count() == 2
        assert [s.shard_id for s in store.by_scope("project:a")] == [a.shard_id]

    def test_reentrant_lock(self, tmp_path):
        store = ShardStore(tmp_path)
        with store._index_lock():
            with store._index_lock():
                store.put(_shard("nested"))
        assert store.count() == 1
