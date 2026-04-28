# Lesson 4: Trace as a Verification Layer

> Add provenance tracing to the pipeline itself — not after the fact, but as part of the work.

## What happened

After the Agent A/B divergence in [Lesson 3](03-prompt-ambiguity-and-nondeterministic-output.md), the parent session (CC) had to manually backfill the missing provenance. This worked because someone was watching — one human checking 3 agents across 12 counties.

Now scale it: 50 states × N counties per state × 3 files per county. Manual spot-checking breaks down fast. You need automated verification, and that verification needs to be part of the provenance record itself.

This is what spiritwriter does.

## What spiritwriter trace provides

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

The chain links: APK binary hash → extraction events → individual findings → final report → witness document. Each step references the previous step's hash. You can verify the entire chain from source artifact to conclusion.

## Using trace for agent verification

The insight from the Agent A/B incident: trace isn't just for auditing external software. It's for auditing your own pipeline.

### Step 1: Emit a plan event before dispatching agents

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

### Step 2: Agents emit their own trace events as they work

Each agent's audit skill already uses spiritwriter to emit events during the audit:

```
apk_hash_verified → permissions_extracted → dex_strings_extracted →
findings_classified → report_generated → witness_signed
```

Agent A's trace chain stops at `report_generated` — it never emitted `witness_signed`. Agent B's chain is complete.

### Step 3: Validate outputs against the plan

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

### Step 4: The full trace chain tells the story

The resulting trace chain for a batch audit looks like:

```
agent_dispatch_plan (12 counties, 3 agents, 36 expected files)
  ├── agent-A: apk_hash → ... → report_generated (×4)
  │   ⚠ Missing: witness_signed for all 4 counties
  ├── agent-B: apk_hash → ... → witness_signed (×4, complete)
  ├── agent-C: apk_hash → ... → witness_signed (×4, complete)
  └── batch_validation: 28/36 files present, 8 gaps identified
        └── backfill_completed: 8 missing files regenerated
```

This is both a **verification mechanism** (catches Agent A's gaps automatically) and a **provenance record** (proves the batch was validated and gaps were filled).

## What this gives you that shell scripts don't

The simple `verify-outputs.sh` from Lesson 3 checks that files exist. Spiritwriter trace gives you:

1. **Tamper evidence.** The hash chain means you can prove files weren't modified after generation. A shell script can tell you files exist; trace can tell you they're the same files the agent produced.

2. **Methodology verification.** The trace chain records which tools were used. If Agent A fell back to regex instead of Rizin (see [Lesson 2](02-tool-availability-and-silent-degradation.md)), the trace shows `regex_extraction` instead of `rizin_extraction` — even if the final report looks the same.

3. **Audit trail for the audit.** When you publish security findings, people ask "how did you arrive at this?" The trace chain is the answer: every step, every input hash, every tool invocation, cryptographically linked.

4. **Composability.** Individual agent traces link into the batch trace. Batch traces link into campaign traces (all 50 states). You can zoom from "20 apps rated HIGH risk" down to "this specific DEX string in this specific APK."

## Getting started

You don't need to implement the full plan → validate → gap-detect flow on day one. Start small:

### Level 1: Add trace to your agent output

Have your agent emit trace events during its work. This costs almost nothing and gives you a log of what actually happened (vs. what the agent said it did).

```python
from spiritwriter.fabric import TraceEmitter

emitter = TraceEmitter("my-audit")
emitter.emit("audit_started", {"target": "some-app.apk"})
# ... do work ...
emitter.emit("audit_complete", {"findings": 11, "risk": "HIGH"})
emitter.save("trace.jsonl")
```

### Level 2: Add post-batch validation

After agents complete, run a validator that checks expected outputs exist. Emit the validation result as a trace event so it's part of the record.

### Level 3: Add plan events

Before dispatching agents, emit a plan event. Now the trace chain covers the full lifecycle: what you intended → what happened → what was verified.

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
