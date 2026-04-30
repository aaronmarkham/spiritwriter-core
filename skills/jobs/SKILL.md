# Skill: Spiritwriter Jobs

Package, dispatch, and execute scoped sub-agent work with budget tracking and trace provenance.

## When to Use

- You need to **package a task with encrypted context** for a sub-agent
- You need to **run a job with budget limits** (prevent runaway spend)
- You need to **track job lifecycle** (packaged → started → completed/failed)
- You need to **return results as shards** linked to the source job

## Install

```bash
pip install -e /path/to/spiritwriter-core
```

## Concepts

| Concept | What it is |
|---------|-----------|
| **JobSpec** | Minimal generic spec: prompt, budget, free-form constraints. Subclass for richer task shapes. |
| **PackagedJob** | Ready-to-spawn bundle: encrypted content/task shards + entitlement token + task text. |
| **BudgetTracker** | Spend monitor. Tracks cumulative cost, raises `JobRunnerError` on over-budget. |
| **Result shard** | Output shard linked to the job by trace chain. Separate from input (never mutate source). |

## Python API

### Package a job (issuer)

```python
from spiritwriter.fabric.jobs import JobSpec, package_job
from spiritwriter.fabric.shard import ShardAtom, AtomKind
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.emitter import TraceEmitter

store = ShardStore("/path/to/shards")
tracer = TraceEmitter(run_id="run-001", agent_id="orchestrator", out_path="/tmp/trace.jsonl")

content_atoms = [
    ShardAtom(text="Source paragraph 1", kind=AtomKind.FACT, key="src.0"),
    ShardAtom(text="Source paragraph 2", kind=AtomKind.FACT, key="src.1"),
]

spec = JobSpec(
    prompt="Summarize the source material in three bullet points",
    budget_usd=2.00,                            # hard cap
    constraints={"max_words": "60"},            # optional, becomes constraint.<key> atoms
)

pkg = package_job(store, content_atoms, spec, tracer=tracer)

# pkg.entitlement_token  — scoped access
# pkg.content_shard_id / pkg.task_shard_id  — encrypted shard ids
# pkg.spawn_task_text()  — ready-to-spawn <sw-job> block + instruction prelude
```

### Run a job (sub-agent side)

```python
from spiritwriter.fabric.runner import (
    hydrate_job, BudgetTracker, create_result_shard, JobRunnerError,
)

# task_text arrives in the sub-agent's prompt and contains the <sw-job> block
job = hydrate_job(store, task_text, tracer=tracer)

print(job.prompt)            # "Summarize the source material in three bullet points"
print(job.budget_usd)        # 2.0
print(job.config["constraint.max_words"])    # "60"

# Track spend (label first, amount second)
tracker = BudgetTracker(
    budget_usd=job.budget_usd,
    token_id=job.token.token_id,
    tracer=tracer,
)
tracker.record("llm:summarize", 0.40)
# tracker.record("retry", 2.00)  # raises JobRunnerError — would exceed cap
```

### Return results

```python
result = create_result_shard(job, {
    "budget": tracker.summary(),
    "outputs": [{"type": "summary", "ref": "..."}],
    "warnings": [],
})
store.put(result)

tracer.job_completed(
    token_id=job.token.token_id,
    result_shard_id=result.shard_id,
    spent_usd=tracker.spent,
    outputs=[{"type": "summary", "ref": "..."}],
)
```

## Job Lifecycle (Trace Events)

```
entitlement_granted → job_packaged → capability_checked →
  shard_decrypted (×2: content, task) → job_started →
  budget_spent (×N) →
job_completed (or job_failed)
```

Every transition is hash-chained via `TraceEmitter`. Full provenance from packaging to result. See [docs/tracing.md](../../docs/tracing.md) for chain mechanics.

## Design Principles

- **Input and output are separate shards** — never write back to source
- **Budget is double-protected** — entitlement cap + `BudgetTracker` enforcement at the call site
- **Jobs are self-contained** — `spawn_task_text()` includes everything the sub-agent needs
- **Trace chain links everything** — entitlement → decryptions → spends → result
- **Subclass for richer specs** — `JobSpec` is intentionally minimal; override `to_atoms()` to add fields without breaking the contract

## Source Files

- `spiritwriter/fabric/jobs.py` — `JobSpec`, `PackagedJob`, `package_job()`
- `spiritwriter/fabric/runner.py` — `parse_job_block()`, `hydrate_job()`, `JobContext`, `BudgetTracker`, `create_result_shard()`, `JobRunnerError`

For the full guide, see [docs/jobs.md](../../docs/jobs.md).
