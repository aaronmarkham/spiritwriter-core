# Lesson 5: Self-Improving Pipelines

> Once you have trace verification, you can A/B test prompts intentionally. The trace data becomes your ground truth for "did the agent actually do the job."

## What you'll learn

After this lesson you'll combine everything from Lessons 1-4 into a pipeline that measures its own quality and improves over time. You'll define a scoring function, create prompt variants, run them against the same batch, and use trace data to pick a winner.

## Prerequisites

- Completed [Lessons 1-4](README.md) — environment, tool verification, explicit deliverables, trace verification
- A working agent pipeline with trace emission
- At least one batch of real or test data to run variants against

## Where you are

By this point in the workshop:

1. **A reproducible agent environment** (Lesson 1) — permissions, PATH, working directory
2. **Tool verification** (Lesson 2) — required tools checked before work begins
3. **Explicit deliverables** (Lesson 3) — prompts that specify what MUST be produced
4. **Trace verification** (Lesson 4) — cryptographic proof of what actually happened

Now combine them into a pipeline that measures its own quality.

## Step 1: Define a scoring function

Before you can measure improvement, define what "success" means. For an audit pipeline:

```python
def score_run(trace_path, expected_deliverables):
    """Score a single agent run from 0.0 to 1.0."""
    trace = load_trace(trace_path)
    event_types = {e["type"] for e in trace}

    scores = {
        # Completeness weighted highest: partial output is worse than slow output
        "completeness": count_present(trace, expected_deliverables) / len(expected_deliverables),
        # Did the agent use binary analysis or fall back to regex?
        "methodology": 1.0 if "audit_strings_extracted" in event_types else 0.5,
        # Was the full trace chain emitted through to the report?
        "provenance": 1.0 if "audit_report_generated" in event_types else 0.0,
    }

    # Weighted: completeness matters most, methodology and provenance equally
    weights = {"completeness": 0.4, "methodology": 0.3, "provenance": 0.3}
    return sum(scores[k] * weights[k] for k in scores)
```

This scoring function uses trace data — not the agent's self-reported output — as ground truth. The event types come from spiritwriter's actual audit trace chain (see [Lesson 4](04-trace-as-verification-layer.md)).

## Step 2: Create prompt variants

Take your current skill file and create targeted variants, each addressing a known failure mode:

| Variant | Change | Targets |
|---------|--------|---------|
| **A** (baseline) | Current skill file as-is | Control |
| **B** (explicit deliverables) | Add "you MUST produce exactly 3 files" language (Lesson 3) | Completeness |
| **C** (tool-check preamble) | Add Rizin verification block (Lesson 2) + explicit deliverables | Methodology + completeness |
| **D** (checkpoint pattern) | Add "after each APK, verify all 3 files exist before proceeding" | Sequential verification |

## Step 3: Run variants against the same batch

```bash
# Same 4 APKs, different prompt variants
for variant in A B C D; do
  claude -p "Audit: Baldwin, Calhoun, Chambers, Colbert" \
    --append-system-prompt-file "skills/audit/SKILL-variant-${variant}.md" \
    --max-turns 120 \
    --permission-mode bypassPermissions \
    --output-format stream-json \
    --settings '{"env":{"PATH":"/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin"}}' \
    > "results/variant-${variant}/output.jsonl" 2>&1 &
done
wait
```

Same batch for all variants ensures differences in output reflect prompt quality, not data variance.

## Step 4: Score and compare

```python
variants = ["A", "B", "C", "D"]
counties = ["Baldwin", "Calhoun", "Chambers", "Colbert"]
expected = ["report.json", "trace.jsonl", "witness.json"]

results = {}
for v in variants:
    scores = []
    for county in counties:
        trace = f"results/variant-{v}/{county}/trace.jsonl"
        scores.append(score_run(trace, expected))
    results[v] = {
        "mean": sum(scores) / len(scores),
        "min": min(scores),
        "all_complete": all(s == 1.0 for s in scores),
    }

# Print comparison table
for v, r in sorted(results.items(), key=lambda x: -x[1]["mean"]):
    status = "PASS" if r["all_complete"] else "GAPS"
    print(f"Variant {v}: mean={r['mean']:.2f}  min={r['min']:.2f}  [{status}]")
```

Example output:

```
Variant D: mean=1.00  min=1.00  [PASS]
Variant C: mean=1.00  min=1.00  [PASS]
Variant B: mean=0.95  min=0.85  [GAPS]
Variant A: mean=0.90  min=0.70  [GAPS]
```

## Step 5: Promote the winner and record the decision

The winning variant becomes the new baseline skill file. Record the decision in the trace chain — provenance for why this version was chosen:

```python
emitter.emit(
    "prompt_variant_selected",
    selected="D",
    reason="100% completeness across all runs",
    comparison_data=results,
    previous_baseline="A",
    improvement="mean score 0.90 -> 1.00",
)
```

## Beyond prompts: what else you can A/B test

The same framework works for any pipeline parameter:

| Parameter | Variant examples | Scoring criteria |
|-----------|-----------------|------------------|
| Prompt/skill content | Explicit vs. implicit deliverables | Completeness rate |
| Turn budget | 60 vs. 120 vs. 200 | Completeness vs. cost |
| Batch size | 2 apps vs. 4 vs. 8 per agent | Quality vs. throughput |
| Model | Different Claude models | Score vs. cost vs. speed |
| Tool configuration | Rizin vs. regex-only | Finding count, methodology score |

## The feedback loop

```
1. Define scoring function
2. Create prompt variants
3. Run variants against same batch
4. Score using trace data
5. Promote winner as new baseline
6. Record decision in trace chain
7. Repeat with new variants
```

Each iteration:
- Produces measurable data about what works
- Records the decision and evidence in the trace chain
- Gives you a provenance trail for your pipeline's evolution

Over time, you're not running agents and hoping — you're building evidence for which configurations produce reliable results for your specific use case.

## Summary: the five-lesson arc

| Lesson | Problem | Solution | Takeaway |
|--------|---------|----------|----------|
| 1. Environment | Permissions blocked, tools missing | Explicit PATH, permission mode | Verify the agent's world before dispatching |
| 2. Silent degradation | Required tool missing, agent fell back silently | Tool-check preamble, fail loudly | Don't let agents downgrade methodology |
| 3. Non-determinism | Same prompt, different outputs | Explicit deliverables, generous turns | Same prompt != same output |
| 4. Trace verification | Manual checking doesn't scale | Plan -> validate -> gap-detect | Make verification part of the provenance |
| 5. Self-improvement | Static prompts, no measurement | A/B test with trace scoring | Measure, compare, promote, repeat |

Each lesson builds on the previous one. Together, they turn a "run agent and hope" workflow into a pipeline you can measure and improve systematically.

## Checklist

- [ ] Define a scoring function for your pipeline output (completeness, methodology, provenance)
- [ ] Create 2-4 prompt variants targeting known failure modes
- [ ] Run variants against the same test batch
- [ ] Score results using trace data
- [ ] Promote the winning variant as your new baseline
- [ ] Record the comparison and decision in the trace chain
- [ ] Schedule periodic re-evaluation as your pipeline evolves

---

Previous: [Lesson 4: Trace as a Verification Layer](04-trace-as-verification-layer.md)
Back to: [Workshop Overview](README.md)
