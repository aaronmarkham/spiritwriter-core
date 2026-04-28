# Lesson 3: Prompt Ambiguity and Non-Deterministic Output

> Identical prompts do not guarantee identical behavior.

## What you'll learn

In this lesson you'll learn why parallel agents given the same prompt can produce different outputs, and how to write prompts that specify **requirements** rather than **capabilities**. You'll also set appropriate turn budgets and add post-completion verification.

## Prerequisites

- Completed [Lesson 1](01-environment-and-permissions.md) — headless agent invocation
- Completed [Lesson 2](02-tool-availability-and-silent-degradation.md) — tool verification

## Concepts

### Non-determinism in LLM agents

LLMs are non-deterministic by nature. Even with identical inputs, the model may:

- Read a skill file in a different order
- Prioritize different sections of a long prompt
- Budget its turns differently based on early decisions
- Hit the turn limit and triage away tasks it considers optional

This means running three parallel agents with the same prompt and skill file can produce three different sets of outputs. One agent might produce all expected deliverables; another might skip provenance files because it ran out of turns.

### Capabilities vs. requirements

The difference between a prompt that says "the audit module supports provenance tracing" and one that says "you MUST generate trace files" is the difference between optional and mandatory. Agents interpret "supports" and "can generate" as capabilities they may use. They interpret "MUST produce" as requirements they can't skip.

## Step 1: Specify explicit deliverables

List every required output file by name. Don't describe capabilities — specify requirements.

**Ambiguous (avoid):**
```
Audit each APK. Generate a report with findings, permissions, and secrets.
The audit module supports provenance tracing via spiritwriter.
```

**Explicit (preferred):**
```
For EACH APK, you MUST produce exactly 3 files:

1. {county}/report.json — audit findings, permissions, hardcoded secrets
2. {county}/trace.jsonl — spiritwriter trace chain (SHA-256 linked events)
3. {county}/witness.json — self-hashing witness document

All 3 files are REQUIRED. If any file is missing for any county, the audit is incomplete.
Do not move to the next APK until all 3 files exist for the current one.
```

The last line is important — it forces sequential verification rather than letting the agent batch work and skip deliverables for earlier items.

## Step 2: Budget turns generously

When dispatching parallel agents, each gets a `--max-turns` budget. If the budget is too tight, some agents will run out of room and silently drop lower-priority work.

A rule of thumb:

```
expected_turns = (number_of_items × turns_per_item)
max_turns = expected_turns × 2, minimum 10 turn buffer
```

For a batch of 4 APKs at ~15 turns each:
- Expected: 60 turns
- Budget: `--max-turns 120`

Setting `--max-turns 60` gives zero buffer. An agent that takes a few extra turns on early items has no room left for later deliverables.

## Step 3: Dispatch parallel agents

With explicit deliverables and generous turn budgets, you can dispatch parallel agents:

```bash
# Agent A — counties 1-4
claude -p "Audit these APKs: Baldwin, Calhoun, Chambers, Colbert" \
  --append-system-prompt-file .claude/skills/audit/SKILL.md \
  --max-turns 120 \
  --permission-mode bypassPermissions \
  --output-format stream-json \
  --settings '{"env":{"PATH":"/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin"}}' &

# Agent B — counties 5-8
claude -p "Audit these APKs: Covington, Limestone, Marshall, Montgomery" \
  --append-system-prompt-file .claude/skills/audit/SKILL.md \
  --max-turns 120 \
  --permission-mode bypassPermissions \
  --output-format stream-json \
  --settings '{"env":{"PATH":"/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin"}}' &

wait
```

Even with all the right settings, you can't guarantee identical behavior across agents. That's expected — the next step compensates for it.

## Step 4: Verify outputs after completion

Add a post-completion check that treats missing files as failures, not caveats.

```bash
#!/bin/bash
# verify-outputs.sh

COUNTIES=("Baldwin" "Calhoun" "Chambers" "Colbert")
REQUIRED_FILES=("report.json" "trace.jsonl" "witness.json")
MISSING=0

for county in "${COUNTIES[@]}"; do
  for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "docs/audits/ocv/AL/${county}/${file}" ]; then
      echo "MISSING: ${county}/${file}"
      MISSING=$((MISSING + 1))
    fi
  done
done

if [ $MISSING -gt 0 ]; then
  echo "ERROR: ${MISSING} files missing. Backfill required."
  exit 1
fi
echo "All outputs verified."
```

For a more robust verification approach using spiritwriter's trace chain, see [Lesson 4](04-trace-as-verification-layer.md).

## The deeper issue

Natural language prompts are not specifications. They leave room for interpretation, and different runs will interpret differently. This is analogous to property-based testing vs. example-based testing — a single prompt that works once doesn't prove the system works reliably.

You need either:

1. **Stronger specifications** — explicit deliverables, checklists, tool-check preambles
2. **Output verification** — check what was actually produced, not what was asked for
3. **Both** (recommended)

For security audit pipelines specifically, the consequences of non-deterministic output are:
- Missing provenance means you can't prove what you found
- Missing findings mean you're giving false assurance
- Inconsistent methodology across runs means results aren't comparable

## Checklist

- [ ] List every required output file by name in your prompt/skill
- [ ] Use "MUST produce" language, not "can generate" or "supports"
- [ ] Set `--max-turns` to 2x your expected turn count
- [ ] Add a post-completion verification step (script or automated check)
- [ ] If dispatching N parallel agents, verify all N produced complete output
- [ ] Treat partial output as failure, not success with caveats

---

Previous: [Lesson 2: Tool Availability & Silent Degradation](02-tool-availability-and-silent-degradation.md)
Next: [Lesson 4: Trace as a Verification Layer](04-trace-as-verification-layer.md)
