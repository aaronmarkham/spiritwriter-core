# Skill: Spiritwriter Claude Code Receipts

Record every Claude Code session as a hash-chained trace: the main agent, each subagent, every tool call, and which call spawned which subagent.

## When to Use

- You want a **receipt** of what a Claude Code session actually did, one you can verify later instead of trusting its own summary
- You want **lineage**: which subagent did what, and which tool call spawned it
- You're building a **live view** of agent activity (tail the trace with `follow_events`)

## Install

```bash
pip install -e /path/to/spiritwriter-core   # provides the spiritwriter-claude-hook command
```

Needs POSIX (Linux, macOS). The recorder writes with `TraceEmitter(concurrent=True)`, which uses `flock`.

## Hook Setup

Add to `~/.claude/settings.json`, or a project's `.claude/settings.json`, and merge with any hooks already there:

```json
{
  "hooks": {
    "SessionStart":     [{"hooks": [{"type": "command", "command": "spiritwriter-claude-hook", "timeout": 5}]}],
    "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "spiritwriter-claude-hook", "timeout": 5}]}],
    "PreToolUse":       [{"matcher": "*", "hooks": [{"type": "command", "command": "spiritwriter-claude-hook", "timeout": 5}]}],
    "PostToolUse":      [{"matcher": "*", "hooks": [{"type": "command", "command": "spiritwriter-claude-hook", "timeout": 5}]}],
    "SubagentStart":    [{"hooks": [{"type": "command", "command": "spiritwriter-claude-hook", "timeout": 5}]}],
    "SubagentStop":     [{"hooks": [{"type": "command", "command": "spiritwriter-claude-hook", "timeout": 5}]}],
    "Stop":             [{"hooks": [{"type": "command", "command": "spiritwriter-claude-hook", "timeout": 5}]}],
    "SessionEnd":       [{"hooks": [{"type": "command", "command": "spiritwriter-claude-hook", "timeout": 5}]}]
  }
}
```

Use the command's absolute path if Claude Code's `PATH` doesn't include your Python's `bin/`. The command reads the hook payload on stdin, appends one event, prints nothing, and always exits 0. A recording failure goes to stderr and never blocks the agent. Each hook costs about 0.1 s of Python startup.

Traces go to `~/.spiritwriter/claude-code/traces/<session_id>.jsonl`, with the directory created 0700 and each file 0600. Override the location with `--dir DIR` or `SPIRITWRITER_CLAUDE_CODE_TRACE_DIR`.

## Event Mapping

| Hook | Trace event | Key fields |
|------|-------------|------------|
| `PreToolUse` | `tool_call` | `tool_use_id`, `tool_name`, `args_sha256`, `args_summary` |
| `PostToolUse` | `tool_result` | `tool_use_id`, `ok`, `duration_ms` |
| `PostToolUse` on `Agent`/`Task` | `spawn_with_shards` | `tool_use_id`, `child_agent_id`, `task`, `model`, `task_prompt_sha256` |
| `SubagentStart` / `SubagentStop` | `agent_started` / `agent_completed` | `agent_type`, `last_message_sha256` |
| `UserPromptSubmit` | `prompt_submitted` | `prompt_sha256`, `prompt_chars` |
| `SessionStart` / `SessionEnd` / `Stop` | `session_started` / `session_ended` / `turn_completed` | `source`, `reason` |
| anything else | `hook_event` | raw `hook_event` name only |

Every event also carries `hook_event` (the raw hook name) and `prompt_id` when present. `run_id` is the session id. `agent_id` is the subagent's id, or `main` for the top-level agent.

**Lineage:** a subagent's events carry its `agent_id`. The `spawn_with_shards` event whose `child_agent_id` equals that id is the call that spawned it, and it shares `tool_use_id` with the parent's `tool_call`. Nested subagents link the same way.

## What Is Never Recorded

- Raw tool inputs and outputs: commands, file contents, fetched pages, MCP arguments.
- Prompt text, subagent prompts, and final messages. These are recorded as a SHA-256 and a length only.

`args_summary` comes from a per-tool allowlist:

| Tool | Summary |
|------|---------|
| `Bash` | the model's `description`, else just the program name |
| `Read` / `Write` / `Edit` | file path |
| `Grep` / `Glob` | pattern and path |
| `WebFetch` | host and path, without the query |
| `Agent` | subagent type and description |
| unlisted and MCP tools | argument names only |

Each summary is scrubbed of secret shapes (AWS, GitHub, Slack, Anthropic/OpenAI keys, JWTs, private keys) and capped at 120 characters. Paths and descriptions still reveal what was worked on, so treat trace files as private.

## Reading a Trace

```python
from spiritwriter.fabric import ChainVerifier, follow_events, read_events_since, verify_chain

events, _ = read_events_since(path)
assert verify_chain(events)

children = {e["child_agent_id"]: e["agent_id"] for e in events if e["type"] == "spawn_with_shards"}

v = ChainVerifier()
for evt, offset in follow_events(path):   # live
    assert v.feed(evt)
```

## Python API

```python
from spiritwriter.agents.claude_code import hook_to_event, record, summarize_tool_input

event_type, fields = hook_to_event(payload)   # pure mapping, no I/O
record(payload, trace_dir="/tmp/traces")      # append to <session_id>.jsonl
record(payload, signer=my_signer)             # Ed25519-sign each event
```

## Compatibility

The hook payload format belongs to Claude Code and changes between versions. Unknown hooks and fields are recorded rather than rejected. `tests/fixtures/claude_code/` holds captured real payloads (Claude Code 2.1), so a format change shows up as a test failure.
