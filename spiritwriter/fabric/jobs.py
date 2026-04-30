"""Job packaging — bundle content + task + entitlement for a sub-agent.

A job is a generic unit of delegated sub-agent work:

- Content shard: encrypted source material the sub-agent will reference.
- Task shard: encrypted production instructions (prompt, budget, constraints).
- Entitlement token: bearer credential gating decryption, scope, capabilities,
  and budget.

The issuer calls package_job() to produce a PackagedJob; spawns the
sub-agent with packaged.spawn_task_text() in its prompt; the sub-agent
calls hydrate_job() (in spiritwriter.fabric.runner) to validate, decrypt,
and execute.

JobSpec carries the minimal generic fields: prompt, budget, constraints.
Callers with richer task shapes — video production, document analysis,
arbitrary structured tasks — subclass JobSpec and override to_atoms().
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spiritwriter.fabric.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass, _now_iso,
)
from spiritwriter.fabric.crypto import generate_job_key, encrypt_shard, serialize_key
from spiritwriter.fabric.entitlement import (
    EntitlementToken, Capability, create_entitlement, serialize_token,
)
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.emitter import TraceEmitter


@dataclass
class JobSpec:
    """Defines what a sub-agent should produce.

    The minimal generic shape: a prompt, a budget cap, and free-form
    constraints. Subclass this and override to_atoms() to add domain-
    specific fields (e.g. VideoJobSpec for media production).
    """
    prompt: str
    budget_usd: float = 10.0
    constraints: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.budget_usd < 0:
            raise ValueError(
                f"budget_usd must be >= 0, got {self.budget_usd!r}"
            )

    def to_atoms(self) -> list[ShardAtom]:
        """Convert job spec to shard atoms.

        Subclasses should call super().to_atoms() and append their own
        atoms — keeps the prompt/budget/constraint atoms in a stable
        position for runners that read them by key.
        """
        atoms = [
            ShardAtom(
                text=self.prompt,
                kind=AtomKind.INSTRUCTION,
                key="production_prompt",
            ),
            ShardAtom(
                text=f"Budget: ${self.budget_usd:.2f}",
                kind=AtomKind.INSTRUCTION,
                key="budget_limit",
            ),
        ]
        for k, v in self.constraints.items():
            atoms.append(ShardAtom(
                text=f"{k}: {v}",
                kind=AtomKind.CONVENTION,
                key=f"constraint.{k}",
            ))
        return atoms


@dataclass
class PackagedJob:
    """Result of packaging a job — everything needed to spawn."""
    content_shard_id: str
    task_shard_id: str
    entitlement_token: EntitlementToken
    job_key: bytes  # Keep in memory only, never persisted

    def spawn_task_text(self) -> str:
        """Generate the task text for sessions_spawn.

        Includes the serialized entitlement token and shard refs.
        The job runner deserializes and hydrates from there.
        """
        token_str = serialize_token(self.entitlement_token)
        return (
            f"<sw-job>\n"
            f"<entitlement>{token_str}</entitlement>\n"
            f"<content-shard>{self.content_shard_id}</content-shard>\n"
            f"<task-shard>{self.task_shard_id}</task-shard>\n"
            f"</sw-job>\n\n"
            f"You are a job runner agent. Parse the <sw-job> block above.\n"
            f"Use the entitlement token to decrypt and hydrate the content and task shards.\n"
            f"Execute the production task according to the task shard instructions.\n"
            f"Track all spending against the budget limit.\n"
            f"Report results when complete."
        )


def package_job(
    store: ShardStore,
    content_atoms: list[ShardAtom],
    job_spec: JobSpec,
    agent_id: str = "lilit",
    granted_to: str = "job-runner",
    capabilities: list[str] | None = None,
    secrets: list[str] | None = None,
    scope_prefix: str = "job",
    tracer: TraceEmitter | None = None,
) -> PackagedJob:
    """Package content + task into encrypted shards with entitlement.

    Args:
        store: ShardStore to persist encrypted shards
        content_atoms: Knowledge atoms (research, facts, sources)
        job_spec: Production instructions
        agent_id: Who is packaging this job
        granted_to: Sub-agent identity
        capabilities: What the sub-agent can do (defaults to standard set)
        secrets: Which API keys the sub-agent can access
        scope_prefix: Scope namespace for this job

    Returns:
        PackagedJob with encrypted shard ids + entitlement token
    """
    # Generate a single job key (both shards use same key)
    job_key = generate_job_key()

    # Build content shard
    content_shard = MemoryShard(
        atoms=content_atoms,
        scope=f"{scope_prefix}:content",
        origin=agent_id,
        decay_class=DecayClass.ACTIVE,
        tags=["job-content"],
    )

    # Build task shard
    task_atoms = job_spec.to_atoms()
    task_shard = MemoryShard(
        atoms=task_atoms,
        scope=f"{scope_prefix}:task",
        origin=agent_id,
        decay_class=DecayClass.SESSION,
        tags=["job-task"],
    )

    # Encrypt and store both
    enc_content = store.encrypt_and_store(content_shard, job_key)
    enc_task = store.encrypt_and_store(task_shard, job_key)

    # Default capabilities for job runner agents
    if capabilities is None:
        capabilities = [
            Capability.SHARD_READ,
            Capability.SHARD_WRITE,
            Capability.KB_CREATE,
            Capability.KB_PRODUCE,
            Capability.WEB_SEARCH,
            Capability.WEB_FETCH,
            Capability.EXEC_RUN,
        ]

    if secrets is None:
        secrets = []

    # Create entitlement
    token = create_entitlement(
        granted_to=granted_to,
        granted_by=agent_id,
        shard_keys={
            content_shard.shard_id: job_key,
            task_shard.shard_id: job_key,
        },
        scopes=[f"{scope_prefix}:*"],
        capabilities=capabilities,
        secrets=secrets,
        budget_usd=job_spec.budget_usd,
    )

    pkg = PackagedJob(
        content_shard_id=content_shard.shard_id,
        task_shard_id=task_shard.shard_id,
        entitlement_token=token,
        job_key=job_key,
    )

    # Emit trace events
    if tracer:
        tracer.entitlement_granted(
            token_id=token.token_id,
            granted_to=granted_to,
            shard_ids=[content_shard.shard_id, task_shard.shard_id],
            scopes=token.scopes,
            capabilities=token.capabilities,
            budget_usd=job_spec.budget_usd,
        )
        tracer.job_packaged(
            content_shard_id=content_shard.shard_id,
            task_shard_id=task_shard.shard_id,
            token_id=token.token_id,
            budget_usd=job_spec.budget_usd,
        )

    return pkg
