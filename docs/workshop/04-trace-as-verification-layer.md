# Lesson 4: Trace as a Verification Layer

> Add provenance tracing to the pipeline itself — not after the fact, but as part of the work.

## What you'll learn

In this lesson you'll integrate spiritwriter trace events into your agent pipeline so that verification happens automatically. You'll move from shell-script output checking (Lesson 3) to cryptographic proof chains that record what was planned, what happened, and what was verified.

## Prerequisites

- Completed [Lessons 1-3](README.md) — environment, tool verification, explicit deliverables
- `pip install spiritwriter` (or your project's equivalent)
- Familiarity with SHA-256 hash chaining concepts

## Concepts

### What spiritwriter trace provides

Spiritwriter is a cryptographic provenance framework. Each step of a pipeline emits **trace events** — structured records linked by SHA-256 hashes into a chain. The chain is tamper-evident: modifying any event invalidates all subsequent hashes.

A trace event looks like this:

```json
{
  "event_id": "evt_a1b2c3",
  "event_type": "apk_audit_complete",
  "timestamp": "2026-04-27T14:32:01Z",
  "parent_hash": "sha256:9f3e...",
  "payload": {
    "county": "Baldwin County AL",
    "package": "com.ocv.baldwincountyal",
    "findings_count": 11,
    "risk_rating": "HIGH",
    "report_path": "docs/audits/ocv/AL/Baldwin County AL/report.json"
  },
  "hash": "sha256:4a7d..."
}
```

The chain links: APK binary hash -> extraction events -> individual findings -> final report -> witness document. Each step references the previous step's hash. You can verify the entire chain from source artifact to conclusion.

### Why trace beats shell scripts

The `verify-outputs.sh` script from Lesson 3 checks that files exist. Spiritwriter trace gives you:

1. **Tamper evidence.** The hash chain proves files weren't modified after generation.
2. **Methodology verification.** The trace records which tools were used. If an agent fell back to regex instead of Rizin (see [Lesson 2](02-tool-availability-and-silent-degradation.md)), the trace shows `regex_extraction` instead of `rizin_extraction`.
3. **Audit trail for the audit.** When you publish security findings, the trace chain shows every step, every input hash, every tool invocation.
4. **Composability.** Individual agent traces link into batch traces. Batch traces link into campaign traces. You can zoom from "20 apps rated HIGH risk" down to "this specific DEX string in this specific APK."

## Step 1: Add trace emission to your agent

Start by having your agent emit trace events during its work. This is the minimum viable integration:

```python
from spiritwriter.fabric import TraceEmitter

emitter = TraceEmitter("my-audit")
emitter.emit("audit_started", {"target": "some-app.apk"})
# ... do work ...
emitter.emit("audit_complete", {"findings": 11, "risk": "HIGH"})
emitter.save("trace.jsonl")
```

Each agent's audit skill emits events during the audit:

```
apk_hash_verified -> permissions_extracted -> dex_strings_extracted ->
findings_classified -> report_generated -> witness_signed
```

If an agent's trace chain stops at `report_generated` and never emits `witness_signed`, you know provenance wasn't completed — even if the report file exists.

## Step 2: Emit a plan event before dispatching agents

Before launching parallel agents, emit a trace event that declares what you expect each agent to produce:

```python
from spiritwriter.fabric import TraceEmitter

emitter = TraceEmitter("audit-dispatch")

plan = emitter.emit("agent_dispatch_plan", {
    "batch_id": "AL-2026-04-27",
    "agents": [
        {
            "agent_id": "agent-A",
            "counties": ["Baldwin", "Calhoun", "Chambers", "Colbert"],
            "expected_deliverables": [
                "{county}/report.json",
                "{county}/trace.jsonl",
                "{county}/witness.json"
            ]
        },
        {
            "agent_id": "agent-B",
            "counties": ["Covington", "Limestone", "Marshall", "Montgomery"],
            "expected_deliverables": ["...same..."]
        },
        {
            "agent_id": "agent-C",
            "counties": ["Russell", "Shelby", "Talladega", "Tallapoosa"],
            "expected_deliverables": ["...same..."]
        }
    ]
})
```

This plan event is now part of the trace chain. It records what should exist when the agents finish.

## Step 3: Validate outputs against the plan

After all agents finish, a validator reads the plan event and checks actual outputs:

```python
from spiritwriter.fabric import TraceEmitter
from pathlib import Path

emitter = TraceEmitter("audit-validator")

plan = load_plan_event("AL-2026-04-27")  # read from trace

gaps = []
for agent in plan["agents"]:
    for county in agent["counties"]:
        for template in agent["expected_deliverables"]:
            path = Path(template.format(county=county))
            if not path.exists():
                gaps.append({
                    "agent_id": agent["agent_id"],
                    "county": county,
                    "missing": str(path)
                })

# Emit validation result as a trace event
emitter.emit("batch_validation", {
    "batch_id": plan["batch_id"],
    "status": "complete" if not gaps else "incomplete",
    "gaps": gaps,
    "total_expected": sum(
        len(a["counties"]) * len(a["expected_deliverables"])
        for a in plan["agents"]
    ),
    "total_present": sum(
        len(a["counties"]) * len(a["expected_deliverables"])
        for a in plan["agents"]
    ) - len(gaps)
})
```

The validation result is itself a trace event, so it's part of the cryptographic record.

## Step 4: Read the full trace chain

The resulting trace chain for a batch audit looks like:

```
agent_dispatch_plan (12 counties, 3 agents, 36 expected files)
  |-- agent-A: apk_hash -> ... -> report_generated (x4)
  |   Warning: Missing witness_signed for all 4 counties
  |-- agent-B: apk_hash -> ... -> witness_signed (x4, complete)
  |-- agent-C: apk_hash -> ... -> witness_signed (x4, complete)
  \-- batch_validation: 28/36 files present, 8 gaps identified
        \-- backfill_completed: 8 missing files regenerated
```

This is both a **verification mechanism** (catches gaps automatically) and a **provenance record** (proves the batch was validated and gaps were filled).

## Getting started incrementally

You don't need to implement the full plan -> validate -> gap-detect flow all at once. Build up in levels:

### Level 1: Add trace to your agent output

Have your agent emit trace events during its work. This costs almost nothing and gives you a log of what actually happened (vs. what the agent said it did).

### Level 2: Add post-batch validation

After agents complete, run a validator that checks expected outputs exist. Emit the validation result as a trace event so it's part of the record.

### Level 3: Add plan events

Before dispatching agents, emit a plan event. Now the trace chain covers the full lifecycle: what you intended -> what happened -> what was verified.

## Checklist

- [ ] Add spiritwriter trace emission to your agent's skill/workflow
- [ ] After batch runs, validate outputs against expectations
- [ ] Emit validation results as trace events (not just stdout)
- [ ] For parallel agent dispatches, emit a plan event before launching
- [ ] Check trace chains for methodology events (was the right tool used?)
- [ ] Store trace files alongside output artifacts

---

Previous: [Lesson 3: Prompt Ambiguity and Non-Deterministic Output](03-prompt-ambiguity-and-nondeterministic-output.md)
Next: [Lesson 5: Self-Improving Pipelines](05-self-improving-pipelines.md)
