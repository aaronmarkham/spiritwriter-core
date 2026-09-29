"""Record Claude Code hook events as a hash-chained trace.

Point Claude Code's hooks at the ``spiritwriter-claude-hook`` command
and every session becomes one trace file (``<session_id>.jsonl``) that
records who did what: the main agent, each subagent, every tool call,
and which tool call spawned which subagent. Every hook runs as its own
short-lived process, so the file is written with
``TraceEmitter(concurrent=True)``.

**Identity and lineage come from Claude Code, not from us.**

- ``run_id`` is the ``session_id``.
- ``agent_id`` is the hook's ``agent_id`` for subagent events. Events
  from the main agent carry no ``agent_id``, so they are recorded as
  :data:`MAIN_AGENT`.
- A tool call and its result share ``tool_use_id``.
- When a spawn tool (``Agent``/``Task``) returns, its ``PostToolUse``
  carries the child's id in ``tool_response.agentId``. That is recorded
  as a ``spawn_with_shards`` event with ``child_agent_id``, which is the
  parent→child link. Nested spawns link the same way, because the
  spawning subagent's own events carry its ``agent_id``.

**What is recorded, and what never is.** Hooks see raw tool inputs and
outputs: shell commands, file contents, fetched pages, prompts. None of
that is written. Tool inputs become ``args_sha256`` (a hash of the
canonical JSON) plus a short ``args_summary`` built from a per-tool
allowlist of low-risk fields, then scrubbed of secret shapes and capped
at :data:`SUMMARY_MAX` characters. Prompts and final messages are
recorded only as a hash and a length. Tool outputs are not recorded,
apart from an ``ok`` flag and the child id of a spawn.

**Unknown input is kept, not rejected.** The hook payload format belongs
to Claude Code and changes between versions. Unknown event types are
recorded as ``hook_event`` with their raw ``hook_event_name``, missing
fields are omitted, and the command always exits 0 so a recording
failure can never block the agent.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from typing import Any
from urllib.parse import urlsplit

from spiritwriter.audit.redact import DETECT_ONLY_PATTERNS, REDACT_PATTERNS
from spiritwriter.fabric.emitter import TraceEmitter
from spiritwriter.fabric.shard import _canonical_json, _sha256

MAIN_AGENT = "main"
SUMMARY_MAX = 120
SPAWN_TOOLS = frozenset({"Agent", "Task"})
DEFAULT_TRACE_DIR = os.path.join("~", ".spiritwriter", "claude-code", "traces")
TRACE_DIR_ENV = "SPIRITWRITER_CLAUDE_CODE_TRACE_DIR"

# Secret shapes common in agent sessions that the audit redactor's lists
# don't cover. Summaries are allowlisted fields first; this is the
# second line of defence, not the first.
_EXTRA_SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("anthropic_key", re.compile(r"\bsk-ant-[0-9A-Za-z_-]{16,}")),
    ("openai_style_key", re.compile(r"\bsk-(?:proj-)?[0-9A-Za-z_-]{20,}")),
    ("github_pat", re.compile(r"\bgithub_pat_[0-9A-Za-z_]{20,}")),
    ("jwt", re.compile(r"\beyJ[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}\.[0-9A-Za-z_-]{8,}")),
]
_SECRET_PATTERNS = REDACT_PATTERNS + DETECT_ONLY_PATTERNS + _EXTRA_SECRET_PATTERNS


def scrub(text: str) -> str:
    """Replace secret-shaped substrings with ``<REDACTED:class fp:…>`` and cap the length."""
    for cls, pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m, _c=cls: f"<REDACTED:{_c} fp:{hashlib.sha256(m.group(0).encode()).hexdigest()[:12]}>", text)
    text = " ".join(text.split())  # one line, no control whitespace
    return text if len(text) <= SUMMARY_MAX else text[: SUMMARY_MAX - 1] + "…"


def _url_summary(url: str) -> str:
    # Query strings and fragments are where tokens live; keep host + path.
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path}" if parts.netloc else url.split("?", 1)[0]


def summarize_tool_input(tool_name: str, tool_input: Any) -> str:
    """Short, low-risk description of a tool call, built from allowlisted fields.

    Unlisted tools (including MCP tools) record only their argument
    names, never their values.
    """
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Bash":
        # The model-written description, else just the program name.
        desc = ti.get("description")
        if desc:
            summary = str(desc)
        else:
            words = str(ti.get("command", "")).split()
            summary = os.path.basename(words[0]) if words else ""
    elif tool_name in ("Read", "Write", "Edit", "MultiEdit", "NotebookEdit"):
        summary = str(ti.get("file_path") or ti.get("notebook_path") or "")
    elif tool_name in ("Glob", "Grep"):
        summary = " in ".join(str(v) for v in (ti.get("pattern"), ti.get("path")) if v)
    elif tool_name == "WebFetch":
        summary = _url_summary(str(ti.get("url", "")))
    elif tool_name == "WebSearch":
        summary = str(ti.get("query", ""))
    elif tool_name in SPAWN_TOOLS:
        summary = " · ".join(str(v) for v in (ti.get("subagent_type"), ti.get("description")) if v)
    else:
        summary = "(" + ", ".join(sorted(map(str, ti))) + ")"
    return scrub(summary)


def _digest(value: Any) -> str:
    return _sha256(_canonical_json(value))


def _text_receipt(prefix: str, text: Any) -> dict[str, Any]:
    if not isinstance(text, str):
        return {}
    return {f"{prefix}_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), f"{prefix}_chars": len(text)}


def _tool_ok(tool_response: Any) -> bool:
    if isinstance(tool_response, dict):
        return not any(tool_response.get(k) for k in ("is_error", "isError", "error", "interrupted"))
    return True


def hook_to_event(payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Map one hook payload to ``(event_type, fields)`` for :meth:`TraceEmitter.emit`.

    ``fields`` always includes ``hook_event`` (the raw hook name) and
    ``agent_id``, plus whichever correlation ids the payload carries.
    Absent fields are left out rather than written as null.
    """
    hook = str(payload.get("hook_event_name") or "unknown")
    fields: dict[str, Any] = {
        "hook_event": hook,
        "agent_id": payload.get("agent_id") or MAIN_AGENT,
    }
    for key in ("agent_type", "prompt_id", "tool_use_id", "tool_name", "duration_ms"):
        if payload.get(key) is not None:
            fields[key] = payload[key]
    tool = payload.get("tool_name")

    if hook == "PreToolUse":
        tool_input = payload.get("tool_input", {})
        fields["args_sha256"] = _digest(tool_input)
        fields["args_summary"] = summarize_tool_input(str(tool), tool_input)
        return "tool_call", fields
    if hook == "PostToolUse":
        response = payload.get("tool_response")
        fields["ok"] = _tool_ok(response)
        child = response.get("agentId") if isinstance(response, dict) else None
        if tool in SPAWN_TOOLS and child:
            tool_input = payload.get("tool_input") or {}
            fields.update(
                child_agent_id=child,
                shard_refs=[],
                task=scrub(str(tool_input.get("description", ""))),
                **_text_receipt("task_prompt", tool_input.get("prompt")),
            )
            if isinstance(response, dict) and response.get("resolvedModel"):
                fields["model"] = response["resolvedModel"]
            return "spawn_with_shards", fields
        return "tool_result", fields
    if hook == "SubagentStart":
        return "agent_started", fields
    if hook == "SubagentStop":
        fields.update(_text_receipt("last_message", payload.get("last_assistant_message")))
        return "agent_completed", fields
    if hook == "UserPromptSubmit":
        fields.update(_text_receipt("prompt", payload.get("prompt")))
        return "prompt_submitted", fields
    if hook == "SessionStart":
        if payload.get("source"):
            fields["source"] = payload["source"]
        return "session_started", fields
    if hook == "SessionEnd":
        if payload.get("reason"):
            fields["reason"] = payload["reason"]
        return "session_ended", fields
    if hook == "Stop":
        return "turn_completed", fields
    return "hook_event", fields


def trace_path(session_id: str, trace_dir: str | None = None) -> str:
    """Where a session's trace lives. ``session_id`` is reduced to a safe filename."""
    base = os.path.expanduser(trace_dir or os.environ.get(TRACE_DIR_ENV) or DEFAULT_TRACE_DIR)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id).lstrip(".") or "unknown"
    return os.path.join(base, safe + ".jsonl")


def record(payload: dict[str, Any], trace_dir: str | None = None, signer: Any | None = None) -> dict[str, Any]:
    """Append one hook payload to its session's trace. Returns the emitted event."""
    session_id = str(payload.get("session_id") or "unknown")
    path = trace_path(session_id, trace_dir)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if not os.path.exists(path):
        # Create owner-only before the first append; traces name files and tools.
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    event_type, fields = hook_to_event(payload)
    agent_id = fields.pop("agent_id")
    emitter = TraceEmitter(session_id, agent_id, path, signer=signer, concurrent=True)
    return emitter.emit(event_type, **fields)


def main(argv: list[str] | None = None) -> int:
    """``spiritwriter-claude-hook [--dir DIR]``: record the hook payload on stdin.

    Always returns 0 and writes nothing to stdout, so it can't block or
    steer the agent. Failures go to stderr as one line.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    trace_dir = None
    if "--dir" in args:
        i = args.index("--dir")
        trace_dir = args[i + 1] if i + 1 < len(args) else None
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
        record(payload, trace_dir)
    except Exception as exc:  # never fail the hook
        print(f"spiritwriter-claude-hook: {type(exc).__name__}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
