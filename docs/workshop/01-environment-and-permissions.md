# Lesson 1: Environment & Permissions

> Before you hand work to an agent, verify the environment it will run in. Agents can't ask for help; they just fail or silently degrade.

## What happened

We wanted to run security audits on 20 sheriff's office mobile apps across 5 states. The plan: use Claude Code agents to download APKs, extract evidence, classify findings, and generate provenance — all unattended.

**Attempt 1: Task tool subagents.** Claude Code has a built-in `Task` tool that spawns subagents. We tried it first. Immediately blocked:

```
Permission denied: Bash tool not in allowed list
```

The project had a `settings.local.json` with an allowlist of permitted tools. The main session had approval to use `Bash`, but subagents spawned by the `Task` tool inherit the same restrictions and couldn't get interactive approval. Dead end.

**Attempt 2: Headless mode.** Switched to `claude -p` (pipe mode) which runs Claude Code as an independent process:

```bash
claude -p "Audit these 4 APKs..." \
  --append-system-prompt-file .claude/skills/audit/SKILL.md \
  --allowedTools "Bash,Read,Write,Edit,Grep,Glob" \
  --permission-mode bypassPermissions \
  --output-format stream-json \
  --max-turns 120
```

This worked — but the agent immediately started failing on basic commands:

```
/bin/sh: curl: command not found
/bin/sh: rz-bin: command not found
```

The headless process doesn't inherit your shell's PATH. Tools that work fine in your terminal don't exist for the agent.

**Fix:** Bake PATH into the settings flag:

```bash
claude -p "..." \
  --settings '{"env":{"PATH":"/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin"}}'
```

## The pattern

Every agent environment has three layers you need to verify:

### 1. Permissions: what the agent is allowed to do

Claude Code has a permission system that gates tool access. In interactive mode, you get prompted. In headless mode, you need `--permission-mode bypassPermissions` or a pre-configured allowlist.

If you're using the Task tool (subagents within a session), permissions flow from the parent's `settings.local.json`. You can't grant additional permissions to subagents.

**Rule:** If your agent needs `Bash`, `WebFetch`, or `WebSearch`, verify the permission path before dispatching. Don't discover this at runtime.

### 2. PATH: what executables exist

A headless `claude -p` process gets a minimal PATH (`/usr/bin:/bin`). Homebrew tools, pip-installed CLIs, language runtimes — none of these are available unless you explicitly pass them.

```bash
# What you think the agent has
which rz-bin   # /opt/homebrew/bin/rz-bin ✓

# What the agent actually has
env -i PATH=/usr/bin:/bin which rz-bin   # not found ✗
```

**Rule:** List every external tool your agent needs. Verify each one exists at its expected path. Pass the full PATH via `--settings`.

### 3. Working directory and file access

The agent starts in whatever directory you launch it from. If your skill references relative paths like `./docs/audits/`, those resolve relative to the launch directory.

```bash
# Launch from the project root
cd /path/to/frio && claude -p "..."
```

**Rule:** Always `cd` to the project root before launching. Use absolute paths in prompts when referencing specific files.

## Checklist

Before dispatching any agent:

- [ ] Identify the permission mode (interactive, allowlist, or bypass)
- [ ] List every external tool the agent will call (curl, rz-bin, python3, etc.)
- [ ] Verify each tool exists and note its full path
- [ ] Build the PATH string: `/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin`
- [ ] Set the working directory to the project root
- [ ] Test with a trivial command: `claude -p "Run: which rz-bin && echo OK" --settings '{"env":{"PATH":"..."}}'`

## What this looks like in practice

Here's the full working command pattern we converged on after debugging:

```bash
cd /path/to/project && \
/Users/you/.local/bin/claude -p "YOUR TASK" \
  --append-system-prompt-file .claude/skills/audit/SKILL.md \
  --allowedTools "Bash,Read,Write,Edit,Grep,Glob,WebSearch,WebFetch" \
  --permission-mode bypassPermissions \
  --output-format stream-json \
  --verbose \
  --max-turns 120 \
  --settings '{"env":{"PATH":"/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin"}}'
```

Key flags:
- `--append-system-prompt-file` embeds the skill as additional system context (vs `--system-prompt` which replaces the default)
- `--output-format stream-json --verbose` gives you real-time progress (plain `json` buffers everything until completion)
- `--max-turns 120` gives the agent room to work — see [Lesson 3](03-prompt-ambiguity-and-nondeterministic-output.md) for sizing guidance

## Why this matters for security work

In a standard dev workflow, a missing tool means a build fails and you fix it. In a security audit pipeline, a missing tool means the agent adapts — and you may not notice. That's [Lesson 2](02-tool-availability-and-silent-degradation.md).

---

Next: [Lesson 2: Tool Availability & Silent Degradation](02-tool-availability-and-silent-degradation.md)
