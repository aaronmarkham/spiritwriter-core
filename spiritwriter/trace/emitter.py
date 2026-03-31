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

    # === Entitlement & Studio Events ===

    def entitlement_granted(
        self,
        token_id: str,
        granted_to: str,
        shard_ids: list[str],
        scopes: list[str],
        capabilities: list[str],
        budget_usd: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record entitlement token creation."""
        return self.emit(
            "entitlement_granted",
            token_id=token_id,
            granted_to=granted_to,
            shard_ids=shard_ids,
            scopes=scopes,
            capabilities=capabilities,
            budget_usd=budget_usd,
            **kwargs,
        )

    def shard_decrypted(
        self,
        shard_id: str,
        token_id: str,
        scope: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record shard decryption via entitlement."""
        return self.emit(
            "shard_decrypted",
            shard_id=shard_id,
            token_id=token_id,
            scope=scope,
            **kwargs,
        )

    def capability_checked(
        self,
        token_id: str,
        capability: str,
        allowed: bool,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record a capability validation check."""
        return self.emit(
            "capability_checked",
            token_id=token_id,
            capability=capability,
            allowed=allowed,
            **kwargs,
        )

    def budget_spent(
        self,
        token_id: str,
        label: str,
        amount: float,
        total_spent: float,
        budget_usd: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record budget expenditure."""
        return self.emit(
            "budget_spent",
            token_id=token_id,
            label=label,
            amount=amount,
            total_spent=total_spent,
            budget_usd=budget_usd,
            **kwargs,
        )

    def studio_job_packaged(
        self,
        content_shard_id: str,
        task_shard_id: str,
        token_id: str,
        budget_usd: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record studio job packaging."""
        return self.emit(
            "studio_job_packaged",
            content_shard_id=content_shard_id,
            task_shard_id=task_shard_id,
            token_id=token_id,
            budget_usd=budget_usd,
            **kwargs,
        )

    def studio_job_started(
        self,
        token_id: str,
        content_shard_id: str,
        task_shard_id: str,
        prompt: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record studio job execution start."""
        return self.emit(
            "studio_job_started",
            token_id=token_id,
            content_shard_id=content_shard_id,
            task_shard_id=task_shard_id,
            prompt=prompt,
            **kwargs,
        )

    def studio_job_completed(
        self,
        token_id: str,
        result_shard_id: str,
        spent_usd: float,
        outputs: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record studio job completion."""
        return self.emit(
            "studio_job_completed",
            token_id=token_id,
            result_shard_id=result_shard_id,
            spent_usd=spent_usd,
            outputs=outputs or [],
            **kwargs,
        )

    def studio_job_failed(
        self,
        token_id: str,
        error: str,
        spent_usd: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record studio job failure."""
        return self.emit(
            "studio_job_failed",
            token_id=token_id,
            error=error,
            spent_usd=spent_usd,
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

    def get_events(self) -> list[dict[str, Any]]:
        """Read all emitted events from the JSONL output file.

        Returns an empty list if the file does not exist yet.
        """
        try:
            with open(self.out_path, "r", encoding="utf-8") as f:
                return [json.loads(line) for line in f if line.strip()]
        except FileNotFoundError:
            return []


def verify_chain(events: list[dict[str, Any]]) -> bool:
    """Verify the hash chain of a list of trace events.

    Checks that each event's hash is correctly computed and that
    prev_event_hash links form a valid chain.

    Returns True if the chain is valid (or empty), False otherwise.
    """
    if not events:
        return True

    for i, evt in enumerate(events):
        # Recompute hash from all fields except hash and sig
        hashable = {k: v for k, v in evt.items() if k not in ("hash", "sig")}
        expected_hash = _sha256(_canonical_json(hashable))
        if evt.get("hash") != expected_hash:
            return False

        # Check chain linkage
        if i == 0:
            if evt.get("prev_event_hash") is not None:
                return False
        else:
            if evt.get("prev_event_hash") != events[i - 1].get("hash"):
                return False

    return True
