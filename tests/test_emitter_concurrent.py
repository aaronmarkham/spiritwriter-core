"""Tests for multi-writer trace files and live following.

Covers the pieces a live consumer (e.g. a visualizer fed by per-event
agent hooks) needs on top of the single-writer emitter:

  - TraceEmitter(concurrent=True): many processes / instances append to
    one file and it stays a single verifiable chain
  - a concurrent emitter continues an existing file's chain
  - torn or hashless final lines refuse to extend (TraceChainError)
  - read_events_since / follow_events: byte-offset tailing that never
    consumes a partial line and yields resumable offsets
  - ChainVerifier: incremental verification, including mid-file resume
"""

from __future__ import annotations

import json
import multiprocessing as mp
import threading

import pytest

from spiritwriter.fabric.emitter import (
    ChainVerifier,
    TraceChainError,
    TraceEmitter,
    fcntl,
    follow_events,
    read_events_since,
    verify_chain,
)

posix_only = pytest.mark.skipif(fcntl is None, reason="concurrent mode needs POSIX fcntl")


def _load(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def _worker(path: str, agent_id: str, n: int) -> None:
    # A fresh emitter per event mirrors one-process-per-hook-event.
    for i in range(n):
        TraceEmitter("run-1", agent_id, path, concurrent=True).emit("tool_call", seq=i)


# === Concurrent writers =================================================


@posix_only
class TestConcurrentEmit:
    def test_many_processes_one_valid_chain(self, tmp_path):
        path = str(tmp_path / "trace.jsonl")
        ctx = mp.get_context("fork")
        procs = [ctx.Process(target=_worker, args=(path, f"agent-{p}", 25)) for p in range(6)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
            assert p.exitcode == 0
        events = _load(path)
        assert len(events) == 150
        assert verify_chain(events)
        assert {e["agent_id"] for e in events} == {f"agent-{p}" for p in range(6)}

    def test_interleaved_instances_chain(self, tmp_path):
        path = str(tmp_path / "trace.jsonl")
        a = TraceEmitter("run-1", "a", path, concurrent=True)
        b = TraceEmitter("run-1", "b", path, concurrent=True)
        for _ in range(3):
            a.emit("x")
            b.emit("y")
        events = _load(path)
        assert verify_chain(events)
        assert [e["agent_id"] for e in events] == ["a", "b"] * 3
        assert b.prev_hash == events[-1]["hash"]

    def test_default_mode_still_forks_with_two_instances(self, tmp_path):
        """Documents why concurrent mode exists: two in-memory heads on one file break the chain."""
        path = str(tmp_path / "trace.jsonl")
        a = TraceEmitter("run-1", "a", path)
        b = TraceEmitter("run-1", "b", path)
        a.emit("x")
        b.emit("y")
        assert not verify_chain(_load(path))

    def test_continues_existing_chain(self, tmp_path):
        path = str(tmp_path / "trace.jsonl")
        TraceEmitter("run-1", "a", path).emit("start")
        TraceEmitter("run-1", "b", path, concurrent=True).emit("next")
        assert verify_chain(_load(path))

    def test_event_shape_matches_default_mode(self, tmp_path):
        plain = TraceEmitter("r", "a", str(tmp_path / "p.jsonl")).emit("x", k=1)
        conc = TraceEmitter("r", "a", str(tmp_path / "c.jsonl"), concurrent=True).emit("x", k=1)
        assert set(plain) == set(conc)

    def test_torn_tail_refuses_to_extend(self, tmp_path):
        path = tmp_path / "trace.jsonl"
        TraceEmitter("r", "a", str(path)).emit("x")
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"type": "half')
        with pytest.raises(TraceChainError, match="incomplete"):
            TraceEmitter("r", "a", str(path), concurrent=True).emit("y")

    def test_hashless_tail_refuses_to_extend(self, tmp_path):
        path = tmp_path / "trace.jsonl"
        path.write_text('{"type": "x"}\n', encoding="utf-8")
        with pytest.raises(TraceChainError, match="not a hashed"):
            TraceEmitter("r", "a", str(path), concurrent=True).emit("y")

    def test_long_last_line(self, tmp_path):
        """The backwards scan must cross several read chunks."""
        path = str(tmp_path / "trace.jsonl")
        e = TraceEmitter("r", "a", path, concurrent=True)
        e.emit("small")
        e.emit("big", blob="z" * 20_000)
        e.emit("after")
        assert verify_chain(_load(path))


def test_concurrent_unavailable_without_fcntl(tmp_path, monkeypatch):
    import spiritwriter.fabric.emitter as em

    monkeypatch.setattr(em, "fcntl", None)
    with pytest.raises(NotImplementedError):
        em.TraceEmitter("r", "a", str(tmp_path / "t.jsonl"), concurrent=True)


# === Following ==========================================================


class TestReadEventsSince:
    def test_missing_file_reads_empty(self, tmp_path):
        assert read_events_since(str(tmp_path / "nope.jsonl")) == ([], 0)

    def test_incremental_reads(self, tmp_path):
        path = str(tmp_path / "t.jsonl")
        e = TraceEmitter("r", "a", path)
        e.emit("one")
        events, off = read_events_since(path)
        assert [x["type"] for x in events] == ["one"]
        e.emit("two")
        e.emit("three")
        events, off2 = read_events_since(path, off)
        assert [x["type"] for x in events] == ["two", "three"]
        assert read_events_since(path, off2) == ([], off2)

    def test_partial_line_left_unconsumed(self, tmp_path):
        path = tmp_path / "t.jsonl"
        TraceEmitter("r", "a", str(path)).emit("one")
        with open(path, "a", encoding="utf-8") as f:
            f.write('{"type": "tw')
        events, off = read_events_since(str(path))
        assert len(events) == 1
        with open(path, "a", encoding="utf-8") as f:
            f.write('o"}\n')
        events, _ = read_events_since(str(path), off)
        assert events == [{"type": "two"}]

    def test_shrunk_file_raises(self, tmp_path):
        path = tmp_path / "t.jsonl"
        TraceEmitter("r", "a", str(path)).emit("one")
        _, off = read_events_since(str(path))
        path.write_text("", encoding="utf-8")
        with pytest.raises(TraceChainError, match="shrank"):
            read_events_since(str(path), off)

    def test_vanished_file_raises(self, tmp_path):
        path = tmp_path / "t.jsonl"
        TraceEmitter("r", "a", str(path)).emit("one")
        _, off = read_events_since(str(path))
        path.unlink()
        with pytest.raises(TraceChainError, match="disappeared"):
            read_events_since(str(path), off)


class TestFollowEvents:
    def test_yields_resumable_offsets(self, tmp_path):
        path = str(tmp_path / "t.jsonl")
        e = TraceEmitter("r", "a", path)
        for t in ("one", "two", "three"):
            e.emit(t)
        seen = list(follow_events(path, should_stop=lambda: True))
        assert [evt["type"] for evt, _ in seen] == ["one", "two", "three"]
        # Resuming from the second event's offset yields only the third.
        rest, _ = read_events_since(path, seen[1][1])
        assert [x["type"] for x in rest] == ["three"]

    def test_picks_up_live_appends(self, tmp_path):
        path = str(tmp_path / "t.jsonl")
        got: list[str] = []
        done = threading.Event()

        def consume():
            for evt, _ in follow_events(path, poll_interval=0.01, should_stop=done.is_set):
                got.append(evt["type"])

        t = threading.Thread(target=consume)
        t.start()
        e = TraceEmitter("r", "a", path)
        e.emit("one")
        e.emit("two")
        for _ in range(200):
            if len(got) == 2:
                break
            threading.Event().wait(0.01)
        done.set()
        t.join(timeout=5)
        assert got == ["one", "two"]


# === Incremental verification ===========================================


class TestChainVerifier:
    def test_matches_verify_chain(self, tmp_path):
        path = str(tmp_path / "t.jsonl")
        e = TraceEmitter("r", "a", path)
        for i in range(5):
            e.emit("x", i=i)
        v = ChainVerifier()
        assert all(v.feed(evt) for evt in _load(path))
        assert v.count == 5

    def test_tamper_sticks(self, tmp_path):
        path = str(tmp_path / "t.jsonl")
        e = TraceEmitter("r", "a", path)
        for i in range(3):
            e.emit("x", i=i)
        events = _load(path)
        events[1]["i"] = 99
        v = ChainVerifier()
        assert v.feed(events[0])
        assert not v.feed(events[1])
        assert not v.feed(events[2])  # stays broken
        assert not v.ok

    def test_mid_file_resume(self, tmp_path):
        path = str(tmp_path / "t.jsonl")
        e = TraceEmitter("r", "a", path)
        for i in range(4):
            e.emit("x", i=i)
        events = _load(path)
        v = ChainVerifier(prev_hash=events[1]["hash"])
        assert v.feed(events[2]) and v.feed(events[3])
        assert not ChainVerifier().feed(events[2])  # without the resume hash it's not a chain start
