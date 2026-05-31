# Delegated Jobs

A **job** is a packaged unit of sub-agent work: encrypted content shards, an encrypted task shard with the production instructions, and an [entitlement token](entitlements.md) that tells the sub-agent what it's allowed to read, do, and spend. The main agent packages the job; spawns a sub-agent with the token; the sub-agent hydrates, executes, and writes a result shard. Every step is traced.

The pattern is generic — it works for any sub-agent task where you need scoped access, capped spend, and tamper-evident provenance. The historical reason it lives in core (and why some defaults look video-flavored) is that this code was extracted from [Claude Studio Producer](https://github.com/aaronmarkham/claude-studio-producer); CSP is a consumer of the pattern, not the only one.

## Pieces

- **`JobSpec`** — what to produce. Prompt, optional shape constraints, budget cap.
- **`PackagedJob`** — what gets shipped. Two encrypted shard ids + the entitlement token + the in-memory job key.
- **`package_job()`** — issuer side. Encrypts content + task shards under one key, mints an entitlement, persists everything to the store, returns the `PackagedJob`.
- **`hydrate_job()`** — runner side. Parses the `<sw-job>` block, validates the token, decrypts both shards, returns a `JobContext`.
- **`JobContext`** — the runner's view of the work. Exposes the prompt, the config, the budget cap, and the rendered content text.
- **`BudgetTracker`** — accumulates spend and refuses calls that would exceed the cap.
- **`create_result_shard()`** — packages the output (cost summary, output refs, warnings) into a `MemoryShard` linked back to the job.

## Quick Start

### Issuer side

```python
from spiritwriter.fabric.shard import ShardAtom, AtomKind
from spiritwriter.fabric.store import ShardStore
from spiritwriter.fabric.jobs import JobSpec, package_job
from spiritwriter.fabric.emitter import TraceEmitter

store = ShardStore("/path/to/shards")
tracer = TraceEmitter(run_id="run-001", agent_id="orchestrator", out_path="/tmp/run.jsonl")

content_atoms = [
    ShardAtom(text="Source paragraph 1", kind=AtomKind.FACT, key="src.0"),
    ShardAtom(text="Source paragraph 2", kind=AtomKind.FACT, key="src.1"),
]

spec = JobSpec(
    prompt="Summarize the source material in three bullet points",
    budget_usd=2.0,
    constraints={"max_words": "60"},
)

pkg = package_job(store, content_atoms, spec, agent_id="orchestrator", tracer=tracer)

# Spawn the sub-agent with the wrapped task text
task_text = pkg.spawn_task_text()
# ...sessions_spawn(task_text=task_text)...
```

`package_job` does five things: generates a single AES key for both shards, encrypts and stores them, mints an entitlement with that key, emits `entitlement_granted` + `job_packaged` trace events, and returns the `PackagedJob`. The job key lives only on the returned `PackagedJob`; it is never persisted.

### Runner side

```python
from spiritwriter.fabric.runner import (
    hydrate_job, BudgetTracker, create_result_shard, JobRunnerError,
)

# task_text arrives in the sub-agent's prompt, containing the <sw-job> block
job = hydrate_job(store, task_text, tracer=tracer)

print(job.prompt)             # "Summarize the source material in three bullet points"
print(job.budget_usd)         # 2.0
print(job.config["constraint.max_words"])    # "60"
print(job.content_text)       # rendered atoms from the content shard

tracker = BudgetTracker(
    budget_usd=job.budget_usd,
    token_id=job.token.token_id,
    tracer=tracer,
)

# do the work, recording cost as you go
response = call_llm(job.prompt, job.content_text)
tracker.record("llm:claude_sonnet", response.cost_usd)

# write the result
result = create_result_shard(job, {
    "budget": tracker.summary(),
    "outputs": [{"type": "summary", "ref": response.text}],
})
store.put(result)

tracer.job_completed(
    token_id=job.token.token_id,
    result_shard_id=result.shard_id,
    spent_usd=tracker.spent,
    outputs=[{"type": "summary", "ref": response.text}],
)
```

`hydrate_job` runs four checks before returning: token not expired, token has `SHARD_READ`, content shard decrypts, task shard decrypts. Any failure raises `JobRunnerError` and the runner emits a `capability_checked(allowed=False)` event so the audit trail shows the attempt.

## JobSpec

```python
@dataclass
class JobSpec:
    prompt: str                                       # what to produce
    budget_usd: float = 10.0                          # spend cap
    constraints: dict[str, Any] = field(default_factory=dict)
```

Only `prompt` is required. `JobSpec.to_atoms()` projects the spec into three kinds of `ShardAtom`s — one for the prompt (`production_prompt`), one for the budget (`budget_limit`), and one per constraint (`constraint.<key>`).

`constraints` is the escape hatch for caller-defined task shape — `{"max_words": "60", "tier": "standard"}`. Each entry becomes a `constraint.<key>` atom on the task shard, accessible from the runner side via `job.config["constraint.max_words"]`. Use this for shape that's specific to your job but doesn't justify a subclass.

**Constraint values are stringified with f-string `{v}` formatting**, so a non-string value lands in the atom as its `str()` repr — `{"items": [1, 2, 3]}` becomes the literal text `items: [1, 2, 3]`. For predictable atom text use `dict[str, str]`; for richer shapes, subclass and emit your own atoms with explicit serialization.

### Subclassing for Richer Specs

When the constraint dict starts feeling stringly-typed, subclass `JobSpec` and add real fields. The contract is: call `super().to_atoms()` first so the prompt and budget atoms stay in stable positions, then append your own:

```python
from dataclasses import dataclass
from spiritwriter.fabric.jobs import JobSpec
from spiritwriter.fabric.shard import ShardAtom, AtomKind

@dataclass
class VideoJobSpec(JobSpec):
    style: str = "explainer"
    output_format: str = "mp4"
    duration_seconds: int = 60
    voice: str = "nova"
    upload_target: str | None = None

    def to_atoms(self):
        atoms = super().to_atoms()
        atoms.append(ShardAtom(
            text=f"Style: {self.style}, Duration: {self.duration_seconds}s, "
                 f"Voice: {self.voice}, Format: {self.output_format}",
            kind=AtomKind.INSTRUCTION,
            key="production_config",
        ))
        if self.upload_target:
            atoms.append(ShardAtom(
                text=f"Upload to: {self.upload_target}",
                kind=AtomKind.INSTRUCTION,
                key="upload_target",
            ))
        return atoms
```

Pass an instance of the subclass anywhere a `JobSpec` is expected — `package_job(store, atoms, VideoJobSpec(prompt=..., output_format="mov"), agent_id="orchestrator")`. The runner side reads atoms by `key`, so adding new keys is always safe; renaming or removing a key is the breaking change to watch.

## PackagedJob

```python
@dataclass
class PackagedJob:
    content_shard_id: str
    task_shard_id: str
    entitlement_token: EntitlementToken
    job_key: bytes        # in-memory only — never persist
```

`PackagedJob.spawn_task_text()` returns the formatted `<sw-job>` block plus a short instruction prelude:

```
<sw-job>
<entitlement>{...serialized token...}</entitlement>
<content-shard>sha256_content_id...</content-shard>
<task-shard>sha256_task_id...</task-shard>
</sw-job>

You are a job runner agent. Parse the <sw-job> block above.
Use the entitlement token to decrypt and hydrate the content and task shards.
Execute the production task according to the task shard instructions.
Track all spending against the budget limit.
Report results when complete.
```

Pass that string as the sub-agent's task text. The marker is `<sw-job>` (namespaced to spiritwriter) rather than the more generic `<job>`, which would collide with prose like "your job is to..." or with HTML/XML content passed as input.

## Hydration

`hydrate_job(store, task_text, tracer=None) -> JobContext` is the runner's entry point. It walks `parse_job_block` → `deserialize_token` → expiry check → capability check → decrypt content shard → decrypt task shard, emitting trace events at each step.

`JobContext` exposes:

| Field / property | What it is |
|---|---|
| `token` | The deserialized `EntitlementToken` |
| `content_shard`, `task_shard` | Decrypted `MemoryShard`s |
| `content_shard_id`, `task_shard_id` | Stable handles for trace correlation |
| `prompt` | Production prompt text (or `None` if missing) |
| `budget_usd` | Mirrors `token.budget_usd` |
| `config` | All task atoms keyed by their `key` field — `{"production_prompt": "...", "constraint.max_words": "60", ...}` |
| `content_text` | `content_shard.hydrate_context()` — atoms rendered as readable text |

`hydrate_job` does *not* construct a `BudgetTracker` — wire one up explicitly with `BudgetTracker(budget_usd=job.budget_usd, token_id=job.token.token_id, tracer=tracer)` so it can emit `budget_spent` events under the same trace chain.

If the runner is called outside a `<sw-job>` context (manual testing, fixture replay), use `parse_job_block(task_text)` directly to get the `(token_str, content_shard_id, task_shard_id)` tuple without the decrypt step.

## Budget Tracking

```python
@dataclass
class BudgetTracker:
    budget_usd: float
    token_id: str | None = None
    tracer: TraceEmitter | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)
```

Cost flows in from the *application*, not from the library. After each LLM call, provider call, or any other paid action, read the cost off the response and call `tracker.record(label, amount)`:

```python
tracker = BudgetTracker(budget_usd=job.budget_usd, token_id=job.token.token_id, tracer=tracer)

response = anthropic_client.messages.create(model="claude-sonnet-4-6", ...)
# anthropic.types.Usage exposes input_tokens/output_tokens/cache_*; turn that into USD
# via your provider's price table (or use a billing helper that does it for you)
sonnet_cost = compute_cost_usd(response.usage, model="claude-sonnet-4-6")
tracker.record("llm:sonnet", sonnet_cost)                # raises JobRunnerError on overflow

tts = elevenlabs_client.tts(text=script, voice=...)
tracker.record("tts:elevenlabs", tts.cost_usd)
```

The arg order is `record(label, amount)` — label first, amount second. `record()` raises `JobRunnerError("Budget exceeded: ...")` on the call that would push spend past `budget_usd`; previous successful records are preserved on `tracker.entries`. To check before calling, use `tracker.can_spend(amount)` for a non-raising query.

Properties:

```python
tracker.spent       # sum of all recorded amounts
tracker.remaining   # budget_usd - spent
tracker.summary()   # {"budget_usd": ..., "spent_usd": ..., "remaining_usd": ..., "entries": [...]}
```

When `tracer` and `token_id` are both set, every `record()` call emits a `budget_spent` event with the label, amount, running total, and budget cap — the audit trail then shows exactly where the money went.

## Result Shards

`create_result_shard(job, results, agent_id="job-runner")` packages a result dict into a `MemoryShard` linked back to the job's content/task shards via `meta`:

```python
result = create_result_shard(job, {
    "budget": tracker.summary(),
    "outputs": [
        {"type": "summary", "ref": "https://...", },
        {"type": "transcript", "ref": "/tmp/transcript.txt"},
    ],
    "warnings": ["LLM returned 4 bullets instead of 3 — kept anyway"],
})
store.put(result)
```

The result shard's scope is derived from the content shard's scope — `:content` is replaced with `:result`. So a job under `scope_prefix="extract"` produces a content shard at `extract:content` and a result shard at `extract:result`. Encrypt the result with the same `job_key` if the result needs the same access boundary as the input.

The result shard is **separate from the inputs by design** — never mutate or write back over the source. Atom-level provenance lives in the result shard's `meta` (`content_shard_id`, `task_shard_id`, `completed_at`), not in the source.

## Job Lifecycle Trace

A complete job emits eight events when issuer and runner both pass a `tracer`:

```
entitlement_granted   ← package_job()
  job_packaged        ← package_job()
    capability_checked(shard:read, allowed=True)   ← hydrate_job()
    shard_decrypted (content)                      ← hydrate_job()
    shard_decrypted (task)                         ← hydrate_job()
    job_started                                    ← hydrate_job()
      budget_spent (×N)                            ← BudgetTracker.record()
    job_completed (or job_failed)                  ← runner emits manually
```

`verify_chain(events)` returns `True` for an intact chain. The chain ends with one of `job_completed` or `job_failed`, and `verify_chain` cannot detect tail-truncation — emit one of those terminal events and have the consumer assert it's present. See [tracing.md](tracing.md#chain-verification) for the full mechanics.

## Composing Jobs

Several patterns compose without changing the primitives.

**Per-pilot fan-out.** Allocate one budget tracker per pilot, share the run via the trace `run_id`:

```python
pilots = [
    BudgetTracker(budget_usd=10.0, token_id=f"pilot-{name}", tracer=tracer)
    for name in ("A", "B", "C")
]
# spawn three sub-agents, each with its own packaged job; merge results on the orchestrator
```

**Multi-stage pipeline.** Each stage is its own job — the result shard of stage N becomes the content of stage N+1. The trace chain spans all stages because every emitter shares the same `out_path` (or you concatenate per-stage JSONL files at the end).

**Resumable jobs.** Persist the `PackagedJob` ids to a checkpoint shard at every successful stage; on restart, look up the checkpoint and resume from the next stage. See [traced-workflows.md](traced-workflows.md) for the full pattern.

**Lineage through encryption.** `package_job()` builds its own encrypted content shard internally — that shard's id is different from any plaintext content shard you might keep on the orchestrator side. When pinning a result shard's `parent_shard_id`, pin it at the **plaintext predecessor** (the atoms you cared about), not the encrypted job-internal shard. The worked example at [`examples/06_phalanx_flow/`](../examples/06_phalanx_flow/) shows the pattern: extracted-paper shard → delegated summarization → result shard linked back to the extracted-paper shard, not the encrypted shipping container.

## What This Layer Is Not

- **Not async.** `package_job` and `hydrate_job` are synchronous. Drive concurrency at the orchestrator layer (asyncio, threads, or multiple sub-agent processes).
- **Not a job queue.** No persistent dispatch, no retry, no dead-letter handling — those belong in the application. Jobs persist their state via the store; restart logic is the caller's responsibility.
- **Not a sandbox.** Capabilities gate what the runner *attempts*; they don't enforce what the runner *can do* in a hostile environment. Combine with process-level sandboxing if the sub-agent runs untrusted code.
- **Not auto-billed.** `BudgetTracker` only tracks what your code calls `record()` on. An LLM call you make without recording the cost is free as far as the tracker knows.
