# Lesson 2: Tool Availability & Silent Degradation

> Agents adapt — which is usually good, but in security analysis means they'll silently downgrade their methodology without telling you.

## What you'll learn

In this lesson you'll learn how to prevent agents from silently falling back to weaker analysis methods when a required tool is unavailable. You'll classify your tools into tiers and add verification steps that force the agent to fail loudly rather than produce incomplete results.

## Prerequisites

- Completed [Lesson 1](01-environment-and-permissions.md) — environment and PATH configured
- Understanding of which tools your pipeline depends on

## Concepts

### Why agents fall back silently

LLMs are trained to be helpful. When a tool isn't available, the agent's instinct is to find an alternative approach rather than stop. This is usually a feature — you want an agent that works around minor obstacles.

But in security analysis, methodology matters. A weaker analysis method doesn't just produce fewer findings — it produces a report that *looks* equally authoritative but has blind spots. The consumer of the report has no way to know the methodology was downgraded.

### Binary analysis vs. string matching

To make this concrete, consider two approaches to analyzing an Android APK:

| Method | What it finds | What it misses |
|--------|--------------|----------------|
| **Binary analysis** (e.g., [Rizin](https://rizin.re/) `rz-bin`) | DEX class names, method signatures, symbol tables, linked native libraries | — |
| **String matching** (regex/grep fallback) | String literals containing known SDK package names | Obfuscated code, native libraries, SDK components without recognizable string patterns |

[Rizin](https://rizin.re/) is a binary analysis framework. Its `rz-bin` tool can parse DEX bytecode, extract symbol tables, identify linked libraries, and dump structured data from compiled binaries. This gives high-confidence findings because you're examining what the code actually contains, not guessing from string patterns.

If `rz-bin` isn't available and the agent falls back to regex-based analysis (grepping for patterns like `com.google.firebase` or `amazonaws.com`), the output looks complete — same report structure, same JSON format, findings listed with names and risk levels. But the findings are shallower, and you wouldn't notice unless you compared against a Rizin-powered run.

## Step 1: Classify your tools into tiers

Map every external tool your agent uses into one of three tiers:

| Tier | If missing | Action |
|------|-----------|--------|
| **Required** | Results are unreliable | Abort with error |
| **Enhancing** | Results are weaker but valid | Warn in output, continue |
| **Optional** | No impact on core results | Silently skip |

For security work, most analysis tools are **Required**. Don't let the agent decide to downgrade.

Example classification for an APK audit pipeline:

| Tool | Tier | Reason |
|------|------|--------|
| `rz-bin` | Required | Binary analysis is the core methodology |
| `curl` | Required | Can't download APKs without it |
| `python3` | Required | Runs spiritwriter, scoring scripts |
| `jq` | Enhancing | JSON pretty-printing; agent can parse JSON without it |

## Step 2: Add tool verification to your prompt

Add a verification preamble to your skill or prompt that checks for Required tools before doing any work. The agent should abort with an explicit error if something is missing.

```
BEFORE STARTING ANY WORK, verify the following tools are available:

1. Run: which rz-bin
   Expected: /opt/homebrew/bin/rz-bin
   If missing: STOP. Output {"error": "rz-bin not found", "action": "install rizin via brew"} and exit.

2. Run: rz-bin -v
   Expected: rizin 0.7+
   If version is too old: STOP. Output {"error": "rz-bin too old", "action": "brew upgrade rizin"}.

Do NOT fall back to alternative analysis methods. If rz-bin is unavailable, the audit cannot proceed.
```

The key line is: **"Do NOT fall back to alternative analysis methods."** Without this, the agent will try to be helpful and find a workaround.

## Step 3: Add verification to your skill file

If you're using a skill file (passed via `--append-system-prompt-file`), add a prerequisites table at the top:

```markdown
## Prerequisites (MUST verify before proceeding)

Run each command below. If ANY fails, stop immediately and report the error.

| Tool | Command | Expected |
|------|---------|----------|
| Rizin | `which rz-bin` | `/opt/homebrew/bin/rz-bin` |
| Python | `python3 --version` | 3.10+ |
| curl | `which curl` | any path |
```

## Step 4: Add a wrapper script for pre-flight checks

For additional safety, run a pre-flight check before dispatching the agent:

```bash
#!/bin/bash
# pre-flight.sh — run before dispatching audit agents

REQUIRED_TOOLS=("rz-bin" "curl" "python3" "jq")

for tool in "${REQUIRED_TOOLS[@]}"; do
  if ! command -v "$tool" &>/dev/null; then
    echo "FATAL: $tool not found in PATH" >&2
    exit 1
  fi
done

echo "All tools verified. Proceeding."
```

This catches problems before the agent even starts, saving time and API costs.

## Step 5: Detect degradation in completed runs

If you're reviewing output from an agent that already ran, look for these signals:

1. **Missing tool references in trace logs.** If the agent was supposed to use `rz-bin` but the trace shows only `grep` and `strings` commands, the methodology was downgraded.

2. **Finding count is lower than expected.** If a known-bad APK produces 5 findings instead of the usual 8-12, suspect a weaker analysis method.

3. **No binary-specific findings.** Rizin finds things like native library linkage and obfuscated class names that regex can't. If all findings are string-match patterns (`com.google.firebase`, `com.facebook.sdk`), the analysis was likely regex-only.

4. **Provenance trace shows the method.** If you're using spiritwriter (see [Lesson 4](04-trace-as-verification-layer.md)), each step emits trace events. A missing `rizin_extraction` event tells you binary analysis didn't happen — even if the final report looks complete.

## Checklist

- [ ] List every external tool your agent uses during analysis
- [ ] Classify each tool: Required / Enhancing / Optional
- [ ] Add verification commands to your skill preamble for all Required tools
- [ ] Include explicit "do NOT fall back" instructions for Required tools
- [ ] Run `pre-flight.sh` (or equivalent) before dispatching agents
- [ ] After runs, check trace logs for tool usage (did it actually call what you expected?)

---

Previous: [Lesson 1: Environment & Permissions](01-environment-and-permissions.md)
Next: [Lesson 3: Prompt Ambiguity and Non-Deterministic Output](03-prompt-ambiguity-and-nondeterministic-output.md)
