"""Studio job packaging — encrypt content + task into shard pairs.

A studio job consists of:
1. Content shard: source material (research, atoms, file refs)
2. Task shard: production instructions (prompt, style, budget, constraints)
3. Entitlement token: grants sub-agent access to both + specific capabilities

The main agent packages the job, spawns a sub-agent with the
entitlement token, and the sub-agent hydrates/decrypts to work.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from spiritwriter.trace.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass, _now_iso,
)
from spiritwriter.trace.crypto import generate_job_key, encrypt_shard, serialize_key
from spiritwriter.trace.entitlement import (
    EntitlementToken, Capability, create_entitlement, serialize_token,
)
from spiritwriter.trace.store import ShardStore


@dataclass
class StudioJobSpec:
    """Defines what a studio sub-agent should produce."""
    prompt: str                          # What to produce
    style: str = "explainer"             # Video style
    budget_usd: float = 10.0             # Max spend
    output_format: str = "mp4"           # Desired output
    duration_seconds: int = 60           # Target duration
    voice: str = "nova"                  # TTS voice
    upload_target: str | None = None     # e.g., "youtube:unlisted"
    constraints: dict[str, Any] = field(default_factory=dict)

    def to_atoms(self) -> list[ShardAtom]:
        """Convert job spec to shard atoms."""
        atoms = [
            ShardAtom(
                text=self.prompt,
                kind=AtomKind.INSTRUCTION,
                key="production_prompt",
            ),
            ShardAtom(
                text=f"Style: {self.style}, Duration: {self.duration_seconds}s, "
                     f"Voice: {self.voice}, Format: {self.output_format}",
                kind=AtomKind.INSTRUCTION,
                key="production_config",
            ),
            ShardAtom(
                text=f"Budget: ${self.budget_usd:.2f}",
                kind=AtomKind.INSTRUCTION,
                key="budget_limit",
            ),
        ]
        if self.upload_target:
            atoms.append(ShardAtom(
                text=f"Upload to: {self.upload_target}",
                kind=AtomKind.INSTRUCTION,
                key="upload_target",
            ))
        for k, v in self.constraints.items():
            atoms.append(ShardAtom(
                text=f"{k}: {v}",
                kind=AtomKind.CONVENTION,
                key=f"constraint.{k}",
            ))
        return atoms


@dataclass
class PackagedJob:
    """Result of packaging a studio job — everything needed to spawn."""
    content_shard_id: str
    task_shard_id: str
    entitlement_token: EntitlementToken
    job_key: bytes  # Keep in memory only, never persisted

    def spawn_task_text(self) -> str:
        """Generate the task text for sessions_spawn.

        Includes the serialized entitlement token and shard refs.
        The studio runner deserializes and hydrates from there.
        """
        token_str = serialize_token(self.entitlement_token)
        return (
            f"<studio-job>\n"
            f"<entitlement>{token_str}</entitlement>\n"
            f"<content-shard>{self.content_shard_id}</content-shard>\n"
            f"<task-shard>{self.task_shard_id}</task-shard>\n"
            f"</studio-job>\n\n"
            f"You are a studio runner agent. Parse the <studio-job> block above.\n"
            f"Use the entitlement token to decrypt and hydrate the content and task shards.\n"
            f"Execute the production task according to the task shard instructions.\n"
            f"Track all spending against the budget limit.\n"
            f"Report results when complete."
        )


def package_job(
    store: ShardStore,
    content_atoms: list[ShardAtom],
    job_spec: StudioJobSpec,
    agent_id: str = "lilit",
    granted_to: str = "studio-runner",
    capabilities: list[str] | None = None,
    secrets: list[str] | None = None,
    scope_prefix: str = "studio",
) -> PackagedJob:
    """Package content + task into encrypted shards with entitlement.

    Args:
        store: ShardStore to persist encrypted shards
        content_atoms: Knowledge atoms (research, facts, sources)
        job_spec: Production instructions
        agent_id: Who is packaging this job
        granted_to: Sub-agent identity
        capabilities: What the sub-agent can do (defaults to standard studio set)
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
        tags=["studio-content"],
    )

    # Build task shard
    task_atoms = job_spec.to_atoms()
    task_shard = MemoryShard(
        atoms=task_atoms,
        scope=f"{scope_prefix}:task",
        origin=agent_id,
        decay_class=DecayClass.SESSION,
        tags=["studio-task"],
    )

    # Encrypt and store both
    enc_content = store.encrypt_and_store(content_shard, job_key)
    enc_task = store.encrypt_and_store(task_shard, job_key)

    # Default capabilities for studio agents
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

    return PackagedJob(
        content_shard_id=content_shard.shard_id,
        task_shard_id=task_shard.shard_id,
        entitlement_token=token,
        job_key=job_key,
    )
