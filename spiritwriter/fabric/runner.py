"""Job runner — sub-agent execution logic for packaged jobs.

This module provides the parsing and hydration logic that a
sub-agent uses to unpack a job and execute it.

The runner:
1. Parses the <sw-job> block from its task text
2. Deserializes the entitlement token
3. Hydrates content + task shards via the store
4. Tracks budget as work proceeds
5. Writes a result shard when complete
"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, field
from typing import Any

from spiritwriter.fabric.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass, _now_iso,
)
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.crypto import decrypt_shard
from spiritwriter.fabric.entitlement import (
    EntitlementToken, deserialize_token, validate_capability,
    validate_budget, is_expired, get_shard_key, Capability,
)
from spiritwriter.fabric.emitter import TraceEmitter


class JobRunnerError(Exception):
    """Errors during job execution."""
    pass


@dataclass
class JobContext:
    """Parsed and hydrated job ready for execution."""
    token: EntitlementToken
    content_shard: MemoryShard
    task_shard: MemoryShard
    content_shard_id: str
    task_shard_id: str

    @property
    def prompt(self) -> str | None:
        """Extract the production prompt from task shard."""
        for atom in self.task_shard.atoms:
            if atom.key == "production_prompt":
                return atom.text
        return None

    @property
    def budget_usd(self) -> float:
        return self.token.budget_usd

    @property
    def config(self) -> dict[str, str]:
        """Extract all task config as key→value dict."""
        return {
            atom.key: atom.text
            for atom in self.task_shard.atoms
            if atom.key is not None
        }

    @property
    def content_text(self) -> str:
        """Render content shard as readable context."""
        return self.content_shard.hydrate_context()


@dataclass
class BudgetTracker:
    """Track spending against entitlement budget."""
    budget_usd: float
    token_id: str | None = None
    tracer: TraceEmitter | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)

    @property
    def spent(self) -> float:
        return sum(e["amount"] for e in self.entries)

    @property
    def remaining(self) -> float:
        return self.budget_usd - self.spent

    def can_spend(self, amount: float) -> bool:
        return self.spent + amount <= self.budget_usd

    def record(self, label: str, amount: float) -> None:
        """Record a spend. Raises if over budget."""
        if not self.can_spend(amount):
            raise JobRunnerError(
                f"Budget exceeded: ${self.spent:.2f} + ${amount:.2f} > "
                f"${self.budget_usd:.2f} limit"
            )
        self.entries.append({
            "label": label,
            "amount": amount,
            "timestamp": _now_iso(),
        })
        if self.tracer and self.token_id:
            self.tracer.budget_spent(
                token_id=self.token_id,
                label=label,
                amount=amount,
                total_spent=self.spent,
                budget_usd=self.budget_usd,
            )

    def summary(self) -> dict[str, Any]:
        return {
            "budget_usd": self.budget_usd,
            "spent_usd": round(self.spent, 4),
            "remaining_usd": round(self.remaining, 4),
            "entries": self.entries,
        }


def parse_job_block(task_text: str) -> tuple[str, str, str]:
    """Parse <sw-job> block from task text.

    Returns (token_str, content_shard_id, task_shard_id).
    """
    token_match = re.search(
        r"<entitlement>(.*?)</entitlement>", task_text, re.DOTALL
    )
    content_match = re.search(
        r"<content-shard>(.*?)</content-shard>", task_text, re.DOTALL
    )
    task_match = re.search(
        r"<task-shard>(.*?)</task-shard>", task_text, re.DOTALL
    )

    if not all([token_match, content_match, task_match]):
        raise JobRunnerError(
            "Missing required fields in <sw-job> block. "
            "Need <entitlement>, <content-shard>, and <task-shard>."
        )

    return (
        token_match.group(1).strip(),
        content_match.group(1).strip(),
        task_match.group(1).strip(),
    )


def hydrate_job(
    store: ShardStore,
    task_text: str,
    tracer: TraceEmitter | None = None,
) -> JobContext:
    """Parse task text, validate entitlement, decrypt shards.

    This is the main entry point for a job runner sub-agent.
    """
    token_str, content_id, task_id = parse_job_block(task_text)

    # Deserialize and validate token
    token = deserialize_token(token_str)

    if is_expired(token):
        if tracer:
            tracer.capability_checked(
                token_id=token.token_id, capability="token_valid", allowed=False,
            )
        raise JobRunnerError("Entitlement token has expired")

    if not validate_capability(token, Capability.SHARD_READ):
        if tracer:
            tracer.capability_checked(
                token_id=token.token_id, capability=Capability.SHARD_READ, allowed=False,
            )
        raise JobRunnerError("Token lacks shard:read capability")

    if tracer:
        tracer.capability_checked(
            token_id=token.token_id, capability=Capability.SHARD_READ, allowed=True,
        )

    # Decrypt content shard
    content_key = get_shard_key(token, content_id)
    content_enc = store.get_encrypted(content_id)
    if content_enc is None:
        raise JobRunnerError(f"Content shard {content_id} not found in store")
    content_shard = decrypt_shard(content_enc, content_key)

    if tracer:
        tracer.shard_decrypted(
            shard_id=content_id, token_id=token.token_id, scope=content_shard.scope,
        )

    # Decrypt task shard
    task_key = get_shard_key(token, task_id)
    task_enc = store.get_encrypted(task_id)
    if task_enc is None:
        raise JobRunnerError(f"Task shard {task_id} not found in store")
    task_shard = decrypt_shard(task_enc, task_key)

    if tracer:
        tracer.shard_decrypted(
            shard_id=task_id, token_id=token.token_id, scope=task_shard.scope,
        )
        tracer.job_started(
            token_id=token.token_id,
            content_shard_id=content_id,
            task_shard_id=task_id,
            prompt=next((a.text for a in task_shard.atoms if a.key == "production_prompt"), None),
        )

    return JobContext(
        token=token,
        content_shard=content_shard,
        task_shard=task_shard,
        content_shard_id=content_id,
        task_shard_id=task_id,
    )


def create_result_shard(
    job: JobContext,
    results: dict[str, Any],
    agent_id: str = "job-runner",
    *,
    parent_shard_id: str | None = None,
) -> MemoryShard:
    """Create a result shard from job execution output.

    The result shard captures what was produced, costs, and output refs.
    It's linked to the job via scope and can be encrypted with the same key.

    Args:
        job: The hydrated JobContext the runner is producing a result for.
        results: Dict of execution output. Recognized keys: ``budget``
            (cost summary dict), ``outputs`` (list of {type, ref}),
            ``warnings`` (list of strings).
        agent_id: Identity of the runner producing the result. Becomes
            the result shard's ``origin``.
        parent_shard_id: **Keyword-only.** Optional content-addressed
            predecessor for lineage. **Pin this at the plaintext content
            shard your orchestrator extracted/curated** — NOT at
            ``job.content_shard_id``, which is the encrypted job-internal
            shard ``package_job()`` built. Those two shards have
            different ids (different bytes after encryption) and the
            meaningful predecessor for audit lineage is the plaintext
            one. Default ``None`` means no parent recorded — acceptable
            for one-shot work, but the orchestrator-with-source-atoms
            case is the canonical pattern (see
            ``docs/jobs.md`` § "Lineage through encryption").

            **Signing implication.** ``parent_shard_id`` is included in
            ``signing_payload()``. Setting it via this kwarg means the
            parent is in the signed bytes from construction. The
            previous mutate-then-re-put pattern would silently
            invalidate any signature applied at construction — another
            reason to prefer this kwarg over post-hoc field mutation.
    """
    atoms = []

    # Execution summary
    atoms.append(ShardAtom(
        text=f"Completed job: {job.prompt or 'unknown'}",
        kind=AtomKind.FACT,
        key="job_summary",
    ))

    # Cost tracking
    if "budget" in results:
        budget = results["budget"]
        atoms.append(ShardAtom(
            text=f"Spent ${budget.get('spent_usd', 0):.2f} of "
                 f"${budget.get('budget_usd', 0):.2f} budget",
            kind=AtomKind.FACT,
            key="cost_summary",
            entity="job_run",
        ))

    # Output refs (video paths, YouTube URLs, etc.)
    for output in results.get("outputs", []):
        atoms.append(ShardAtom(
            text=f"{output.get('type', 'output')}: {output.get('ref', 'unknown')}",
            kind=AtomKind.FACT,
            key=f"output.{output.get('type', 'unknown')}",
            entity="job_run",
        ))

    # Errors or warnings
    for warning in results.get("warnings", []):
        atoms.append(ShardAtom(
            text=warning,
            kind=AtomKind.CONTEXT,
            key="warning",
        ))

    return MemoryShard(
        atoms=atoms,
        scope=job.content_shard.scope.replace(":content", ":result"),
        origin=agent_id,
        decay_class=DecayClass.STABLE,
        tags=["job-result"],
        parent_shard_id=parent_shard_id,
        meta={
            "content_shard_id": job.content_shard_id,
            "task_shard_id": job.task_shard_id,
            "completed_at": _now_iso(),
        },
    )
