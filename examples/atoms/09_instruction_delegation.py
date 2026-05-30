"""INSTRUCTION atoms + delegation — closing the loop.

The base example (08_instruction.py) shows INSTRUCTION atoms in
isolation. This one shows the *whole point*: instructions get
packaged with content into a real job that a sub-agent can hydrate
and execute, with trace events covering each step of the
package/hydrate/settle workflow.

What this shows:
- An orchestrator builds content atoms (FACT-shaped knowledge) and
  task-side INSTRUCTION atoms (via JobSpec)
- `package_job()` encrypts both shards, mints an EntitlementToken
  binding the decrypt keys + scope + capabilities + budget
- TraceEmitter records job_packaged → job_started → (work happens)
  → job_completed, all hash-chained
- The sub-agent (simulated here as a hydrate-and-print) sees only
  what the entitlement scopes allow

Cross-links:
- docs/jobs.md — the package/hydrate/settle workflow
- docs/entitlements.md — cap-chain and scope-pattern enforcement
- docs/tracing.md — chain-of-custody events
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from spiritwriter.fabric.emitter import TraceEmitter
from spiritwriter.fabric.jobs import JobSpec, package_job
from spiritwriter.fabric.shard import AtomKind, ShardAtom
from spiritwriter.fabric.store import ShardStore


def build_content_atoms() -> list[ShardAtom]:
    """The knowledge the sub-agent will work from (FACT-shaped)."""
    return [
        ShardAtom(
            text="Fugaku is a Japanese supercomputer at RIKEN's R-CCS center.",
            kind=AtomKind.FACT,
            entity="Fugaku", key="overview",
            value="Japanese supercomputer at RIKEN R-CCS",
        ),
        ShardAtom(
            text="Fugaku held the #1 TOP500 spot from 2020 to 2022.",
            kind=AtomKind.FACT,
            entity="Fugaku", key="top500_history",
            value="rank-1 2020-2022",
        ),
    ]


def build_job_spec() -> JobSpec:
    """The task — instructions packaged into INSTRUCTION + CONVENTION atoms
    by JobSpec.to_atoms(), then bundled into the task shard."""
    return JobSpec(
        prompt="Write a 2-paragraph summary of the Fugaku supercomputer.",
        budget_usd=0.50,
        constraints={
            "output_format": "prose-only-no-bullets",
            "audience": "technical-but-non-specialist",
            "citation_style": "inline-source_ref-markers",
        },
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="atoms_ex_") as tmp:
        tmp_path = Path(tmp)
        store = ShardStore(tmp_path / "shards")
        trace_path = tmp_path / "trace.jsonl"

        # The orchestrator sets up a trace and packages the job.
        orch = TraceEmitter(
            run_id="job-xyz-001",
            agent_id="orchestrator:summary-pipeline",
            out_path=trace_path,
        )

        content_atoms = build_content_atoms()
        spec = build_job_spec()

        # package_job:
        # - mints a job_key (AES-256 symmetric)
        # - wraps content_atoms into an encrypted content shard
        # - wraps spec.to_atoms() (INSTRUCTION + CONVENTION) into an
        #   encrypted task shard
        # - returns a PackagedJob with entitlement token (scoped to
        #   just these shard ids, with budget cap = spec.budget_usd)
        packaged = package_job(
            store=store,
            content_atoms=content_atoms,
            job_spec=spec,
            agent_id="orchestrator:summary-pipeline",
            granted_to="worker:summarizer-1",
            tracer=orch,
        )

        print(f"Job packaged.")
        print(f"  Content shard:  {packaged.content_shard_id[:12]}... (encrypted)")
        print(f"  Task shard:     {packaged.task_shard_id[:12]}... (encrypted)")
        print(f"  Entitlement:    granted_to={packaged.entitlement_token.granted_to}, "
              f"budget=${packaged.entitlement_token.budget_usd}")

        # Sub-agent side: hydrate using the entitlement.
        orch.emit("job_started", token_id=packaged.entitlement_token.token_id,
                  content_shard_id=packaged.content_shard_id,
                  task_shard_id=packaged.task_shard_id)
        hydrated = store.hydrate_with_entitlement(packaged.entitlement_token)
        print(f"\nSub-agent's hydrated context (decrypted via the token):\n")
        print(hydrated)

        # Pretend the work completes.
        orch.emit("job_completed", token_id=packaged.entitlement_token.token_id,
                  spent_usd=0.18)

        print(f"\nTrace events ({len(orch.get_events())}):")
        for ev in orch.get_events():
            print(f"  - {ev['type']}")

        print("\nThe takeaway: INSTRUCTION atoms don't live in isolation —")
        print("they're the directive half of a job alongside the content half,")
        print("bound by an entitlement and witnessed by the trace.")


if __name__ == "__main__":
    main()
