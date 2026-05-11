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

from spiritwriter.fabric.shard import _canonical_json, _sha256, _now_iso


class TraceEmitter:
    """Emit hash-chained trace events to a JSONL file.

    Each event links to the previous via prev_event_hash, forming a
    tamper-evident chain. Pass a signer to sign each event with Ed25519.

    **Cap context (optional).** Pass cap_id / cap_chain /
    subject_thumbprint / role to the constructor and every event picks
    them up. Each event then traces back to the specific cap that
    authorized it — enabling provenance queries like "everything
    builder #4 did" or "everything under run-abc". Per-event kwargs
    override for cases where one event runs under a different cap than
    the emitter's default.

    All cap-context fields are optional. Emitters built without them
    produce events identical in shape to the pre-feature version.
    """

    def __init__(
        self,
        run_id: str,
        agent_id: str,
        out_path: str,
        signer: Any | None = None,
        *,
        cap_id: str | None = None,
        cap_chain: list[str] | None = None,
        subject_thumbprint: str | None = None,
        role: str | None = None,
    ):
        self.run_id = run_id
        self.agent_id = agent_id
        self.out_path = out_path
        self.signer = signer  # Optional Ed25519 signer
        self.prev_hash: str | None = None
        # Cap context — sticky defaults applied to every emitted event
        # unless overridden per-call.
        self.cap_id = cap_id
        self.cap_chain = list(cap_chain) if cap_chain is not None else None
        self.subject_thumbprint = subject_thumbprint
        self.role = role
        os.makedirs(os.path.dirname(out_path) if os.path.dirname(out_path) else ".", exist_ok=True)

    def emit(self, event_type: str, **kwargs: Any) -> dict[str, Any]:
        """Emit a trace event, chain it, optionally sign it.

        Cap-context fields (cap_id, cap_chain, subject_thumbprint, role)
        set on the emitter attach automatically and are covered by the
        chained hash. Override per-event by passing them as kwargs.
        """
        evt: dict[str, Any] = {
            "type": event_type,
            "run_id": self.run_id,
            "event_id": kwargs.pop("event_id", str(uuid.uuid4())),
            "ts": _now_iso(),
            "agent_id": self.agent_id,
            "prev_event_hash": self.prev_hash,
        }
        # Attach cap context — kwargs may override per-event.
        if "cap_id" not in kwargs and self.cap_id is not None:
            evt["cap_id"] = self.cap_id
        if "cap_chain" not in kwargs and self.cap_chain is not None:
            evt["cap_chain"] = list(self.cap_chain)
        if "subject_thumbprint" not in kwargs and self.subject_thumbprint is not None:
            evt["subject_thumbprint"] = self.subject_thumbprint
        if "role" not in kwargs and self.role is not None:
            evt["role"] = self.role
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

    def current_trace_ref(self) -> str | None:
        """Stable pointer to the most recent event in this chain.

        Format: ``chain:<run_id>#<last_event_hash>``. Drop into
        :attr:`MemoryShard.trace_ref` when producing a shard during a
        traced operation — parsing the ref later answers "which trace
        event was this shard emitted under?"

        Returns None before the first emit() call.
        """
        if self.prev_hash is None:
            return None
        return f"chain:{self.run_id}#{self.prev_hash}"

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

    def job_packaged(
        self,
        content_shard_id: str,
        task_shard_id: str,
        token_id: str,
        budget_usd: float,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record job packaging."""
        return self.emit(
            "job_packaged",
            content_shard_id=content_shard_id,
            task_shard_id=task_shard_id,
            token_id=token_id,
            budget_usd=budget_usd,
            **kwargs,
        )

    def job_started(
        self,
        token_id: str,
        content_shard_id: str,
        task_shard_id: str,
        prompt: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record job execution start."""
        return self.emit(
            "job_started",
            token_id=token_id,
            content_shard_id=content_shard_id,
            task_shard_id=task_shard_id,
            prompt=prompt,
            **kwargs,
        )

    def job_completed(
        self,
        token_id: str,
        result_shard_id: str,
        spent_usd: float,
        outputs: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record job completion."""
        return self.emit(
            "job_completed",
            token_id=token_id,
            result_shard_id=result_shard_id,
            spent_usd=spent_usd,
            outputs=outputs or [],
            **kwargs,
        )

    def job_failed(
        self,
        token_id: str,
        error: str,
        spent_usd: float = 0.0,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Record job failure."""
        return self.emit(
            "job_failed",
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


# === Provenance queries =====================================================
#
# These operate on already-loaded event lists (from `emitter.get_events()` or
# `_load_events(jsonl_path)`). They're filters, not indices — fine for trace
# logs in the hundreds-of-thousands range; build a real index if you need
# faster than that.
#
# All filters preserve event order, so chain integrity (verifiable via
# verify_chain) is not affected by querying.


def events_by_cap(events: list[dict[str, Any]], cap_id: str) -> list[dict[str, Any]]:
    """Events whose leaf cap_id matches.

    Answers "what specifically did this worker do?" — assumes the
    worker's emitter carries that cap_id (or each event overrode it
    explicitly).
    """
    return [e for e in events if e.get("cap_id") == cap_id]


def events_by_signer(
    events: list[dict[str, Any]], subject_thumbprint: str
) -> list[dict[str, Any]]:
    """Events emitted by a specific signing identity.

    `subject_thumbprint` is the sha256 hex of the signer's public key
    (matches ``MemoryShard.created_by``). Useful when one identity
    operated under multiple caps (e.g., rotation) — captures everything
    the *key* did regardless of which cap wielded it.
    """
    return [e for e in events if e.get("subject_thumbprint") == subject_thumbprint]


def events_by_role(events: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
    """Events by role label (e.g., ``builder``, ``inspector``, ``critic``).

    Roles are opaque to the substrate but group spans cleanly when an
    orchestrator spawns multiple workers under distinct roles.
    """
    return [e for e in events if e.get("role") == role]


def events_under_chain(
    events: list[dict[str, Any]], ancestor_cap_id: str
) -> list[dict[str, Any]]:
    """Events whose cap_chain contains ``ancestor_cap_id``.

    Answers "everything that ran under user X's authority" by passing
    X's bootstrap/root cap_id — captures every descendant worker's
    events regardless of role. Requires events to carry ``cap_chain``;
    falls back to checking ``cap_id`` when no chain is recorded (a
    leaf-only signal).
    """
    out: list[dict[str, Any]] = []
    for e in events:
        chain = e.get("cap_chain")
        if chain is not None and ancestor_cap_id in chain:
            out.append(e)
        elif chain is None and e.get("cap_id") == ancestor_cap_id:
            out.append(e)
    return out


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
