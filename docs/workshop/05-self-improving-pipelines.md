# Lesson 5: Self-Improving Pipelines

> Once you have trace verification, you can A/B test prompts intentionally. The trace data becomes your ground truth for "did the agent actually do the job."

## Where we are

By this point in the workshop, you have:

1. **A reproducible agent environment** (Lesson 1) — permissions, PATH, working directory
2. **Tool verification** (Lesson 2) — required tools checked before work begins
3. **Explicit deliverables** (Lesson 3) — prompts that specify what MUST be produced
4. **Trace verification** (Lesson 4) — cryptographic proof of what actually happened

Each of these was a response to a real failure. Now we combine them into something more powerful: a pipeline that measures its own quality and improves over time.

## The idea

The Agent A/B divergence wasn't just a bug — it was an unintentional A/B test. Same prompt, different results. The trace chain told us exactly where they diverged and what was missing.

What if you did this on purpose?

## Intentional prompt A/B testing

### Step 1: Define what "success" means

Before you can measure improvement, you need a scoring function. For our audit pipeline:

```python
def score_run(trace_path, expected_deliverables):
    """Score a single agent run from 0.0 to 1.0."""
    trace = load_trace(trace_path)

    scores = {
        "completeness": count_present(trace, expected_deliverables) / len(expected_deliverables),
        "methodology": 1.0 if "rizin_extraction" in trace.event_types else 0.5,
        "provenance": 1.0 if all(f in trace.event_types
            for f in ["trace_saved", "witness_signed"]) else 0.0,
    }

    # Weighted average
    weights = {"completeness": 0.4, "methodology": 0.3, "provenance": 0.3}
    return sum(scores[k] * weights[k] for k in scores)
```

Using this scoring function against our original run:
- **Agent A:** completeness=1.0, methodology=1.0, provenance=0.0 → **score: 0.70**
- **Agent B:** completeness=1.0, methodology=1.0, provenance=1.0 → **score: 1.00**
- **Agent C:** completeness=1.0, methodology=1.0, provenance=1.0 → **score: 1.00**

Average: 0.90. The gap in provenance from Agent A pulls the batch score down.

### Step 2: Create prompt variants

Take your current skill file and create targeted variants:

**Variant A (baseline):** Current skill file as-is.

**Variant B (explicit deliverables):** Add the "you MUST produce exactly 3 files" language from Lesson 3.

**Variant C (tool-check preamble):** Add the Rizin verification block from Lesson 2 plus explicit deliverables.

**Variant D (checkpoint pattern):** Add intermediate checkpoints — "After each APK, verify all 3 files exist before proceeding to the next."

### Step 3: Run variants against the same batch

```bash
# Same 4 APKs, different prompt variants
for variant in A B C D; do
  claude -p "Audit: Baldwin, Calhoun, Chambers, Colbert" \
    --append-system-prompt-file "skills/audit/SKILL-variant-${variant}.md" \
    --max-turns 120 \
    --output-format stream-json \
    ... \
    > "results/variant-${variant}/output.jsonl" 2>&1 &
done
wait
```

### Step 4: Score and compare

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

### Step 5: Promote the winner

Variant D (checkpoints) produces consistently complete output. It becomes the new baseline skill file. The trace data from the comparison run becomes the evidence for why this version was chosen.

```python
emitter.emit("prompt_variant_selected", {
    "selected": "D",
    "reason": "100% completeness across all runs",
    "comparison_data": results,
    "previous_baseline": "A",
    "improvement": "mean score 0.90 → 1.00"
})
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
┌─────────────────────────────────────────┐
│  1. Define scoring function             │
│  2. Create prompt variants              │
│  3. Run variants against same batch     │
│  4. Score using trace data              │
│  5. Promote winner as new baseline      │
│  6. Record decision in trace chain      │
│  7. Repeat with new variants            │
└─────────────────────────────────────────┘
```

Each iteration through this loop:
- Produces measurable data about what works
- Records the decision and evidence in the trace chain
- Gives you a provenance trail for your pipeline's evolution

Over time, you're not just running agents — you're building evidence for which configurations produce reliable results for your specific use case.

## What this looks like at scale

For the Frio audit pipeline across 50 states:

1. **First run:** Use the best-known prompt variant. Trace everything.
2. **Validation:** Score all agent outputs. Flag gaps.
3. **Backfill:** Re-run gaps with the same or improved prompt.
4. **Analysis:** Which counties/platforms cause the most failures? Why?
5. **Iterate:** Create targeted variants for problem cases. Test. Promote.

The trace chain for the entire campaign — discovery → audit → validation → backfill → variant testing — becomes a comprehensive record of what you did, what you found, and how you verified it.

## Checklist

- [ ] Define a scoring function for your pipeline output (completeness, methodology, provenance)
- [ ] Create 2-4 prompt variants targeting known failure modes
- [ ] Run variants against the same test batch
- [ ] Score results using trace data
- [ ] Promote the winning variant as your new baseline
- [ ] Record the comparison and decision in the trace chain
- [ ] Schedule periodic re-evaluation as your pipeline evolves

## Summary: the five-lesson arc

| Lesson | Failure | Fix | Takeaway |
|--------|---------|-----|----------|
| 1. Environment | Permissions blocked, tools missing | Explicit PATH, permission mode | Verify the agent's world before dispatching |
| 2. Silent degradation | Rizin missing, agent used regex | Tool-check preamble, fail loudly | Don't let agents downgrade methodology |
| 3. Non-determinism | Agent A skipped provenance | Explicit deliverables, generous turns | Same prompt ≠ same output |
| 4. Trace verification | Manual spot-checking doesn't scale | Plan → validate → gap-detect | Make verification part of the provenance |
| 5. Self-improvement | Static prompts, no measurement | A/B test with trace scoring | Measure, compare, promote, repeat |

Each lesson builds on the previous failure. Together, they turn a fragile "run agent and hope" workflow into a pipeline you can trust — and prove you can trust.

---

Previous: [Lesson 4: Trace as a Verification Layer](04-trace-as-verification-layer.md)
Back to: [Workshop Overview](README.md)
