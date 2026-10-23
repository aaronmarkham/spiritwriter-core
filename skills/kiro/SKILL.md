# Skill: Spiritwriter Kiro Receipts

Record Kiro (IDE and CLI) sessions as hash-chained traces, in the same frame schema as the Claude Code recorder (`skills/claude-code/SKILL.md`). Frames carry `source: "kiro"`.

## Install

```bash
pip install spiritwriter                  # provides spiritwriter-kiro-hook
spiritwriter-kiro-hook install --dry-run  # preview
spiritwriter-kiro-hook install            # .kiro/hooks/spiritwriter.json in the cwd (IDE and CLI)
spiritwriter-kiro-hook install --global   # ~/.kiro/hooks/spiritwriter.json (CLI V3)
spiritwriter-kiro-hook install --uninstall
```

Options: `--raw` (register with raw capture on), `--triggers SessionStart,Stop` (subset), `--command CMD` (override the executable). Only `spiritwriter.json` is written, only `spiritwriter-*` hooks in it are managed, and an unparseable file is left alone.

## Behaviour

The hook reads the payload on stdin, appends one event, prints nothing and always exits 0. Kiro adds a successful hook's stdout to the agent's context, so stdout stays empty. Errors go to `~/.spiritwriter/kiro/errors.log` (`SPIRITWRITER_KIRO_DEBUG=1` also prints to stderr).

Traces: `~/.spiritwriter/kiro/traces/<session_id>.jsonl` (`--dir`, `SPIRITWRITER_KIRO_TRACE_DIR`). No `session_id` in the payload: `kiro-<sha256(cwd)[:12]>-<YYYYMMDD>`.

| Kiro trigger | Trace event |
|--------------|-------------|
| `SessionStart` / `agentSpawn` | `session_started` |
| `UserPromptSubmit` | `prompt_submitted` (prompt from `USER_PROMPT`; hash and length only) |
| `PreToolUse` | `tool_call` (`tool_kind`, `args_sha256`, `args_summary`) |
| `PostToolUse` | `tool_result` (`ok`, or `ok_unknown` when Kiro sends no result fields) |
| `Stop` | `turn_completed` |
| `SessionEnd` | `session_ended` |
| anything else | `hook_event` |

## Raw capture

`SPIRITWRITER_KIRO_RAW=1` (or `--raw`) appends the redacted stdin JSON, trigger and `USER_PROMPT` presence to `~/.spiritwriter/kiro/raw-capture.jsonl` (`SPIRITWRITER_KIRO_RAW_FILE`). Use it to learn the undocumented `PostToolUse`, `Stop` and `SessionEnd` fields. Prompt text only with `SPIRITWRITER_KIRO_RAW_PROMPTS=1`, redacted.

## Reading a trace

Same as Claude Code: `read_events_since`, `verify_chain`, `ChainVerifier`, `follow_events` from `spiritwriter.fabric`.
