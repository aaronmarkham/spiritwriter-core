# Skill: Spiritwriter Trace

Hash-chained event logging for agent provenance and audit trails.

## When to Use

- You need to **log agent actions** with tamper-evident chain integrity
- You need to **track shard access**, encryption, entitlement grants
- You need to **visualize workflows** from trace event logs
- You need **provenance** — who did what, when, linked to what

## Install

```bash
pip install -e /path/to/spiritwriter-core
```

## Concepts

| Concept | What it is |
|---------|-----------|
| **TraceEvent** | A single logged action: event_id (UUID), event_type, timestamp, agent_id, payload, prev_hash |
| **TraceEmitter** | Append-only writer that maintains hash chain. Each event's prev_hash = hash of previous event. |
| **Hash chain** | SHA-256 chain linking events. Tampering with any event breaks the chain. |

## Python API

### Emit trace events

```python
from spiritwriter.trace.emitter import TraceEmitter

emitter = TraceEmitter(agent_id="lilit", output_path="trace.jsonl")

# Log any event type with arbitrary payload
emitter.emit("task_started", {"task": "build feature X"})
emitter.emit("shard_read", {"shard_id": "abc123", "scope": "project:csp"})
emitter.emit("task_completed", {"result": "success"})
```

### Built-in event types

The emitter has convenience methods for common events:

```python
emitter.emit_entitlement_granted(token)
emitter.emit_shard_decrypted(shard_id, scope)
emitter.emit_capability_checked(token_id, capability, allowed=True)
emitter.emit_budget_spent(token_id, amount, remaining)
emitter.emit_studio_job_packaged(job_id, shard_ids)
emitter.emit_studio_job_started(job_id)
emitter.emit_studio_job_completed(job_id, result_shard_id)
emitter.emit_studio_job_failed(job_id, error)
```

### Verify chain integrity

```python
from spiritwriter.trace.emitter import verify_chain

events = emitter.get_events()
is_valid = verify_chain(events)  # True if no tampering
```

### Visualize traces

```python
from spiritwriter.trace.visualize import render_trace

# Generates Mermaid diagram from JSONL trace
mermaid_code = render_trace(
    "trace.jsonl",
    diagram_type="workflow"  # or "genealogy" or "multi_agent"
)
```

## Trace Event Schema

```json
{
  "event_id": "uuid",
  "event_type": "string",
  "timestamp": "ISO-8601",
  "agent_id": "string",
  "payload": { },
  "prev_hash": "sha256-of-previous-event"
}
```

Events are stored as newline-delimited JSON (JSONL). Each line is one event.

## Source Files

- `spiritwriter/trace/emitter.py` — TraceEmitter, verify_chain
- `spiritwriter/trace/visualize.py` — Mermaid diagram generation from traces
