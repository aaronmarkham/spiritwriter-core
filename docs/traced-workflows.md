# Building Traced Workflows with Spiritwriter

How to add provenance tracking, checkpoints, and resume to any multi-stage pipeline.

## Overview

A traced workflow wraps your existing business logic with:
- **Hash-chained trace events** — tamper-evident receipts for every stage
- **Checkpoint shards** — resumable state between stages
- **Budget tracking** — cost per stage, per agent
- **Provenance reports** — Mermaid diagrams showing the full pipeline lineage

Build cost: ~50 lines of glue on top of your existing logic. The tracing infrastructure is all library calls.

## Prerequisites

```bash
pip install -e /path/to/spiritwriter-core
```

Read the relevant skills for deeper reference:
- `skills/shards/SKILL.md` — shard format, storage, hydration
- `skills/trace/SKILL.md` — trace events, hash chains, visualization
- `skills/studio/SKILL.md` — job packaging, budget tracking (optional)
- `skills/entitlements/SKILL.md` — scoped access control (optional)

## Quick Start

### 1. Define Your Stages

```python
STAGES = ["ingest", "extract", "generate", "validate", "assemble"]
```

### 2. Set Up Store and Emitter

```python
from spiritwriter.trace.emitter import TraceEmitter
from spiritwriter.trace.store import ShardStore

store = ShardStore("/path/to/shards")
emitter = TraceEmitter(agent_id="my-pipeline", output_path="run.jsonl")
```

### 3. Wrap Each Stage

Before and after your existing logic, add two lines:

```python
# Before
emitter.emit("stage_started", {"stage": "extract", "input_ref": input_shard.shard_id})

# ... your existing logic here ...

# After
emitter.emit("stage_completed", {"stage": "extract", "output_ref": output_shard.shard_id})
```

### 4. Write Checkpoints

After each stage, persist state as a shard:

```python
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass

checkpoint = MemoryShard(
    atoms=[
        ShardAtom(
            text="Stage complete",
            kind=AtomKind.CHECKPOINT,
            key="stage",
            value="extract_complete",
        ),
        ShardAtom(
            text="Reference to intermediate output",
            kind=AtomKind.CONTEXT,
            key="output_shard_ref",
            value=output_shard.shard_id,
        ),
    ],
    scope="jobs:in-progress",
    origin="my-pipeline",
    decay_class=DecayClass.CHECKPOINT,  # 4 hours
    tags=["checkpoint", "my-pipeline"],
)

ref = store.put(checkpoint)
store.set_ref("job:my-pipeline:checkpoint", ref.shard_id)
```

### 5. Resume from Checkpoints

On startup, check where you left off:

```python
def get_resume_stage(store, stages, job_ref="job:my-pipeline:checkpoint"):
    """Find which stage to resume from."""
    shard = store.resolve_ref(job_ref)
    if shard is None:
        return stages[0], None  # start from beginning

    atom = shard.get_atom("stage")
    if atom is None:
        return stages[0], None

    # "extract_complete" → resume from next stage after "extract"
    completed = atom.value.replace("_complete", "")
    if completed in stages:
        next_idx = stages.index(completed) + 1
        if next_idx < len(stages):
            return stages[next_idx], shard
    
    return stages[0], None
```

### 6. Verify Chain and Generate Report

After all stages complete:

```python
from spiritwriter.trace.emitter import verify_chain
from spiritwriter.trace.visualize import render_trace

# Verify no tampering
events = emitter.get_events()
assert verify_chain(events), "Trace chain integrity check failed!"

# Generate Mermaid provenance diagram
mermaid_code = render_trace("run.jsonl", diagram_type="workflow")
```

## Complete Pipeline Template

```python
"""Traced workflow template — copy and customize."""

from spiritwriter.trace.emitter import TraceEmitter, verify_chain
from spiritwriter.trace.store import ShardStore
from spiritwriter.trace.shard import MemoryShard, ShardAtom, AtomKind, DecayClass
from spiritwriter.trace.visualize import render_trace


STAGES = ["ingest", "extract", "generate", "validate", "assemble"]

# Map stage names to your actual functions
STAGE_HANDLERS = {
    "ingest": lambda store, prev: do_ingest(store, prev),
    "extract": lambda store, prev: do_extract(store, prev),
    "generate": lambda store, prev: do_generate(store, prev),
    "validate": lambda store, prev: do_validate(store, prev),
    "assemble": lambda store, prev: do_assemble(store, prev),
}


def write_checkpoint(store, stage, result_ref, pipeline_name):
    """Write a checkpoint shard after a stage completes."""
    checkpoint = MemoryShard(
        atoms=[
            ShardAtom(
                text=f"Stage {stage} complete",
                kind=AtomKind.CHECKPOINT,
                key="stage",
                value=f"{stage}_complete",
            ),
            ShardAtom(
                text="Output reference",
                kind=AtomKind.CONTEXT,
                key="output_ref",
                value=result_ref,
            ),
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
    """Determine which stage to resume from."""
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
    """Run a traced pipeline with checkpoint/resume."""
    store = ShardStore(store_path)
    emitter = TraceEmitter(agent_id=pipeline_name, output_path=trace_path)

    resume_stage, checkpoint = get_resume_stage(store, STAGES, pipeline_name)
    print(f"Resuming from: {resume_stage}")

    emitter.emit("pipeline_started", {
        "pipeline": pipeline_name,
        "resume_from": resume_stage,
    })

    prev = checkpoint
    for stage in STAGES[STAGES.index(resume_stage):]:
        # Emit receipt: starting
        emitter.emit("stage_started", {
            "stage": stage,
            "input_ref": prev.shard_id if prev else None,
        })

        # Run the actual work
        try:
            result_ref = STAGE_HANDLERS[stage](store, prev)
        except Exception as e:
            emitter.emit("stage_failed", {"stage": stage, "error": str(e)})
            raise

        # Write checkpoint + emit receipt: completed
        prev = write_checkpoint(store, stage, result_ref, pipeline_name)
        emitter.emit("stage_completed", {
            "stage": stage,
            "output_ref": result_ref,
            "checkpoint_ref": prev.shard_id,
        })

        print(f"  ✓ {stage} complete")

    # Clean up checkpoint, write result ref
    store.delete_ref(f"job:{pipeline_name}:checkpoint")
    store.set_ref(f"job:{pipeline_name}:result", result_ref)

    emitter.emit("pipeline_completed", {
        "pipeline": pipeline_name,
        "result_ref": result_ref,
    })

    # Verify chain integrity
    events = emitter.get_events()
    chain_ok = verify_chain(events)
    print(f"  Chain integrity: {'✓' if chain_ok else '✗'} ({len(events)} events)")

    # Generate provenance report
    mermaid = render_trace(trace_path, diagram_type="workflow")

    return {
        "result_ref": result_ref,
        "chain_intact": chain_ok,
        "event_count": len(events),
        "provenance_mermaid": mermaid,
    }
```

## Multi-Agent Pipelines

Different stages can be run by different agents/models:

```python
# Stage → agent mapping
AGENT_MAP = {
    "ingest": "haiku",       # cheap, mechanical
    "extract": "haiku",      # structured extraction
    "generate": "sonnet",    # creative script writing
    "validate": "opus",      # quality judgment
    "assemble": "local",     # deterministic, no LLM
}
```

Each agent gets its own TraceEmitter with its `agent_id`. The trace events show exactly which model did which stage:

```
stage_started  {agent: "haiku",  stage: "extract"}
stage_completed {agent: "haiku",  stage: "extract", cost: 0.005}
stage_started  {agent: "sonnet", stage: "generate"}
stage_completed {agent: "sonnet", stage: "generate", cost: 0.02}
```

### With Budget Tracking

```python
from spiritwriter.trace.studio_runner import BudgetTracker

tracker = BudgetTracker(budget_cents=100)  # $1.00 cap

# After each LLM call
tracker.spend(cost_cents, f"{stage}:{call_description}")

# At end
print(tracker.summary())
# {"total_spent": 42, "budget_remaining": 58, "line_items": [...]}
```

### With Scoped Access (Entitlements)

For sensitive inputs, encrypt and scope access per agent:

```python
from spiritwriter.trace.crypto import encrypt_shard, generate_key
from spiritwriter.trace.entitlement import create_entitlement, Capability

# Encrypt the input
key = generate_key()
encrypted = store.encrypt_and_store(input_shard, key)

# Grant scoped access to the extraction agent
token = create_entitlement(
    issuer="pipeline-orchestrator",
    subject="haiku-extractor",
    scopes=["project:my-pipeline"],
    capabilities=[Capability.SHARD_READ],
    shard_keys={encrypted.shard_id: key},
    budget_cents=10,
    ttl_seconds=300,
)

# Agent hydrates with entitlement
context = store.hydrate_with_entitlement(token)
```

## Per-Stage Overhead

| What you add | Lines | When |
|-------------|-------|------|
| `emitter.emit("stage_started", ...)` | 1 | Before your logic |
| `emitter.emit("stage_completed", ...)` | 1 | After your logic |
| `write_checkpoint(...)` | 1 call (~15 lines in helper) | After your logic |
| `get_resume_stage(...)` | 1 call (~15 lines in helper) | At pipeline start |
| `verify_chain(...)` | 1 | At pipeline end |
| `render_trace(...)` | 1 | At pipeline end |
| `BudgetTracker.spend(...)` | 1 per LLM call | Optional |
| Entitlement setup | ~10 | Optional, for sensitive data |

**Total per stage: 2-3 lines.** The helpers are written once and reused across pipelines.

## Provenance Report

The trace JSONL file is a complete audit trail. Render it as:

```python
# Simple workflow diagram
render_trace("run.jsonl", diagram_type="workflow")

# Shard genealogy (which shards derived from which)
render_trace("run.jsonl", diagram_type="genealogy")

# Multi-agent view (which agent did what)
render_trace("run.jsonl", diagram_type="multi_agent")
```

Output is Mermaid markdown, renderable as PNG/SVG by any Mermaid tool.

## Claude Code Integration

For CC-driven stages, use the checkpoint/resume pattern:

```bash
# Each CC invocation = one stage
claude -p --permission-mode acceptEdits --max-turns 20 \
  "Read job:my-pipeline:checkpoint. Run extract stage. Write checkpoint. Exit."

# Verify checkpoint before advancing
python3 -c "
from spiritwriter.trace.store import ShardStore
store = ShardStore('/path/to/shards')
c = store.resolve_ref('job:my-pipeline:checkpoint')
assert c.get_atom('stage').value == 'extract_complete'
print('✓ checkpoint verified')
"

# Next stage
claude -p --permission-mode acceptEdits --max-turns 20 \
  "Read job:my-pipeline:checkpoint. Run generate stage. Write checkpoint. Exit."
```

Or automate with a shell loop:

```bash
STAGES=("ingest" "extract" "generate" "validate" "assemble")
for stage in "${STAGES[@]}"; do
    claude -p --permission-mode acceptEdits --max-turns 20 \
        "Read job:my-pipeline:checkpoint. Run $stage stage only. Write checkpoint. Exit." \
        2>&1 | tee "logs/$stage.log"
    
    python3 -c "
from spiritwriter.trace.store import ShardStore
store = ShardStore('/path/to/shards')
c = store.resolve_ref('job:my-pipeline:checkpoint')
assert c.get_atom('stage').value == '${stage}_complete', 'Checkpoint missing!'
print('✓ $stage verified')
" || { echo "Stage $stage failed"; exit 1; }
done
```

## File Reference

| File | What it does |
|------|-------------|
| `spiritwriter/trace/shard.py` | MemoryShard, ShardAtom, content addressing |
| `spiritwriter/trace/store.py` | ShardStore, refs, checkpoint persistence |
| `spiritwriter/trace/emitter.py` | TraceEmitter, hash chain, verify_chain |
| `spiritwriter/trace/visualize.py` | Mermaid diagram generation |
| `spiritwriter/trace/crypto.py` | AES-256-GCM shard encryption |
| `spiritwriter/trace/entitlement.py` | Scoped access tokens |
| `spiritwriter/trace/studio_job.py` | Job packaging |
| `spiritwriter/trace/studio_runner.py` | Job execution, BudgetTracker |
