"""Trace emitter — hash-chained provenance events.

Adapted from strands-trace reference implementation.
Extended with shard lifecycle events.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from spiritwriter.trace.shard import _canonical_json, _sha256, _now_iso


class TraceEmitter:
    """Emit hash-chained trace events to a JSONL file.

    Each event is linked to the previous via prev_event_hash,
    forming a tamper-evident chain. Events can optionally be
    signed with an Ed25519 key (when signer is provided).
    """

    def __init__(
        self,
        run_id: str,
        agent_id: str,
        out_path: str,
        signer: Any | None = None,
    ):
        self.run_id = run_id
        self.agent_id = agent_id
        self.out_path = out_path
        self.signer = signer  # Optional Ed25519 signer
        self.prev_hash: str | None = None
        os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else ".", exist_ok=True)

    def emit(self, event_type: str, **kwargs: Any) -> dict[str, Any]:
        """Emit a trace event, chain it, optionally sign it."""
        evt: dict[str, Any] = {
            "type": event_type,
            "run_id": self.run_id,
            "event_id": kwargs.pop("event_id", str(uuid.uuid4())),
            "ts": _now_iso(),
            "agent_id": self.agent_id,
            "prev_event_hash": self.prev_hash,
        }
        evt.update(kwargs)

        # Compute hash over everything except hash and sig
        hashable = {k: v for k, v in evt.items() if k not in ("hash", "sig")}
        h = _sha256(_canonical_json(hashable))
        evt["hash"] = h

        # Sign if signer available
        if self.signer:
            evt["sig"] = self.signer.sign(h.encode("utf-8"))

        self.prev_hash = h
        self._write(evt)
        return evt

    def _write(self, evt: dict[str, Any]) -> None:
        with open(self.out_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(evt, ensure_ascii=False) + "\n")

    # === Shard Lifecycle Events ===

    def shard_created(self, shard_id: str, scope: str, atom_count: int, **kwargs: Any) -> dict[str, Any]:
        """Record shard creation in the trace chain."""
        return self.emit(
            "shard_created",
            shard_id=shard_id,
            scope=scope,
            atom_count=atom_count,
            **kwargs,
        )

    def shard_resolved(self, shard_id: str, by_agent: str, **kwargs: Any) -> dict[str, Any]:
        """Record shard hydration/resolution."""
        return self.emit(
            "shard_resolved",
            shard_id=shard_id,
            resolved_by=by_agent,
            **kwargs,
        )

    def shard_superseded(self, old_shard_id: str, new_shard_id: str, **kwargs: Any) -> dict[str, Any]:
        """Record shard replacement."""
        return self.emit(
            "shard_superseded",
            old_shard_id=old_shard_id,
            new_shard_id=new_shard_id,
            **kwargs,
        )

    def spawn_with_shards(
        self,
        child_agent_id: str,
        shard_refs: list[dict[str, Any]],
        task: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record spawning a sub-agent with shard pointers."""
        return self.emit(
            "spawn_with_shards",
            child_agent_id=child_agent_id,
            shard_refs=shard_refs,
            task=task,
            **kwargs,
        )

    def decision_extracted(
        self,
        shard_id: str,
        decision_text: str,
        entity: str | None = None,
        rationale: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record a decision extracted from conversation."""
        return self.emit(
            "decision_extracted",
            shard_id=shard_id,
            decision_text=decision_text,
            entity=entity,
            rationale=rationale,
            **kwargs,
        )
