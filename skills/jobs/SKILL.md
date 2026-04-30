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
| **JobSpec** | Job definition: task description, content shard refs, tool permissions, budget. |
| **PackagedJob** | Ready-to-spawn bundle: encrypted shards + entitlement token + task text. |
| **BudgetTracker** | Spend monitor. Tracks cumulative cost, raises on over-budget. |
| **Result shard** | Output shard linked to the job by trace chain. Separate from input (never mutate source). |

## Python API

### Package a job

```python
from spiritwriter.fabric.jobs import JobSpec, package_job

spec = JobSpec(
    task="Analyze this paper and extract key findings",
    content_refs=[shard_ref_1, shard_ref_2],
    tools=["web_search", "read"],
    budget_cents=200,       # $2.00 max
    ttl_seconds=1800,       # 30 min
)

packaged = package_job(spec, store, issuer="lilit")
# packaged.entitlement_token — scoped access
# packaged.encrypted_shards — encrypted content
# packaged.spawn_task_text() — ready-to-spawn XML with embedded job block
```

### Run a job (sub-agent side)

```python
from spiritwriter.fabric.runner import parse_job_block, hydrate_job, BudgetTracker

# Parse the job block from task text
job_block = parse_job_block(task_text)

# Hydrate: validate token, decrypt shards, get context
context = hydrate_job(job_block, store)

# Track budget
tracker = BudgetTracker(budget_cents=job_block["budget_cents"])
tracker.spend(50, "llm_call_1")
tracker.spend(30, "llm_call_2")
# tracker.spend(200, "big_call")  # raises BudgetExceeded
```

### Return results

```python
from spiritwriter.fabric.runner import create_result_shard

result = create_result_shard(
    atoms=[...],            # extracted knowledge
    job_id=job_block["job_id"],
    scope=job_block["scope"],
    agent_id="sub-agent-007",
)
store.put(result)
```

## Job Lifecycle (Trace Events)

```
job_packaged → entitlement_granted → job_started →
  capability_checked → shard_decrypted (×N) →
  budget_spent (×N) →
job_completed (or job_failed)
```

Every transition is hash-chained via TraceEmitter. Full provenance from packaging to result.

## Design Principles

- **Input and output are separate shards** — never write back to source
- **Budget is double-protected** — entitlement cap + BudgetTracker enforcement
- **Jobs are self-contained** — spawn_task_text() includes everything the sub-agent needs
- **Trace chain links everything** — job → token → decryptions → spends → result

## Source Files

- `spiritwriter/fabric/jobs.py` — JobSpec, PackagedJob, package_job()
- `spiritwriter/fabric/runner.py` — parse_job_block(), hydrate_job(), BudgetTracker, create_result_shard()
