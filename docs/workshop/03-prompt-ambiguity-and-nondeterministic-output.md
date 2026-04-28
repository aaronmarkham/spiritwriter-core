# Lesson 3: Prompt Ambiguity and Non-Deterministic Output

> Identical prompts do not guarantee identical behavior.

## What happened

We needed to audit 12 Alabama county sheriff apps. Rather than run them sequentially (slow), we split them into 3 batches of 4 and launched 3 parallel `claude -p` agents:

```bash
# Agent A — counties 1-4
claude -p "Audit these APKs: Baldwin, Calhoun, Chambers, Colbert" \
  --append-system-prompt-file .claude/skills/audit/SKILL.md \
  --max-turns 60 ...

# Agent B — counties 5-8
claude -p "Audit these APKs: Covington, Limestone, Marshall, Montgomery" \
  --append-system-prompt-file .claude/skills/audit/SKILL.md \
  --max-turns 60 ...

# Agent C — counties 9-12
claude -p "Audit these APKs: Russell, Shelby, Talladega, Tallapoosa" \
  --append-system-prompt-file .claude/skills/audit/SKILL.md \
  --max-turns 60 ...
```

Same CLI invocation. Same prompt template. Same skill file. Same model.

**Agent B and C** produced the expected output for all their counties: `report.json`, `trace.jsonl`, `witness.json`.

**Agent A** produced `report.json` for all 4 counties but skipped provenance entirely — no `trace.jsonl`, no `witness.json`. The audit reports were there, but the cryptographic proof chain wasn't.

Neither agent was "wrong." The skill file described provenance generation but didn't list it as a required deliverable. Agent A processed its 4 APKs, spent 58 of its 60 turns on the audit work, and had no room left for provenance. Agent B happened to read deeper into the skill file and budgeted differently.

We only discovered the gap because the parent session (CC) manually checked the output directories for each agent.

## Why this happens

LLMs are non-deterministic by nature. Even with identical inputs, the model may:

- Read the skill file in a different order
- Prioritize different sections of a long prompt
- Budget its turns differently based on early decisions
- Hit the turn limit and triage away tasks it considers optional

Two contributing factors made this worse:

1. **Turn budget was too tight.** 60 turns for 4 APKs with provenance was marginal. Agent A used 58 turns and ran out of room. Agent B happened to be more efficient.

2. **The prompt was ambiguous about what was required.** The skill file described provenance as a capability, not a requirement. The difference between "you can generate trace files" and "you MUST generate trace files" is the difference between optional and mandatory.

## Fix 1: Explicit deliverables

List every required output file by name. Don't describe capabilities — specify requirements.

**Before (ambiguous):**
```
Audit each APK. Generate a report with findings, permissions, and secrets.
The audit module supports provenance tracing via spiritwriter.
```

**After (explicit):**
```
For EACH APK, you MUST produce exactly 3 files:

1. {county}/report.json — audit findings, permissions, hardcoded secrets
2. {county}/trace.jsonl — spiritwriter trace chain (SHA-256 linked events)
3. {county}/witness.json — self-hashing witness document

All 3 files are REQUIRED. If any file is missing for any county, the audit is incomplete.
Do not move to the next APK until all 3 files exist for the current one.
```

## Fix 2: Budget turns generously

The rule of thumb we arrived at:

```
expected_turns = (number_of_items × turns_per_item)
max_turns = expected_turns × 2, minimum 10 turn buffer
```

For our audit batches:
- 4 APKs × 15 turns each = 60 expected
- Budget: `--max-turns 120`

The original 60-turn limit was exactly the expected amount with zero buffer. Agent A needed more room and had none.

## Fix 3: Verify after completion

Even with explicit prompts and generous budgets, you can't guarantee identical behavior. You need a post-completion check.

**Simple version — shell script:**

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

**Better version — spiritwriter trace verification:**

See [Lesson 4](04-trace-as-verification-layer.md) for how to make this verification part of the provenance chain itself.

## The deeper issue

This incident reveals something fundamental about using AI agents for structured work: **the same prompt is not a specification.** Natural language instructions leave room for interpretation, and different runs will interpret differently.

This is analogous to property-based testing vs. example-based testing. A single test case (prompt) that passes once doesn't prove the system works — it proves it worked that time. You need either:

1. **Stronger specifications** (explicit deliverables, checklists, tool-check preambles)
2. **Output verification** (check what was actually produced, not what was asked for)
3. **Both** (recommended)

For security audit pipelines specifically, the consequences of non-deterministic output are:
- Missing provenance means you can't prove what you found
- Missing findings mean you're giving false assurance
- Inconsistent methodology across runs means results aren't comparable

## Checklist

- [ ] Explicitly list every required output file by name in your prompt/skill
- [ ] Use "MUST produce" language, not "can generate" or "supports"
- [ ] Set `--max-turns` to 2x your expected turn count
- [ ] Add a post-completion verification step (script or automated check)
- [ ] If dispatching N parallel agents, verify all N produced complete output
- [ ] Treat partial output as failure, not success with caveats

---

Previous: [Lesson 2: Tool Availability & Silent Degradation](02-tool-availability-and-silent-degradation.md)
Next: [Lesson 4: Trace as a Verification Layer](04-trace-as-verification-layer.md)
