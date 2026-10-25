"""Focused tests for strict terminal trace receipts."""

from __future__ import annotations

import json

import pytest

from spiritwriter.fabric.shard import _canonical_json, _sha256
from spiritwriter.fabric.emitter import (
    TraceChainError,
    TraceEmitter,
    TraceReceiptError,
    generate_trace_receipt,
    verify_chain,
    verify_trace_receipt,
)


def test_receipt_binds_stream_chain_trace_bytes_and_artifacts(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    artifact_path = tmp_path / "result.json"
    artifact_path.write_bytes(b'{"ok":true}\n')
    emitter = TraceEmitter("run-1", "agent-a", str(trace_path))
    first = emitter.emit("started")
    last = emitter.emit("completed")

    receipt = generate_trace_receipt(
        trace_path,
        {"result": artifact_path},
        stream_id="run-1",
    )

    assert receipt["stream_id"] == "run-1"
    assert receipt["event_count"] == 2
    assert receipt["first_event_hash"] == first["hash"]
    assert receipt["last_event_hash"] == last["hash"]
    assert receipt["trace_bytes"] == len(trace_path.read_bytes())
    assert receipt["terminal_artifact_bytes"] == {"result": len(artifact_path.read_bytes())}
    assert verify_trace_receipt(receipt, trace_path, {"result": artifact_path})


def test_receipt_detects_exact_trace_byte_change(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    artifact_path = tmp_path / "result.txt"
    artifact_path.write_text("done", encoding="utf-8")
    TraceEmitter("run-1", "a", str(trace_path)).emit("completed")
    receipt = generate_trace_receipt(trace_path, {"result": artifact_path})

    trace_path.write_bytes(trace_path.read_bytes() + b"\n")

    assert not verify_trace_receipt(receipt, trace_path, {"result": artifact_path})


def test_receipt_detects_terminal_artifact_change(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    artifact_path = tmp_path / "result.txt"
    artifact_path.write_text("before", encoding="utf-8")
    TraceEmitter("run-1", "a", str(trace_path)).emit("completed")
    receipt = generate_trace_receipt(trace_path, {"result": artifact_path})

    artifact_path.write_text("after", encoding="utf-8")

    assert not verify_trace_receipt(receipt, trace_path, {"result": artifact_path})


def test_receipt_detects_field_tamper(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    artifact_path = tmp_path / "result.txt"
    artifact_path.write_text("done", encoding="utf-8")
    TraceEmitter("run-1", "a", str(trace_path)).emit("completed")
    receipt = generate_trace_receipt(trace_path, {"result": artifact_path})
    receipt["event_count"] = 99

    assert not verify_trace_receipt(receipt, trace_path, {"result": artifact_path})


def test_receipt_rejects_wrong_or_mixed_stream_identity(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    artifact_path = tmp_path / "result.txt"
    artifact_path.write_text("done", encoding="utf-8")
    TraceEmitter("run-1", "a", str(trace_path)).emit("first")

    with pytest.raises(TraceReceiptError, match="does not match expected"):
        generate_trace_receipt(trace_path, {"result": artifact_path}, stream_id="run-2")

    TraceEmitter("run-1", "b", str(trace_path), concurrent=True).emit("second")
    events = [json.loads(line) for line in trace_path.read_text().splitlines()]
    events[-1]["run_id"] = "run-2"
    hashable = {k: v for k, v in events[-1].items() if k not in ("hash", "sig")}
    events[-1]["hash"] = _sha256(_canonical_json(hashable))
    trace_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    assert verify_chain(events)

    with pytest.raises(TraceChainError, match="multiple run_id"):
        generate_trace_receipt(trace_path, {"result": artifact_path})


def test_receipt_requires_terminal_artifact(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    TraceEmitter("run-1", "a", str(trace_path)).emit("completed")

    with pytest.raises(TraceReceiptError, match="at least one"):
        generate_trace_receipt(trace_path, {})
