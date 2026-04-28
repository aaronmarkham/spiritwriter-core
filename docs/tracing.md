# Tracing and Provenance

A **TraceEmitter** writes hash-chained JSONL event logs — a tamper-evident audit trail covering agent actions, shard lifecycle, and entitlement usage. Every event SHA-256s its own payload and links to the previous event's hash. Mutate any event after the fact, the chain breaks, and `verify_chain()` returns `False`.

This is not a database — there's no query layer, no indexing, no aggregation. It's a structured receipt log that an auditor or another agent can replay to answer "what happened, in what order, and prove nothing's been edited."

## Event Shape

Every event is a JSON object on its own line:

```json
{
  "type": "shard_created",
  "run_id": "run-001",
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "ts": "2026-04-17T14:30:00Z",
  "agent_id": "orchestrator",
  "prev_event_hash": "abc123...",
  "shard_id": "def456...",
  "scope": "project:myapp",
  "atom_count": 5,
  "hash": "789xyz..."
}
```

The `hash` field covers everything except itself and `sig`. The `prev_event_hash` field links the chain — `null` for the first event, the previous event's `hash` thereafter. Optional `sig` adds an Ed25519 signature for non-repudiation.

## Quick Start

```python
from spiritwriter.fabric.emitter import TraceEmitter, verify_chain

emitter = TraceEmitter(
    run_id="run-2026-04-17-001",
    agent_id="orchestrator",
    out_path="/tmp/traces/run-001.jsonl",
)

# emit() takes the event type and arbitrary keyword fields
evt = emitter.emit("task_started", task="analyze documents", budget=5.0)
evt["hash"]            # SHA-256 of the event payload
evt["prev_event_hash"] # None for the first event, prior hash thereafter

emitter.emit("task_progress", step=1, status="fetching")
emitter.emit("task_completed", result="success", spent_usd=2.50)

events = emitter.get_events()
assert verify_chain(events)   # False on tamper, insertion, or removal
```

`emit()` writes the JSONL line synchronously and updates the in-memory `prev_hash` for the next event. The output file is append-only; the directory is created on `__init__` if missing.

## Shard Lifecycle Events

Helper methods produce well-known event shapes for the common shard operations:

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind

shard = MemoryShard(
    atoms=[ShardAtom(text="Project context", kind=AtomKind.CONTEXT)],
    scope="project:myapp",
    origin="dev-agent",
)

emitter.shard_created(shard_id=shard.shard_id, scope=shard.scope, atom_count=len(shard.atoms))
emitter.shard_resolved(shard_id=shard.shard_id, by_agent="sub-agent-01")
emitter.shard_superseded(old_shard_id=shard.shard_id, new_shard_id="new_shard_abc...")
emitter.spawn_with_shards(
    child_agent_id="script-writer",
    shard_refs=[shard.ref.to_dict()],
    task="Write video script from project context",
)
```

The helpers are thin wrappers around `emit()` — they exist to give every well-known event a stable schema across producers, so a consumer can rely on `evt["type"] == "shard_created"` having `shard_id`, `scope`, `atom_count` no matter who emitted it.

## Entitlement and Budget Events

```python
emitter.entitlement_granted(
    token_id="tok-001",
    granted_to="script-writer",
    shard_ids=[shard.shard_id],
    scopes=["project:*"],
    capabilities=["shard:read", "web:search"],
    budget_usd=10.0,
)

emitter.shard_decrypted(shard_id=shard.shard_id, token_id="tok-001", scope="project:myapp")
emitter.capability_checked(token_id="tok-001", capability="shard:read", allowed=True)
emitter.budget_spent(
    token_id="tok-001",
    label="Claude API call",
    amount=0.15,
    total_spent=2.65,
    budget_usd=10.0,
)
```

`capability_checked` is worth emitting on *both* the allowed and denied paths — the audit trail then shows not only what an agent did, but what it tried to do and was prevented from doing.

## Studio Job Events

Track sub-agent production jobs through their full lifecycle:

```python
emitter.studio_job_packaged(
    content_shard_id="content-abc...",
    task_shard_id="task-def...",
    token_id="tok-001",
    budget_usd=5.0,
)
emitter.studio_job_started(
    token_id="tok-001",
    content_shard_id="content-abc...",
    task_shard_id="task-def...",
    prompt="Produce a 60-second explainer video",
)
emitter.studio_job_completed(
    token_id="tok-001",
    result_shard_id="result-ghi...",
    spent_usd=3.50,
    outputs=[{"type": "video", "path": "/tmp/output.mp4"}],
)
# or
emitter.studio_job_failed(
    token_id="tok-001",
    error="Provider timeout after 30s",
    spent_usd=1.20,
)
```

## Decision Extraction

Capture decisions extracted from conversations, attaching them to a shard for downstream provenance:

```python
emitter.decision_extracted(
    shard_id=shard.shard_id,
    decision_text="Use PostgreSQL for session storage",
    entity="myproject",
    rationale="Need ACID guarantees for concurrent writes",
)
```

## Chain Verification

```python
events = emitter.get_events()
assert verify_chain(events)   # True if intact, False if tampered
```

`verify_chain` checks three things, in order:

1. Each event's stored `hash` matches a freshly computed SHA-256 over its other fields.
2. Each event's `prev_event_hash` matches the previous event's `hash`.
3. The first event's `prev_event_hash` is `None`.

Any single-field edit, event insertion, event removal, or reordering breaks one of those three invariants. There's no signature on `verify_chain` itself — it's a pure function on a list of dicts, so you can verify chains produced by other agents or other runs by reading their JSONL and passing the result.

## Signed Traces

For non-repudiation — proving the chain came from a specific keypair holder — pass an Ed25519 signer to the emitter:

```python
from spiritwriter.fabric.sealed import generate_signing_keypair, sign_data

signing_key, verify_key = generate_signing_keypair()

class Signer:
    def __init__(self, key):
        self._key = key
    def sign(self, data):
        # emitter passes the event hash as bytes; signer returns a hex string
        return sign_data(data, self._key).hex()

emitter = TraceEmitter(
    run_id="run-signed",
    agent_id="audited-agent",
    out_path="/tmp/signed-trace.jsonl",
    signer=Signer(signing_key),
)

evt = emitter.emit("action", detail="signed event")
evt["sig"]   # Ed25519 signature hex
```

The emitter calls `signer.sign(hash_bytes)` and stores the result as the `sig` field. The `sig` field is excluded from the chain hash (so signing one event doesn't change what subsequent events link to). To verify, read the events, recompute each `hash`, and verify each `sig` against the corresponding `verify_key` — `verify_chain` only checks structural integrity, not signatures.

## A Typical Chain

A complete production job, end to end:

```
studio_job_packaged
  -> entitlement_granted
    -> studio_job_started
      -> capability_checked (shard:read, allowed=True)
      -> shard_decrypted (content shard)
      -> shard_decrypted (task shard)
      -> budget_spent (LLM call)
      -> budget_spent (provider call)
      -> shard_created (result shard)
    -> studio_job_completed
```

Render this as a Mermaid diagram via [visualize.render_trace](traced-workflows.md) — the trace JSONL is the source of truth for both the audit log *and* the human-readable provenance report.

## What Tracing Is Not

- **Not a database.** No query layer, no indexing, no aggregation across runs. Read the JSONL, filter in Python.
- **Not authorization.** A trace event records what happened; it doesn't gate what's allowed. Use [entitlements](encryption.md#entitlement-tokens) for that.
- **Not synchronous across writers.** One emitter per file. Multiple processes writing to the same `out_path` will produce interleaved lines that can't be chain-verified — give each producer its own file.
- **Not encrypted.** Trace events are plaintext JSONL. If event payloads contain sensitive data (decrypted shard content, raw user input), the file itself needs filesystem-level protection.
