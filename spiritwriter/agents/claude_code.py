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

**Artifacts.** Write tools also record the size of the change
(``lines_added``, ``lines_removed``, ``bytes_written``, counted from the
tool input and then discarded) and, once the write succeeds, a
``artifact_sha256`` of the file on disk. Each version of a file an agent
produced gets a content-addressed identity without its content ever
entering the trace.

**Unknown input is kept, not rejected.** The hook payload format belongs
to Claude Code and changes between versions. Unknown event types are
recorded as ``hook_event`` with their raw ``hook_event_name``, missing
fields are omitted, and the command always exits 0 so a recording
failure can never block the agent.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import sys
from typing import Any
from urllib.parse import urlsplit

from spiritwriter.audit.redact import DETECT_ONLY_PATTERNS, REDACT_PATTERNS
from spiritwriter.fabric.emitter import TraceEmitter
from spiritwriter.fabric.shard import _canonical_json, _sha256

MAIN_AGENT = "main"
SUMMARY_MAX = 120
SPAWN_TOOLS = frozenset({"Agent", "Task"})
FILE_TOOLS = frozenset({"Read", "Write", "Edit", "MultiEdit", "NotebookEdit"})
WRITE_TOOLS = frozenset({"Write", "Edit", "MultiEdit", "NotebookEdit"})
ARTIFACT_MAX_BYTES = 64 * 1024 * 1024  # larger files are not hashed (keeps each hook fast)
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


def scrub(text: str, keep: str = "head") -> str:
    """Replace secret-shaped substrings with ``<REDACTED:class fp:…>`` and cap the length.

    ``keep="tail"`` truncates from the front instead (``…/dir/file.py``), which is what
    file paths need: the filename is the part that identifies the artifact.
    """
    for cls, pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m, _c=cls: f"<REDACTED:{_c} fp:{hashlib.sha256(m.group(0).encode()).hexdigest()[:12]}>", text)
    text = " ".join(text.split())  # one line, no control whitespace
    if len(text) <= SUMMARY_MAX:
        return text
    return "…" + text[-(SUMMARY_MAX - 1):] if keep == "tail" else text[: SUMMARY_MAX - 1] + "…"


def _url_summary(url: str) -> str:
    # Query strings and fragments are where tokens live — drop them. Use hostname (+port), never
    # netloc, so a "user:pass@" prefix never reaches the summary.
    parts = urlsplit(url)
    if not parts.hostname:
        return url.split("?", 1)[0].split("@")[-1]
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return f"{host}{parts.path}"


# Command prefixes that aren't the program: wrappers, and inline NAME=value assignments (which can
# carry secrets like `GH_TOKEN=… cmd`). Skip them so the summary is the actual program name, never a secret.
_WRAPPERS = frozenset({"sudo", "env", "time", "nice", "nohup", "exec", "command", "builtin", "doas", "xargs", "stdbuf", "setsid", "then", "do"})


def _bash_program(command: str) -> str:
    """The program a Bash command runs, skipping wrappers and inline VAR=value assignments.

    Words are split the way the shell does (``shlex``), so a quoted value with spaces
    (``DB_PASSWORD='p@ss w0rd' psql``) stays one word and is skipped whole. A command shlex
    can't parse (an unterminated quote) yields "" rather than a whitespace-split guess, which
    could hand back part of a quoted secret.

    A flag stops the search: we can't know whether a wrapper flag takes a value
    (``sudo -u deploy`` → ``deploy``, ``sudo -p 'pw prompt'`` → the prompt), so rather than
    risk returning a flag's value we report the wrapper name reached so far — a safe, generic
    label. So ``sudo -u deploy psql`` summarizes as ``sudo``, while ``sudo psql`` is ``psql``.
    """
    try:
        words = shlex.split(command)
    except ValueError:
        return ""
    last_wrapper = ""
    for word in words:
        if "=" in word.split("/", 1)[0]:   # NAME=value assignment (not a path like a/b=c)
            continue
        if word.startswith("-"):           # a flag — its value could be a secret, so stop guessing
            return last_wrapper
        base = os.path.basename(word)
        if base in _WRAPPERS:
            last_wrapper = base
            continue
        return base
    return last_wrapper


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
            summary = _bash_program(str(ti.get("command", "")))
    elif tool_name in FILE_TOOLS:
        return scrub(str(ti.get("file_path") or ti.get("notebook_path") or ""), keep="tail")
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


def _lines(text: str) -> list[str]:
    return text.splitlines() if text else []


def change_stats(tool_name: str, tool_input: Any) -> dict[str, int]:
    """Size of a write, from the tool input alone: ``lines_added``, ``lines_removed``, ``bytes_written``.

    Edit and MultiEdit are diffed line by line (old_string → new_string), so the counts are exact
    for the edited region. Write replaces a whole file, but the previous contents aren't in the
    input, so it reports only lines added. Content is used to count and then discarded.
    """
    ti = tool_input if isinstance(tool_input, dict) else {}
    if tool_name == "Write":
        content = str(ti.get("content", ""))
        return {"lines_added": len(_lines(content)), "lines_removed": 0, "bytes_written": len(content.encode("utf-8"))}
    if tool_name in ("Edit", "MultiEdit"):
        edits = ti.get("edits") if tool_name == "MultiEdit" else [ti]
        added = removed = written = 0
        for e in edits if isinstance(edits, list) else []:
            if not isinstance(e, dict):
                continue
            old, new = str(e.get("old_string", "")), str(e.get("new_string", ""))
            for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, _lines(old), _lines(new), autojunk=False).get_opcodes():
                if op in ("replace", "delete"):
                    removed += i2 - i1
                if op in ("replace", "insert"):
                    added += j2 - j1
            written += len(new.encode("utf-8"))
        return {"lines_added": added, "lines_removed": removed, "bytes_written": written}
    if tool_name == "NotebookEdit":
        src = str(ti.get("new_source", ""))
        return {"lines_added": 0 if ti.get("edit_mode") == "delete" else len(_lines(src)), "lines_removed": 0,
                "bytes_written": len(src.encode("utf-8"))}
    return {}


def artifact_receipt(payload: dict[str, Any]) -> dict[str, Any]:
    """Hash the file a successful write tool just produced: ``artifact_sha256`` + ``artifact_bytes``.

    The hash gives each version of an artifact a content-addressed identity, so a trace can show
    that an agent produced exactly this version of a file. Only regular files up to
    :data:`ARTIFACT_MAX_BYTES` are hashed; anything else (missing, too large, unreadable) returns {}.
    Relative paths resolve against the hook's ``cwd``.
    """
    ti = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    path = ti.get("file_path") or ti.get("notebook_path")
    if not path:
        return {}
    path = os.path.join(str(payload.get("cwd") or ""), os.path.expanduser(str(path)))
    try:
        st = os.stat(path)
        if not os.path.isfile(path) or st.st_size > ARTIFACT_MAX_BYTES:
            return {}
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        return {}
    return {"artifact_sha256": h.hexdigest(), "artifact_bytes": st.st_size}


def _digest(value: Any) -> str:
    return _sha256(_canonical_json(value))


def _text_receipt(prefix: str, text: Any) -> dict[str, Any]:
    if not isinstance(text, str):
        return {}
    return {f"{prefix}_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), f"{prefix}_chars": len(text)}


def _project(payload: dict[str, Any]) -> dict[str, Any]:
    """``project``: the basename of the session's working directory, so viewers can label
    sessions ("AgentCrossing") instead of showing a session UUID. Only the last path
    component is kept, not the full path."""
    cwd = str(payload.get("cwd") or "").rstrip("/\\")
    name = os.path.basename(cwd) if cwd else ""
    return {"project": scrub(name)} if name else {}


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
        fields.update(change_stats(str(tool), tool_input))
        return "tool_call", fields
    if hook == "PostToolUse":
        response = payload.get("tool_response")
        fields["ok"] = _tool_ok(response)
        child = response.get("agentId") if isinstance(response, dict) else None
        if tool in SPAWN_TOOLS and child:
            ti = payload.get("tool_input")
            tool_input = ti if isinstance(ti, dict) else {}
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
        fields.update(_project(payload))
        return "prompt_submitted", fields
    if hook == "SessionStart":
        if payload.get("source"):
            fields["source"] = payload["source"]
        fields.update(_project(payload))
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
    if event_type == "tool_result" and fields.get("ok") and payload.get("tool_name") in WRITE_TOOLS:
        fields.update(artifact_receipt(payload))
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
