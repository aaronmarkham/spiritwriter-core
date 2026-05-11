#!/usr/bin/env python3
"""Demo 5: Per-key delegation with trace observability.

End-to-end walk of the cap-chain primitives shipped in 0.6.0, composed
with the trace system:

  Root → Orchestrator → {Builder, Inspector, Critic}

Each worker:
  - Holds its own Ed25519 keypair (generated at spawn)
  - Builds a TraceEmitter pre-loaded with its cap_id / cap_chain /
    subject_thumbprint / role (so every event auto-tags itself)
  - Emits a small sequence of trace events under its leaf cap
  - Produces one signed MemoryShard with trace_ref pointing at the
    event during which it was emitted, and cap_id pointing at its
    authorizing leaf cap

Then we verify:
  - Each per-worker trace chain (hash linkage intact)
  - The cap chain root→leaf for each worker (signatures + linkage)
  - Each produced shard's leaf signature against its worker's pubkey
  - The intersection of caveats authorizes each shard's scope at issue
    time

Finally we demonstrate provenance queries against the merged event
log: filter by role, by leaf signer, by ancestor cap.

Usage:
    python examples/05_delegation_with_trace/run.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from spiritwriter.fabric.shard import (
    MemoryShard, ShardAtom, AtomKind, DecayClass,
    generate_signing_keypair, pubkey_thumbprint,
)
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.emitter import (
    TraceEmitter, verify_chain,
    events_by_cap, events_by_signer, events_by_role, events_under_chain,
)
from spiritwriter.fabric.entitlement import (
    EntitlementToken, Capability, Caveat, CaveatType,
    create_entitlement, issue_delegated,
    verify_cap_chain, authorize_chain,
)


# ── Setup: root + orchestrator + 3 workers ─────────────────────────


@dataclass
class WorkerCtx:
    """Everything a worker holds: its keys, its cap, and its full chain."""
    role: str
    agent_id: str
    sk: bytes
    pk: bytes
    cap: EntitlementToken
    chain: list[EntitlementToken]


def _build_root(root_sk: bytes, root_pk: bytes) -> EntitlementToken:
    root = create_entitlement(
        granted_to="aaron",
        granted_by="self",
        shard_keys={},
        scopes=["sw:*"],
        capabilities=[Capability.SHARD_READ, Capability.SHARD_WRITE],
        secrets=[],
        budget_usd=100.0,
    )
    root.subject_pubkey = root_pk
    # Allow 2 levels of delegation below: orchestrator (1) + workers (2)
    root.caveats = [Caveat(CaveatType.MAX_DELEGATION_DEPTH, 2)]
    root.sign(root_sk)
    return root


def _build_orchestrator(
    root: EntitlementToken,
    root_sk: bytes,
    orch_pk: bytes,
    run_id: str,
) -> EntitlementToken:
    """Orchestrator runs the whole show — scoped to one specific run."""
    return issue_delegated(
        root, root_sk,
        subject_pubkey=orch_pk,
        granted_to=f"orchestrator:{run_id}",
        scopes=[f"sw:article:{run_id}:*"],
        capabilities=[Capability.SHARD_WRITE],
        caveats=[
            Caveat(CaveatType.EXPIRES_AT, "2099-12-31T00:00:00Z"),  # demo-only
            Caveat(CaveatType.SCOPE_LIMIT, f"sw:article:{run_id}:*"),
        ],
    )


def _build_worker(
    role: str,
    orch: EntitlementToken,
    orch_sk: bytes,
    run_id: str,
) -> WorkerCtx:
    sk, pk = generate_signing_keypair()
    cap = issue_delegated(
        orch, orch_sk,
        subject_pubkey=pk,
        granted_to=f"worker:{role}",
        capabilities=[Capability.SHARD_WRITE],
        # Workers can't delegate further — leaf nodes.
        caveats=[Caveat(CaveatType.MAX_DELEGATION_DEPTH, 0)],
    )
    return WorkerCtx(role=role, agent_id=f"worker:{role}", sk=sk, pk=pk, cap=cap, chain=[])


# ── The worker function ────────────────────────────────────────────


def run_worker(
    worker: WorkerCtx,
    chain: list[EntitlementToken],
    store: ShardStore,
    trace_path: Path,
    run_id: str,
) -> MemoryShard:
    """Simulate a single worker's traced operation under its leaf cap.

    Returns the signed shard the worker produced.
    """
    # Build an emitter with cap context — every event auto-tags itself
    # with this worker's identity, role, and chain position.
    emitter = TraceEmitter(
        run_id=run_id,
        agent_id=worker.agent_id,
        out_path=str(trace_path),
        cap_id=worker.cap.cap_id,
        cap_chain=[c.cap_id for c in chain],
        subject_thumbprint=pubkey_thumbprint(worker.pk),
        role=worker.role,
    )

    emitter.emit("worker_started", task=f"do the {worker.role} thing")

    # Pretend to do work; emit a midstream event.
    emitter.emit("intermediate_finding", note=f"{worker.role} found something")

    # The pivotal moment: produce a shard. Pin trace_ref to the chain
    # position so readers can trace this shard back to this event later.
    pre_shard_ref = emitter.current_trace_ref()
    shard = MemoryShard(
        atoms=[
            ShardAtom(
                text=f"{worker.role.title()}'s finding for {run_id}",
                kind=AtomKind.FACT,
                entity=f"run:{run_id}",
                key=f"{worker.role}_finding",
                value="done",
            ),
        ],
        scope=f"sw:article:{run_id}:{worker.role}",
        origin=worker.agent_id,
        decay_class=DecayClass.PERMANENT,
        cap_id=worker.cap.cap_id,
        trace_ref=pre_shard_ref,
    )
    shard.sign(worker.sk)
    store.put(shard)

    emitter.emit(
        "shard_produced",
        shard_id=shard.shard_id,
        scope=shard.scope,
    )
    emitter.emit("worker_completed")

    return shard


# ── Main ───────────────────────────────────────────────────────────


def main(output_dir: Path | None = None) -> int:
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="demo05_"))
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    run_id = "run-abc"
    print(f"=== Demo 5: per-key delegation + trace ===\n")
    print(f"Output dir: {output_dir}\n")

    # 1. Cap setup ----------------------------------------------------
    root_sk, root_pk = generate_signing_keypair()
    orch_sk, orch_pk = generate_signing_keypair()

    root = _build_root(root_sk, root_pk)
    orch = _build_orchestrator(root, root_sk, orch_pk, run_id)
    print(f"Root cap         : {root.cap_id[:16]}…")
    print(f"Orchestrator cap : {orch.cap_id[:16]}…  (issued by root)")

    workers = [
        _build_worker("builder", orch, orch_sk, run_id),
        _build_worker("inspector", orch, orch_sk, run_id),
        _build_worker("critic", orch, orch_sk, run_id),
    ]
    for w in workers:
        w.chain = [root, orch, w.cap]
        print(f"Worker cap       : {w.cap.cap_id[:16]}…  ({w.role})")
    print()

    # 2. Run each worker ---------------------------------------------
    store = ShardStore(output_dir / "shards")
    produced: dict[str, MemoryShard] = {}
    trace_paths: dict[str, Path] = {}

    for w in workers:
        tp = output_dir / f"{w.role}.jsonl"
        trace_paths[w.role] = tp
        shard = run_worker(w, w.chain, store, tp, run_id)
        produced[w.role] = shard
        print(f"  {w.role}: produced {shard.shard_id[:16]}… "
              f"(signed by {shard.created_by[:12]}…, "
              f"cap {shard.cap_id[:12]}…, "
              f"trace_ref {shard.trace_ref[-12:]})")
    print()

    # 3. Verify chains and shards ------------------------------------
    print("Verification:")
    for w in workers:
        events = [json.loads(l) for l in trace_paths[w.role].read_text(encoding="utf-8").splitlines() if l.strip()]
        assert verify_chain(events), f"{w.role} trace chain broken"

        verify_cap_chain(w.chain, root_pubkeys=[root_pk])
        assert authorize_chain(
            w.chain,
            scope=produced[w.role].scope,
            now_iso="2026-05-11T12:00:00Z",
        ), f"{w.role} cap chain doesn't authorize its scope"

        produced[w.role].verify(w.pk)
        print(f"  [ok] {w.role}: trace chain, cap chain, and shard signature all verify")
    print()

    # 4. Provenance queries on the merged event log ------------------
    print("Provenance queries (merged event log across all workers):")
    merged = []
    for w in workers:
        merged.extend(json.loads(l) for l in trace_paths[w.role].read_text(encoding="utf-8").splitlines() if l.strip())

    builder_evts = events_by_cap(merged, workers[0].cap.cap_id)
    print(f"  events_by_cap(builder.cap_id)            -> {len(builder_evts)} events")

    inspector_evts = events_by_role(merged, "inspector")
    print(f"  events_by_role('inspector')              -> {len(inspector_evts)} events")

    critic_thumb = pubkey_thumbprint(workers[2].pk)
    critic_evts = events_by_signer(merged, critic_thumb)
    print(f"  events_by_signer(critic.thumbprint)      -> {len(critic_evts)} events")

    under_orch = events_under_chain(merged, orch.cap_id)
    print(f"  events_under_chain(orch.cap_id)          -> {len(under_orch)} events (all 3 workers)")

    under_root = events_under_chain(merged, root.cap_id)
    print(f"  events_under_chain(root.cap_id)          -> {len(under_root)} events (everything under root authority)")

    assert len(builder_evts) == 4   # started + intermediate + produced + completed
    assert len(inspector_evts) == 4
    assert len(critic_evts) == 4
    assert len(under_orch) == 12    # 3 workers × 4 events each
    assert len(under_root) == 12

    print(f"\n[ok] demo 5 complete — {len(merged)} traced events across 3 workers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
