# Tracing & Provenance

The **TraceEmitter** produces hash-chained JSONL event logs — a tamper-evident audit trail covering agent actions, shard lifecycle, and entitlement usage.

## Design

Each event is:
1. **Timestamped** — ISO 8601 UTC
2. **Hash-chained** — SHA-256 of event payload, linked to previous event via `prev_event_hash`
3. **Optionally signed** — Ed25519 signature for non-repudiation
4. **Append-only** — JSONL file, one event per line

Tampering with any event breaks the chain — `verify_chain()` detects it.

## Quick Start

```python
from spiritwriter.fabric.emitter import TraceEmitter, verify_chain

emitter = TraceEmitter(
    run_id="run-2026-04-17-001",
    agent_id="orchestrator",
    out_path="/tmp/traces/run-001.jsonl",
)

# Emit a custom event
evt = emitter.emit("task_started", task="analyze documents", budget=5.0)
print(evt["hash"])           # SHA-256 of event payload
print(evt["prev_event_hash"])  # None (first event) or previous hash

# Emit more events — chain builds automatically
emitter.emit("task_progress", step=1, status="fetching")
emitter.emit("task_completed", result="success", spent_usd=2.50)

# Verify the full chain
events = emitter.get_events()
assert verify_chain(events)  # True if no tampering
```

## Shard Lifecycle Events

```python
from spiritwriter.fabric.shard import MemoryShard, ShardAtom, AtomKind

shard = MemoryShard(
    atoms=[ShardAtom(text="Project context", kind=AtomKind.CONTEXT)],
    scope="project:myapp",
    origin="dev-agent",
)

# Record shard creation
emitter.shard_created(
    shard_id=shard.shard_id,
    scope=shard.scope,
    atom_count=len(shard.atoms),
)

# Record shard resolution (hydration)
emitter.shard_resolved(
    shard_id=shard.shard_id,
    by_agent="sub-agent-01",
)

# Record shard supersession (new version)
emitter.shard_superseded(
    old_shard_id=shard.shard_id,
    new_shard_id="new_shard_abc123...",
)

# Record spawning a sub-agent with shard references
emitter.spawn_with_shards(
    child_agent_id="script-writer",
    shard_refs=[shard.ref.to_dict()],
    task="Write video script from project context",
)
```

## Entitlement & Budget Events

```python
# Record entitlement creation
emitter.entitlement_granted(
    token_id="tok-001",
    granted_to="script-writer",
    shard_ids=[shard.shard_id],
    scopes=["project:*"],
    capabilities=["shard:read", "web:search"],
    budget_usd=10.0,
)

# Record shard decryption via entitlement
emitter.shard_decrypted(
    shard_id=shard.shard_id,
    token_id="tok-001",
    scope="project:myapp",
)

# Record capability check
emitter.capability_checked(
    token_id="tok-001",
    capability="shard:read",
    allowed=True,
)

# Record budget expenditure
emitter.budget_spent(
    token_id="tok-001",
    label="Claude API call",
    amount=0.15,
    total_spent=2.65,
    budget_usd=10.0,
)
```

## Studio Job Events

Track sub-agent production jobs through the full lifecycle:

```python
# Job packaged (ready to spawn)
emitter.studio_job_packaged(
    content_shard_id="content-abc...",
    task_shard_id="task-def...",
    token_id="tok-001",
    budget_usd=5.0,
)

# Job started
emitter.studio_job_started(
    token_id="tok-001",
    content_shard_id="content-abc...",
    task_shard_id="task-def...",
    prompt="Produce a 60-second explainer video",
)

# Job completed
emitter.studio_job_completed(
    token_id="tok-001",
    result_shard_id="result-ghi...",
    spent_usd=3.50,
    outputs=[{"type": "video", "path": "/tmp/output.mp4"}],
)

# Or job failed
emitter.studio_job_failed(
    token_id="tok-001",
    error="Provider timeout after 30s",
    spent_usd=1.20,
)
```

## Decision Extraction

Capture decisions extracted from conversations for provenance:

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

# Verify hash chain integrity
is_valid = verify_chain(events)
print(f"Chain valid: {is_valid}")

# What verify_chain checks:
# 1. Each event's hash matches its recomputed SHA-256
# 2. Each event's prev_event_hash matches the previous event's hash
# 3. First event has prev_event_hash = None
```

If an event is modified, inserted, or removed, `verify_chain()` returns `False`.

## Signed Traces

For non-repudiation, pass an Ed25519 signer:

```python
from spiritwriter.fabric.sealed import generate_signing_keypair

signing_key, verify_key = generate_signing_keypair()

# Simple signer wrapper
class Signer:
    def __init__(self, key):
        self._key = key
    def sign(self, data):
        from spiritwriter.fabric.sealed import sign_data
        return sign_data(data if isinstance(data, bytes) else data.encode(), self._key).hex()

emitter = TraceEmitter(
    run_id="run-signed",
    agent_id="audited-agent",
    out_path="/tmp/signed-trace.jsonl",
    signer=Signer(signing_key),
)

# Events now include a "sig" field
evt = emitter.emit("action", detail="signed event")
print(evt["sig"])  # Ed25519 signature hex
```

## Event Format

Each line in the JSONL file:

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

## Typical Trace Chain

A complete production job trace, end to end:

```
studio_job_packaged
  → entitlement_granted
    → studio_job_started
      → capability_checked (shard:read)
      → shard_decrypted (content shard)
      → shard_decrypted (task shard)
      → budget_spent (LLM call)
      → budget_spent (provider call)
      → shard_created (result shard)
    → studio_job_completed
```
