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
import os
import stat
import threading
import time

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


def _mixed_worker(root: str, proc: int, n: int) -> None:
    store = ShardStore(root)
    for i in range(n):
        shard = _shard(f"mixed {proc} fact {i}")
        store.put(shard)
        if i % 2:
            store.delete(shard.shard_id)


class TestMixedAndThreaded:
    def test_mixed_put_delete_across_processes(self, tmp_path):
        ctx = mp.get_context("spawn")
        procs = [
            ctx.Process(target=_mixed_worker, args=(str(tmp_path), p, 20))
            for p in range(4)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=120)
            assert p.exitcode == 0

        store = ShardStore(tmp_path)
        assert store.count() == 4 * 10
        assert store.verify_index() == {"missing": [], "dangling": []}

    def test_threads_in_one_process(self, tmp_path):
        store = ShardStore(tmp_path)
        errors = []

        def work(t):
            try:
                for i in range(20):
                    store.put(_shard(f"thread {t} fact {i}"))
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(t,)) for t in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        assert not errors
        assert store.count() == 6 * 20
        assert store.verify_index() == {"missing": [], "dangling": []}

    def test_two_instances_same_root_do_not_deadlock(self, tmp_path):
        a, b = ShardStore(tmp_path), ShardStore(tmp_path)
        result = []

        def run():
            with a._index_lock():
                b.put(_shard("from b"))
                result.append(b.count())

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(timeout=10)
        assert not t.is_alive(), "deadlocked"
        assert result == [1]


class TestCorruptLoad:
    @pytest.mark.parametrize(
        "payload",
        [
            '{"project:a": ["é'.encode("utf-8")[:-1],  # torn mid UTF-8 character
            b"null",
            b"[]",
        ],
        ids=["torn-utf8", "null", "list"],
    )
    def test_corrupt_index_is_healed(self, tmp_path, payload):
        store = ShardStore(tmp_path)
        shard = _shard("a", scope="project:a")
        store.put(shard)
        store.index_path.write_bytes(payload)

        assert store.list_scopes() == ["project:a"]
        # A write path persists the healed index.
        store.put(_shard("b", scope="project:a"))
        assert store.count() == 2
        assert isinstance(json.loads(store.index_path.read_text("utf-8")), dict)

    def test_read_of_corrupt_index_does_not_write(self, tmp_path):
        store = ShardStore(tmp_path)
        store.put(_shard("a"))
        store.index_path.write_bytes(b"null")
        store._lock_path.unlink()

        assert store.count() == 1
        assert store.index_path.read_bytes() == b"null"
        assert not store._lock_path.exists()


class TestReindex:
    def test_valid_but_incomplete_index_is_healed(self, tmp_path):
        store = ShardStore(tmp_path)
        shards = [_shard(f"s{i}") for i in range(4)]
        for s in shards:
            store.put(s)
        kept = [s.shard_id for s in shards[:2]]
        store.index_path.write_text(
            json.dumps({"project:test": [kept[1], kept[0], "deadbeef"]}),
            encoding="utf-8",
        )
        report = store.verify_index()
        assert len(report["missing"]) == 2 and report["dangling"] == ["deadbeef"]

        result = store.reindex()
        assert sorted(result["added"]) == sorted(s.shard_id for s in shards[2:])
        assert result["removed"] == ["deadbeef"]
        ids = json.loads(store.index_path.read_text("utf-8"))["project:test"]
        assert ids[:2] == [kept[1], kept[0]]  # existing order preserved
        assert store.count() == 4
        assert store.verify_index() == {"missing": [], "dangling": []}

    def test_put_of_existing_unindexed_shard_indexes_it(self, tmp_path):
        store = ShardStore(tmp_path)
        shard = _shard("crashed before index")
        path = store._shard_path(shard.shard_id)
        path.parent.mkdir(parents=True)
        path.write_text(shard.to_json(), encoding="utf-8")

        store.put(shard)
        assert store.count() == 1


class TestDeleteAndModes:
    def test_delete_failure_keeps_shard_and_index(self, tmp_path, monkeypatch):
        store = ShardStore(tmp_path)
        shard = _shard("keep me")
        store.put(shard)

        def boom(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(store, "_save_index", boom)
        with pytest.raises(OSError):
            store.delete(shard.shard_id)
        monkeypatch.undo()

        assert store.has(shard.shard_id)
        assert store.count() == 1

    def test_delete_of_unindexed_shard_is_graceful(self, tmp_path):
        store = ShardStore(tmp_path)
        shard = _shard("x")
        store.put(shard)
        store.index_path.write_text("{}", encoding="utf-8")
        assert store.delete(shard.shard_id) is True
        assert not store.has(shard.shard_id)

    @pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
    def test_index_and_shard_modes_follow_umask(self, tmp_path):
        old = os.umask(0o022)
        try:
            store = ShardStore(tmp_path)
            shard = _shard("m")
            store.put(shard)
            assert stat.S_IMODE(store.index_path.stat().st_mode) == 0o644
            assert stat.S_IMODE(store._shard_path(shard.shard_id).stat().st_mode) == 0o644

            # An existing mode is preserved across rewrites.
            os.chmod(store.index_path, 0o664)
            store.put(_shard("m2"))
            assert stat.S_IMODE(store.index_path.stat().st_mode) == 0o664
        finally:
            os.umask(old)

    def test_stale_temp_files_are_swept_fresh_ones_kept(self, tmp_path):
        store = ShardStore(tmp_path)
        old = tmp_path / "index.json.1.deadbeef.tmp"
        fresh = tmp_path / "index.json.2.cafef00d.tmp"
        old.write_text("x")
        fresh.write_text("x")
        past = time.time() - store_mod._STALE_TMP_SECS - 60
        os.utime(old, (past, past))

        store.put(_shard("sweep"))
        assert not old.exists()
        assert fresh.exists()
