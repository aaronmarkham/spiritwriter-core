# Demo 1: Simple Trace

A single parent agent receives a request, packages it into a memory shard,
spawns one subagent with that shard via `package_job`, the subagent returns
a result shard, and the parent records completion.

## What it shows

- **ShardStore + ShardAtom basics** — creating content-addressed knowledge
  bundles and storing them to disk
- **TraceEmitter** — hash-chained provenance events (`shard_created`,
  `spawn_with_shards`, `studio_job_completed`)
- **package_job** — encrypting content + task into shard pairs with an
  entitlement token for the subagent
- **Child trace** — the subagent emits its own trace chain; the parent
  references it by `child_run_id`
- **verify_chain()** — proves neither trace was tampered with
- **render_trace()** — generates a Mermaid diagram of the workflow

## How to run

```bash
python examples/01_simple_trace/run.py
```

## What to look at

1. **`traces/parent.jsonl`** — open it. Each line is a JSON event. Notice
   `prev_event_hash` linking each event to the prior one. The `hash` field
   is a SHA-256 of the event content — change one byte and `verify_chain`
   fails.

2. **`traces/child.jsonl`** — the subagent's independent chain. It starts
   with `capability_checked` (was it allowed to read the shards?), then
   `shard_decrypted` (it unlocked them with the entitlement key), then
   `budget_spent` and `studio_job_completed`.

3. **`traces/workflow.mmd`** — paste into https://mermaid.live to see the
   parent's workflow as a flowchart.

## Takeaway

Trace isn't logging — it's a cryptographic receipt. Every shard creation,
every subagent spawn, every budget spend is hash-chained. If any event is
modified after the fact, the chain breaks and `verify_chain()` returns
`False`.
