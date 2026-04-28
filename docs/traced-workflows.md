# Building Traced Workflows

A long-running multi-stage pipeline crashes halfway through. The choices are: restart from scratch (lose hours of work), or restart from where it left off (need to know what completed, what didn't, and what state to resume from). The **traced workflow pattern** gives you both — a tamper-evident receipt for every stage, and a checkpoint shard at every boundary so resume is a one-line lookup.

The recipe: wrap each stage with two `emit()` calls (started, completed), persist a checkpoint shard between stages, and verify the chain at the end. About two lines of glue per stage; the helpers are written once.

## What You Get

- **Hash-chained trace events** — tamper-evident receipt for every stage, every LLM call, every shard hand-off.
- **Checkpoint shards** — resumable state between stages, scoped with `DecayClass.CHECKPOINT` so they auto-prune after 4 hours.
- **Budget tracking** — per-stage and per-agent spend, with hard caps that raise instead of silently overspending.
- **Provenance reports** — Mermaid diagrams (workflow / genealogy / multi-agent) rendered from the same trace JSONL.

The cost is roughly 50 lines of glue on top of your existing logic. None of it is novel — every primitive lives in [spiritwriter.fabric](../spiritwriter/fabric).

## Quick Start

The minimum viable traced workflow has five moving parts: stage list, store, emitter, per-stage emit pair, and final verification.

### Define the Stages

```python
STAGES = ["ingest", "extract", "generate", "validate", "assemble"]
```

### Set Up Store and Emitter

```python
from spiritwriter.fabric.emitter import TraceEmitter
from spiritwriter.fabric.store import ShardStore

store = ShardStore("/path/to/shards")
emitter = TraceEmitter(
    run_id="my-pipeline-2026-04-28",   # any string identifying this run
    agent_id="my-pipeline",
    out_path="run.jsonl",
)
```

`TraceEmitter` writes JSONL append-only. One emitter per output file — multiple producers writing to the same path will interleave lines and break chain verification.

### Wrap Each Stage

Two `emit()` calls bracket each stage's existing logic. `emit()` takes the event type and arbitrary keyword fields:

```python
emitter.emit("stage_started", stage="extract", input_ref=input_shard.shard_id)

# ...your existing logic here...

emitter.emit("stage_completed", stage="extract", output_ref=output_shard.shard_id)
```

### Write a Checkpoint Shard

After each stage, persist resume state as a `CHECKPOINT`-class shard:

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass

checkpoint = MemoryShard(
    atoms=[
        ShardAtom(text="Stage complete", kind=AtomKind.CHECKPOINT,
                  key="stage", value="extract_complete"),
        ShardAtom(text="Reference to intermediate output", kind=AtomKind.CONTEXT,
                  key="output_shard_ref", value=output_shard.shard_id),
    ],
    scope="jobs:in-progress",
    origin="my-pipeline",
    decay_class=DecayClass.CHECKPOINT,   # auto-prune after 4 hours
    tags=["checkpoint", "my-pipeline"],
)

ref = store.put(checkpoint)
store.set_ref("job:my-pipeline:checkpoint", ref.shard_id)
```

The named ref is what makes resume work — it's a stable handle the next process can look up regardless of what shard ID got minted.

### Resume from Checkpoints

On startup, find the last completed stage and resume after it:

```python
def get_resume_stage(store, stages, ref_name="job:my-pipeline:checkpoint"):
    """Return (next_stage_to_run, checkpoint_shard_or_None)."""
    shard = store.resolve_ref(ref_name)
    if shard is None:
        return stages[0], None    # nothing to resume from

    atom = shard.get_atom("stage")
    if atom is None:
        return stages[0], None

    completed = atom.value.replace("_complete", "")
    if completed in stages:
        next_idx = stages.index(completed) + 1
        if next_idx < len(stages):
            return stages[next_idx], shard

    return stages[0], None
```

### Verify and Render

After all stages complete:

```python
from spiritwriter.fabric.emitter import verify_chain
from spiritwriter.fabric.visualize import load_trace, render_trace

events = emitter.get_events()
assert verify_chain(events), "Trace chain integrity check failed"

# Render as Mermaid — render_trace takes events, not a path
mermaid_code = render_trace(events, diagram_type="workflow")
```

`render_trace` accepts the diagram types `"workflow"`, `"genealogy"`, and `"multi-agent"` (note the hyphen). For an arbitrary trace file produced elsewhere, use `load_trace(path)` first.

## Complete Pipeline Template

Copy this and customize the stage handlers:

```python
"""Traced workflow template — copy and customize."""

from spiritwriter.fabric.emitter import TraceEmitter, verify_chain
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.fabric.visualize import render_trace


STAGES = ["ingest", "extract", "generate", "validate", "assemble"]

STAGE_HANDLERS = {
    "ingest":   lambda store, prev: do_ingest(store, prev),
    "extract":  lambda store, prev: do_extract(store, prev),
    "generate": lambda store, prev: do_generate(store, prev),
    "validate": lambda store, prev: do_validate(store, prev),
    "assemble": lambda store, prev: do_assemble(store, prev),
}


def write_checkpoint(store, stage, result_ref, pipeline_name):
    checkpoint = MemoryShard(
        atoms=[
            ShardAtom(text=f"Stage {stage} complete", kind=AtomKind.CHECKPOINT,
                      key="stage", value=f"{stage}_complete"),
            ShardAtom(text="Output reference", kind=AtomKind.CONTEXT,
                      key="output_ref", value=result_ref),
        ],
        scope="jobs:in-progress",
        origin=pipeline_name,
        decay_class=DecayClass.CHECKPOINT,
        tags=["checkpoint", pipeline_name],
    )
    ref = store.put(checkpoint)
    store.set_ref(f"job:{pipeline_name}:checkpoint", ref.shard_id)
    return checkpoint


def get_resume_stage(store, stages, pipeline_name):
    shard = store.resolve_ref(f"job:{pipeline_name}:checkpoint")
    if shard is None:
        return stages[0], None
    atom = shard.get_atom("stage")
    if atom is None:
        return stages[0], None
    completed = atom.value.replace("_complete", "")
    if completed in stages:
        next_idx = stages.index(completed) + 1
        if next_idx < len(stages):
            return stages[next_idx], shard
    return stages[0], None


def run_pipeline(store_path, trace_path, pipeline_name="my-pipeline"):
    store = ShardStore(store_path)
    emitter = TraceEmitter(
        run_id=f"{pipeline_name}-run",
        agent_id=pipeline_name,
        out_path=trace_path,
    )

    resume_stage, checkpoint = get_resume_stage(store, STAGES, pipeline_name)
    print(f"Resuming from: {resume_stage}")

    emitter.emit("pipeline_started", pipeline=pipeline_name, resume_from=resume_stage)

    prev = checkpoint
    for stage in STAGES[STAGES.index(resume_stage):]:
        emitter.emit("stage_started",
                     stage=stage,
                     input_ref=prev.shard_id if prev else None)

        try:
            result_ref = STAGE_HANDLERS[stage](store, prev)
        except Exception as e:
            emitter.emit("stage_failed", stage=stage, error=str(e))
            raise

        prev = write_checkpoint(store, stage, result_ref, pipeline_name)
        emitter.emit("stage_completed",
                     stage=stage,
                     output_ref=result_ref,
                     checkpoint_ref=prev.shard_id)

        print(f"  ✓ {stage} complete")

    store.delete_ref(f"job:{pipeline_name}:checkpoint")
    store.set_ref(f"job:{pipeline_name}:result", result_ref)

    emitter.emit("pipeline_completed", pipeline=pipeline_name, result_ref=result_ref)

    events = emitter.get_events()
    chain_ok = verify_chain(events)
    print(f"  Chain integrity: {'✓' if chain_ok else '✗'} ({len(events)} events)")

    mermaid = render_trace(events, diagram_type="workflow")

    return {
        "result_ref": result_ref,
        "chain_intact": chain_ok,
        "event_count": len(events),
        "provenance_mermaid": mermaid,
    }
```

The `delete_ref` + `set_ref` pair at the end is deliberate: clear the checkpoint pointer (so a re-run starts fresh) and store a stable handle to the final result.

## Multi-Agent Pipelines

Different stages can run on different models or agents. Map stage names to agent identifiers:

```python
AGENT_MAP = {
    "ingest":   "haiku",    # cheap, mechanical
    "extract":  "haiku",    # structured extraction
    "generate": "sonnet",   # creative work
    "validate": "opus",     # quality judgment
    "assemble": "local",    # deterministic, no LLM
}
```

Give each agent its own `TraceEmitter` with its `agent_id`. The trace events show exactly which agent did what:

```
stage_started   {agent_id: "haiku",  stage: "extract"}
stage_completed {agent_id: "haiku",  stage: "extract", cost_usd: 0.005}
stage_started   {agent_id: "sonnet", stage: "generate"}
stage_completed {agent_id: "sonnet", stage: "generate", cost_usd: 0.02}
```

`render_trace(events, diagram_type="multi-agent")` produces a swim-lane diagram showing each agent's contribution.

## Budget Tracking

`BudgetTracker` enforces a hard cap. It raises `StudioRunnerError` when a spend would exceed the budget — fail-loud rather than silently overspending:

```python
from spiritwriter.fabric.studio_runner import BudgetTracker

tracker = BudgetTracker(
    budget_usd=1.00,                # hard cap
    token_id="tok-001",             # optional — for trace correlation
    tracer=emitter,                 # optional — emits budget_spent events
)

tracker.record(label="extract:llm_call_1", amount=0.05)
tracker.record(label="generate:llm_call_2", amount=0.42)

tracker.spent       # 0.47
tracker.remaining   # 0.53
tracker.can_spend(0.10)   # True
tracker.can_spend(1.00)   # False — would exceed budget

tracker.summary()
# {"budget_usd": 1.0, "spent_usd": 0.47, "remaining_usd": 0.53, "entries": [...]}
```

Note: the API is `record(label, amount)`, not `spend(amount, label)`. Arg order is label first.

## Scoped Access via Entitlements

For sensitive inputs, encrypt the input shard and grant scoped access per agent:

```python
from spiritwriter.fabric.crypto import generate_job_key
from spiritwriter.fabric.entitlement import create_entitlement, Capability

key = generate_job_key()
encrypted = store.encrypt_and_store(input_shard, key)

token = create_entitlement(
    granted_to="haiku-extractor",
    granted_by="pipeline-orchestrator",
    shard_keys={encrypted.shard_id: key},   # raw bytes; create_entitlement serializes
    scopes=["project:my-pipeline"],
    capabilities=[Capability.SHARD_READ],
    secrets=[],                              # no secret-store entitlements
    budget_usd=0.10,
    expires_at="2026-12-31T23:59:59Z",       # ISO timestamp; optional
)

# The agent hydrates with their token; the store enforces all checks
context = store.hydrate_with_entitlement(token)
```

See [encryption.md](encryption.md#entitlement-tokens) for the full validation order (expiry → capability → scope) and the per-shard-key distribution pattern.

## Per-Stage Overhead

| What you add | Cost | When |
|--------------|------|------|
| `emitter.emit("stage_started", ...)` | 1 line | Before stage logic |
| `emitter.emit("stage_completed", ...)` | 1 line | After stage logic |
| `write_checkpoint(...)` | 1 call (~15 lines in helper) | After stage logic |
| `get_resume_stage(...)` | 1 call (~15 lines in helper) | At pipeline start |
| `verify_chain(events)` | 1 line | At pipeline end |
| `render_trace(events, diagram_type=...)` | 1 line | At pipeline end |
| `BudgetTracker.record(label, amount)` | 1 line per LLM call | Optional |
| Entitlement setup | ~10 lines | Optional, for sensitive data |

**Total per stage: 2-3 lines.** The helpers are written once and reused across pipelines.

## Provenance Reports

The trace JSONL is the source of truth for both audit and visualization. Three diagram types from the same events:

```python
from spiritwriter.fabric.visualize import render_trace, load_trace

events = emitter.get_events()              # in-memory
# or: events = load_trace("run.jsonl")     # from disk

render_trace(events, diagram_type="workflow")     # stages and transitions
render_trace(events, diagram_type="genealogy")    # which shard derived from which
render_trace(events, diagram_type="multi-agent")  # which agent did what
```

Output is Mermaid markdown. Render as PNG/SVG with any Mermaid CLI or pipe through GitHub's auto-render.

## Claude Code Integration

For pipelines where each stage runs as a separate Claude Code invocation, the checkpoint pattern translates directly. Each invocation reads the checkpoint, runs one stage, writes the next checkpoint, exits. A shell loop drives the sequence:

```bash
STAGES=("ingest" "extract" "generate" "validate" "assemble")
for stage in "${STAGES[@]}"; do
    claude -p --permission-mode acceptEdits --max-turns 20 \
        "Read job:my-pipeline:checkpoint. Run $stage stage only. Write checkpoint. Exit." \
        2>&1 | tee "logs/$stage.log"

    python3 -c "
from spiritwriter.fabric.store import ShardStore
store = ShardStore('/path/to/shards')
c = store.resolve_ref('job:my-pipeline:checkpoint')
assert c.get_atom('stage').value == '${stage}_complete', 'Checkpoint missing!'
print('✓ $stage verified')
" || { echo "Stage $stage failed"; exit 1; }
done
```

Each invocation is a fresh process. The checkpoint shard is what carries state across them — Claude Code itself doesn't need to know about pipelines, only how to read and write shards.

## What This Pattern Is Not

- **Not transaction-safe across stages.** A crash mid-stage may leave the trace ahead of the checkpoint (the `stage_started` event written, no `stage_completed` yet). On resume, the chain is still valid, but the stage will re-run. Make stage handlers idempotent.
- **Not for sub-second steps.** The overhead (emit + checkpoint write) is dominated by filesystem syncs — each stage costs a few ms. Fine for stages measured in seconds; wasteful for stages measured in microseconds.
- **Not a replacement for retries.** If a stage fails transiently, this pattern lets you resume after a crash but doesn't decide *whether* to retry. Wrap stage handlers with your own retry logic before reaching for this.
- **Not concurrency-safe across writers.** One emitter per trace file. For parallel stages, give each its own trace file and merge after.

## File Reference

| File | What it does |
|------|--------------|
| `spiritwriter/fabric/shard.py` | `MemoryShard`, `ShardAtom`, content addressing |
| `spiritwriter/fabric/store.py` | `ShardStore`, refs, checkpoint persistence |
| `spiritwriter/fabric/emitter.py` | `TraceEmitter`, hash chain, `verify_chain` |
| `spiritwriter/fabric/visualize.py` | Mermaid diagram generation |
| `spiritwriter/fabric/crypto.py` | AES-256-GCM shard encryption |
| `spiritwriter/fabric/entitlement.py` | Scoped access tokens |
| `spiritwriter/fabric/studio_job.py` | Job packaging |
| `spiritwriter/fabric/studio_runner.py` | Job execution, `BudgetTracker` |
