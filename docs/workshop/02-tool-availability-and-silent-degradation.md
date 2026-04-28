# Lesson 2: Tool Availability & Silent Degradation

> Agents adapt — which is usually good, but in security analysis means they'll silently downgrade their methodology without telling you.

## What happened

Our audit skill uses [Rizin](https://rizin.re/) (`rz-bin`) for binary analysis of Android APKs. Rizin can parse DEX bytecode, extract symbol tables, identify linked libraries, and dump structured data from compiled binaries. This gives high-confidence findings — you're looking at what the code actually contains, not guessing from string patterns.

Early in our audit runs, Rizin wasn't installed on the machine. The agent encountered `rz-bin: command not found` and... kept going. It didn't error out. It didn't report a problem. It fell back to regex-based analysis: grepping through APK contents for patterns like `com.google.firebase`, `com.facebook.sdk`, `amazonaws.com`.

The output looked complete. The report had the same structure. Findings were listed with names, categories, and risk levels. The JSON was valid. If you weren't comparing against a Rizin-powered run, you'd never notice.

But the findings were shallower:

| Method | What it finds | What it misses |
|--------|--------------|----------------|
| Rizin binary analysis | DEX class names, method signatures, symbol tables, linked native libraries | — |
| Regex fallback | String literals containing known SDK package names | Obfuscated code, native libraries, SDK components that don't have recognizable string patterns |

We only noticed the difference when we ran the same APK with Rizin available and got additional findings that the regex pass had missed.

## Why agents do this

LLMs are trained to be helpful. When a tool isn't available, the agent's instinct is to find an alternative approach rather than stop. This is usually a feature — you want an agent that works around minor obstacles.

But in security analysis, methodology matters. A weaker analysis method doesn't just produce fewer findings — it produces a report that *looks* equally authoritative but has blind spots. The consumer of the report (a human making security decisions) has no way to know the methodology was downgraded.

## The fix: fail loudly

Add a tool verification preamble to your skill or prompt. Check for required tools before doing any work, and abort with an explicit error if something is missing.

### Option A: In the prompt

```
BEFORE STARTING ANY AUDIT WORK, verify the following tools are available:

1. Run: which rz-bin
   Expected: /opt/homebrew/bin/rz-bin
   If missing: STOP. Output {"error": "rz-bin not found", "action": "install rizin via brew"} and exit.

2. Run: rz-bin -v
   Expected: rizin 0.7+
   If version is too old: STOP. Output {"error": "rz-bin too old", "action": "brew upgrade rizin"}.

Do NOT fall back to alternative analysis methods. If rz-bin is unavailable, the audit cannot proceed.
```

### Option B: In a wrapper script

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

### Option C: In the skill file

```markdown
## Prerequisites (MUST verify before proceeding)

Run each command below. If ANY fails, stop immediately and report the error.

| Tool | Command | Expected |
|------|---------|----------|
| Rizin | `which rz-bin` | `/opt/homebrew/bin/rz-bin` |
| Python | `python3 --version` | 3.10+ |
| curl | `which curl` | any path |
```

## Detecting degradation after the fact

If you can't prevent degradation (maybe you're reviewing output from an agent that already ran), look for these signals:

1. **Missing tool references in trace logs.** If the agent was supposed to use `rz-bin` but the trace shows only `grep` and `strings` commands, the methodology was downgraded.

2. **Finding count is lower than expected.** If a known-bad APK produces 5 findings instead of the usual 8-12, suspect a weaker analysis method.

3. **No binary-specific findings.** Rizin finds things like native library linkage and obfuscated class names that regex can't. If all findings are string-match patterns (`com.google.firebase`, `com.facebook.sdk`), the analysis was likely regex-only.

4. **Provenance trace shows the method.** If you're using spiritwriter (see [Lesson 4](04-trace-as-verification-layer.md)), each step of the analysis emits trace events. A missing `rizin_extraction` event in the trace chain tells you the binary analysis didn't happen — even if the final report looks complete.

## The broader principle

This isn't just about Rizin. Any agent-driven pipeline has tools that range from "nice to have" to "critical for correctness." Map your tools into tiers:

| Tier | If missing | Action |
|------|-----------|--------|
| **Required** | Results are unreliable | Abort with error |
| **Enhancing** | Results are weaker but valid | Warn in output, continue |
| **Optional** | No impact on core results | Silently skip |

For security work, most analysis tools are Tier 1 (Required). Don't let the agent decide to downgrade.

## Checklist

- [ ] List every external tool your agent uses during analysis
- [ ] Classify each tool: Required / Enhancing / Optional
- [ ] Add verification commands to your skill preamble for all Required tools
- [ ] Include explicit "do NOT fall back" instructions for Required tools
- [ ] After runs, check trace logs for tool usage (did it actually call what you expected?)

---

Previous: [Lesson 1: Environment & Permissions](01-environment-and-permissions.md)
Next: [Lesson 3: Prompt Ambiguity and Non-Deterministic Output](03-prompt-ambiguity-and-nondeterministic-output.md)
