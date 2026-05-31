# 06_phalanx_flow — paper → atomize → shard → delegate → resolve

End-to-end worked example composing all three named primitives under a single trace:

- **Shingled extraction** — text → overlapping chunks → atoms
- **Memory shards** — atoms → content-addressed bundles in a `ShardStore`
- **Delegated jobs** — atoms + `JobSpec` → encrypted package → sub-agent hydrates → result shard
- **Phalanx / `CanonicalRegistry`** — entity-shaped atoms → canonical IDs, dedup across surface forms

The whole pipeline runs under a single `TraceEmitter` so a downstream auditor can reconstruct exactly what happened, who did what, and verify the chain end-to-end.

## Run it

```bash
python examples/06_phalanx_flow/run.py
```

Deterministic. No LLM, no network. Safe to run anywhere.

## What it does

```
Stage 1 — Shingled chunking
  Real chunk_text() over a synthetic 2484-char paper about Fugaku.
  → 2 overlapping chunks (target 2000, overlap 400)

Stage 2 — Atom extraction (curated)
  14 atoms (4 fact + 3 decision + 1 convention + 6 entity) representing
  what shingled extraction would produce. Hand-curated for determinism.
  Bundled into a MemoryShard, stored.

Stage 3 — Phalanx entity resolution
  8 author mentions (3 byline + 3 affiliation + 2 acknowledgment) →
  3 canonical entities. normalize_author() pre-pass collapses
  "K. Yamamoto" and "Kazuhiko Yamamoto" to the same ESS digest.

Stage 4 — Delegated summarization
  JobSpec + package_job() → sub-agent hydrates → result shard whose
  parent_shard_id pins back to the stage-2 plaintext content shard.
```

## Three teaching moments

1. **Phalanx doesn't auto-normalize.** The registry's ESS digest is computed from raw candidate fields (with only baseline `.strip().lower()`). `K. Yamamoto` and `Kazuhiko Yamamoto` only collapse to one canonical entity after `normalize_author()` reduces `first_name` to its first letter. The deeper version of this lesson lives at [`docs/entity-resolution.md`](../../docs/entity-resolution.md#normalize-before-you-resolve).

2. **`package_job()` builds its own internal encrypted content shard.** That shard's id differs from any plaintext content shard you hold on the orchestrator side. When pinning a result shard's `parent_shard_id`, pin it at the plaintext predecessor (the atoms you cared about), not the encrypted shipping container. See the new "Lineage through encryption" callout in [`docs/jobs.md`](../../docs/jobs.md#composing-jobs).

3. **One trace can span all four stages.** Same `TraceEmitter` threads through chunking, storage, resolution, and delegation. The chain still verifies end-to-end.

## Related docs

- [`docs/shingled-extraction.md`](../../docs/shingled-extraction.md) — overlapping windows + multi-pass consensus voting
- [`docs/entity-resolution.md`](../../docs/entity-resolution.md) — `CanonicalRegistry`, ESS, tiered matching
- [`docs/jobs.md`](../../docs/jobs.md) — `JobSpec`, `package_job()`, `hydrate_job()`
- [`docs/tracing.md`](../../docs/tracing.md) — hash-chained provenance

## Tests

`tests/test_demos.py::TestDemo06PhalanxFlow` — 7 tests covering exit-zero, trace verifies, all 10 expected event types present, Yamamoto-variants-merge invariant, 8→3 mention collapse invariant, result-lineage-pins-to-content invariant, registry populated.
