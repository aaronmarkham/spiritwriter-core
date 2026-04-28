# Lesson 1: Environment & Permissions

> Before you hand work to an agent, verify the environment it will run in. Agents can't ask for help — they just fail or silently degrade.

## What you'll learn

In this lesson you'll set up Claude Code to run agents headlessly from the CLI or from within an IDE session. You'll configure permissions, ensure the right tools are on the PATH, and verify everything works before dispatching real work.

## Prerequisites

- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed
- A terminal with your normal shell profile loaded
- A project directory you want agents to operate in

## Concepts

### Modes of running Claude Code agents

Claude Code can run agents in two ways:

1. **Interactive mode** — the default. You chat with Claude in a terminal or IDE, and it asks permission before running commands.
2. **Headless mode** (`claude -p`) — Claude runs as an independent process with no interactive prompts. This is what you use for automation, batch processing, and unattended pipelines.

There's also a **Task tool** (subagent) mode available inside interactive sessions. Subagents spawned this way inherit the parent session's permission restrictions and can't request additional permissions interactively. For batch work, headless mode is more reliable.

### Permission system

Claude Code gates access to tools like `Bash`, `WebFetch`, and `WebSearch` through a permission system. In interactive mode, you're prompted to approve each tool. In headless mode, you need to pre-configure permissions using one of:

- `--permission-mode bypassPermissions` — allows all tools (use for trusted automation)
- `--allowedTools "Bash,Read,Write,Edit,Grep,Glob"` — explicit allowlist

If your project has a `settings.local.json` with a tool allowlist, subagents spawned by the Task tool inherit those restrictions. You can't grant additional permissions to subagents at runtime.

**Rule:** If your agent needs `Bash`, `WebFetch`, or `WebSearch`, verify the permission path before dispatching. Don't discover this at runtime.

## Step 1: Identify your tools

Before launching an agent, list every external tool it will need. For a security audit pipeline using [Rizin](https://rizin.re/) (a binary analysis framework for examining compiled executables), the list might be:

| Tool | Purpose | Install |
|------|---------|---------|
| `curl` | Download APKs and other artifacts | ships with macOS |
| `rz-bin` | Rizin binary analysis — extracts symbols, classes, and linked libraries from compiled binaries | `brew install rizin` |
| `python3` | Run scripts, spiritwriter | system or `brew install python` |
| `jq` | Parse JSON output | `brew install jq` |

## Step 2: Verify tools are on the PATH

A headless `claude -p` process gets a minimal PATH (`/usr/bin:/bin`). Homebrew tools, pip-installed CLIs, and language runtimes are **not** available unless you explicitly include them.

Check what the agent would see versus what your shell sees:

```bash
# What your shell has
which rz-bin   # /opt/homebrew/bin/rz-bin ✓

# What a headless agent would have (minimal PATH)
env -i PATH=/usr/bin:/bin which rz-bin   # not found ✗
```

Build a PATH string that includes every directory your tools live in. The exact paths depend on your platform:

| Platform | Typical tool directories |
|----------|------------------------|
| macOS (Apple Silicon) | `/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin` |
| macOS (Intel) | `/usr/local/bin:/usr/bin:/bin:/usr/sbin` |
| Linux (with Homebrew) | `/home/linuxbrew/.linuxbrew/bin:/usr/bin:/usr/local/bin:/bin` |
| Linux (system packages) | `/usr/bin:/usr/local/bin:/bin:/usr/sbin` |

The examples in this workshop use macOS Apple Silicon paths. Adjust for your platform.

## Step 3: Set the working directory

The agent starts in whatever directory you launch it from. If your skill or prompt references relative paths like `./docs/audits/`, those resolve relative to the launch directory.

```bash
# Launch from the project root
cd /path/to/project && claude -p "..."
```

**CLI vs. IDE behavior:** The CLI has no problem navigating outside the starting directory tree (e.g., `../parent-folder`). However, the IDE extension may produce errors or unexpected behavior if the target directory isn't part of the current workspace. See the [Claude Code docs](https://docs.anthropic.com/en/docs/claude-code) for current details on workspace scoping.

**Rule:** Always launch from the project root. Use absolute paths in prompts when referencing specific files.

## Step 4: Invoke a headless agent

Here's how to invoke an agent directly from the CLI with the correct permissions, PATH, and tool configuration:

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
- `--append-system-prompt-file` loads a file and appends it to the default system prompt (vs `--system-prompt` which replaces it). Note: `claude --help` lists `--append-system-prompt <prompt>` for inline text; the `-file` variant is documented in the `--bare` section and accepts a file path.
- `--output-format stream-json --verbose` gives you real-time progress (plain `json` buffers everything until completion)
- `--max-turns 120` gives the agent room to work — see [Lesson 3](03-prompt-ambiguity-and-nondeterministic-output.md) for sizing guidance

You can also teach your IDE's Claude Code session to invoke agents this way — have it run headless `claude -p` subprocesses with your specific prompts and settings.

## Step 5: Test with a trivial command

Before dispatching real work, verify the environment with a quick smoke test:

```bash
claude -p "Run: which rz-bin && which curl && echo OK" \
  --allowedTools "Bash" \
  --permission-mode bypassPermissions \
  --settings '{"env":{"PATH":"/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin"}}'
```

If any tool is missing, fix the PATH or install the tool before proceeding.

## Checklist

Before dispatching any agent:

- [ ] Identify the permission mode (interactive, allowlist, or bypass)
- [ ] List every external tool the agent will call (curl, rz-bin, python3, etc.)
- [ ] Verify each tool exists and note its full path
- [ ] Build the PATH string: `/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin`
- [ ] Set the working directory to the project root
- [ ] Run a smoke test to confirm the environment is correct

## Troubleshooting

### Permission denied errors

```
Permission denied: Bash tool not in allowed list
```

This happens when the permission system blocks a tool the agent needs. Common causes:
- Using Task tool subagents that inherit a restrictive `settings.local.json`
- Forgetting `--permission-mode bypassPermissions` or `--allowedTools` in headless mode

**Fix:** Switch to headless mode (`claude -p`) with explicit `--allowedTools` or `--permission-mode bypassPermissions`.

### Command not found

```
/bin/sh: curl: command not found
/bin/sh: rz-bin: command not found
```

The headless process has a minimal PATH. Tools that work in your terminal don't exist for the agent.

**Fix:** Pass the full PATH via `--settings`:

```bash
--settings '{"env":{"PATH":"/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin"}}'
```

**Tip:** To avoid repeating this on every invocation, add the `env` block to your project's `.claude/settings.json`:

```json
{
  "env": {
    "PATH": "/opt/homebrew/bin:/usr/bin:/usr/local/bin:/bin:/usr/sbin"
  }
}
```

Then all `claude -p` invocations from that project will inherit the PATH automatically. The remaining examples in this workshop use the inline `--settings` flag for explicitness, but a project-level settings file is recommended for production use.

## Why this matters for security work

In a standard dev workflow, a missing tool means a build fails and you fix it. In a security audit pipeline, a missing tool means the agent adapts — and you may not notice. That's [Lesson 2](02-tool-availability-and-silent-degradation.md).

---

Next: [Lesson 2: Tool Availability & Silent Degradation](02-tool-availability-and-silent-degradation.md)
