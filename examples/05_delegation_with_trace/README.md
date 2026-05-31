# 05_delegation_with_trace — per-key delegation + trace observability

End-to-end walk of the cap-chain primitives composed with the trace system:

```
Root → Orchestrator → { Builder, Inspector, Critic }
```

Each worker:
- Holds its own Ed25519 keypair (generated at spawn)
- Builds a `TraceEmitter` pre-loaded with its `cap_id` / `cap_chain` / `subject_thumbprint` / `role` so every event auto-tags itself
- Emits a small sequence of events under its leaf cap
- Produces one signed `MemoryShard` whose `trace_ref` points at the event it was emitted under and whose `cap_id` points at its authorizing leaf cap

## Run it

```bash
python examples/05_delegation_with_trace/run.py
```

Deterministic. No LLM, no network.

## What gets verified

Four independent chains, per worker:

1. **Trace chain** — hash linkage intact across the worker's events
2. **Cap chain** — `verify_cap_chain` from root through orchestrator to leaf (signatures + parent-child linkage)
3. **Shard signature** — leaf signs the produced shard; pubkey verifies
4. **Caveat intersection** — `authorize_chain` confirms the chain authorizes the shard's scope at issue time

Plus provenance queries on the merged event log: filter by role, by leaf signer thumbprint, and by ancestor cap (`events_under_chain`).

## Outputs

- `builder.jsonl`, `inspector.jsonl`, `critic.jsonl` — per-worker trace logs
- `shards/` — content-addressed signed shards (one per worker)
- `delegation_tree.mmd` — Mermaid diagram of the cap tree
- `multi_agent.mmd` — Mermaid diagram of the merged event log as a multi-agent workflow

## Related docs

- [`docs/entitlements.md`](../../docs/entitlements.md) — cap chains, caveats, delegation
- [`docs/tracing.md`](../../docs/tracing.md) — hash-chained events, provenance queries
- [`docs/jobs.md`](../../docs/jobs.md) — `package_job()` / `hydrate_job()` (different shape; demo 06 covers that flow)

## Tests

`tests/test_demos.py::TestDemo05DelegationWithTrace` — 8 tests covering exit-zero, per-worker trace creation, chain verification, cap-context tagging, shard-to-trace pinning, role-based queries, ancestor-chain queries, and Mermaid diagram generation.
