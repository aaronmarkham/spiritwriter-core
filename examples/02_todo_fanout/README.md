# Demo 2: Todo Fan-Out

Parent agent receives a compound request, creates a todo-list shard (one
atom per task), then fans out to N subagents. Each subagent emits its own
trace and writes a result shard. Parent assembles all results into a
single assembly shard with lineage back to each source.

## What it shows

- **Multi-child fan-out** — 4 subagents with distinct `run_id`s, each
  producing its own trace chain
- **Content-addressing lineage** — each result atom carries a `source_ref`
  pointing back to the todo-list shard's atom content hash
- **Assembly shard** — collects results from all subagents, with
  `source_ref` on each atom linking to the shard that produced it
- **ShardStore.get()** — hydrates every referenced shard from disk to show
  what a downstream agent would see

## How to run

```bash
python examples/02_todo_fanout/run.py
```

## What to look at

1. **`traces/parent.jsonl`** — notice the repeating pattern:
   `entitlement_granted` -> `studio_job_packaged` -> `spawn_with_shards` ->
   `subagent_completed`, four times. Then a final `shard_created` for the
   assembly.

2. **`traces/child_*.jsonl`** — four independent trace chains. Each starts
   with capability checks and shard decryption, does its work, and ends
   with `studio_job_completed`.

3. **Lineage** in the output — the assembly shard's atoms each have a
   `source_ref` pointing at the result shard that produced them. You can
   walk the chain: assembly atom -> result shard -> todo atom content hash.

4. **`input.md`** — the bundled document the subagents "process". In a
   real system this would be a research paper, codebase, or dataset.

## Takeaway

Fan-out is a common pattern for compound tasks. Fabric makes each
subagent's work independently verifiable (its own trace chain) while the
parent's trace captures the full orchestration. The assembly shard's
`source_ref` fields make lineage queryable — you can always answer "where
did this fact come from?"
