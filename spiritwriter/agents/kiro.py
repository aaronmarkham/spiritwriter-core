"""Record Kiro (IDE and CLI) hook events as a hash-chained trace.

Point Kiro's command hooks at the ``spiritwriter-kiro-hook`` command
(``spiritwriter-kiro-hook install`` writes the hook file for you) and
every session becomes one trace file (``<session_id>.jsonl``) in the
same frame schema as :mod:`spiritwriter.agents.claude_code`, so the
existing readers and verifiers work unchanged. Frames carry
``source: "kiro"`` so viewers can tell the two apart.

**The hook never gets in Kiro's way.**

- It always exits 0.
- It prints nothing to stdout. Kiro adds the stdout of a successful
  command hook to the agent's context for some triggers (Session Start
  at least), so stdout must stay empty.
- It prints nothing to stderr by default either: Kiro forwards stderr of
  a failing hook to the agent or the user. Failures go to a one-line log
  (``errors.log`` next to the traces' parent directory); set
  ``SPIRITWRITER_KIRO_DEBUG=1`` to also see them on stderr.
- A watchdog (:data:`WATCHDOG_SECONDS`) ends the process if recording
  stalls, for example on a lock held by a crashed writer.
- It never blocks a ``PreToolUse`` tool call: it has no decision output.

**Partly undocumented payloads.** Kiro documents ``hook_event_name``,
``cwd``, ``session_id``, ``tool_name`` and ``tool_input`` and, for
Prompt Submit, the ``USER_PROMPT`` environment variable. The
``PostToolUse`` result fields and the ``Stop``/``SessionEnd`` payloads are
not documented. Every field here is optional: unknown fields are ignored,
missing fields are omitted, an unknown trigger is recorded as
``hook_event``. To discover what Kiro really sends, turn on raw capture
(``SPIRITWRITER_KIRO_RAW=1`` or ``--raw``), which appends a redacted copy
of each payload to a local JSONL file.

**Session identity.** ``run_id`` is Kiro's ``session_id``. When a payload
has none, the fallback key is ``kiro-<sha256(cwd)[:12]>-<YYYYMMDD>``
(local date), so a workspace's events for one day still share a chain.
Events from one workspace on one day are therefore merged into one trace
when Kiro does not supply a session id.

**What is recorded, and what never is.** As in the Claude Code recorder,
tool inputs become ``args_sha256`` plus a short ``args_summary`` built
from an allowlist of low-risk fields and scrubbed of secret shapes.
Prompts are recorded as a hash and a length only. Tool outputs are not
recorded.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from typing import Any

from spiritwriter.agents.claude_code import (
    MAIN_AGENT,
    SUMMARY_MAX,
    _SECRET_PATTERNS,
    _bash_program,
    _digest,
    _project,
    _text_receipt,
    _url_summary,
    artifact_receipt,
    scrub,
)
from spiritwriter.fabric.emitter import TraceEmitter, fcntl

SOURCE = "kiro"
DEFAULT_TRACE_DIR = os.path.join("~", ".spiritwriter", "kiro", "traces")
TRACE_DIR_ENV = "SPIRITWRITER_KIRO_TRACE_DIR"
RAW_ENV = "SPIRITWRITER_KIRO_RAW"
RAW_FILE_ENV = "SPIRITWRITER_KIRO_RAW_FILE"
RAW_PROMPTS_ENV = "SPIRITWRITER_KIRO_RAW_PROMPTS"
DEBUG_ENV = "SPIRITWRITER_KIRO_DEBUG"
DEFAULT_RAW_FILE = os.path.join("~", ".spiritwriter", "kiro", "raw-capture.jsonl")
HOOK_FILE_NAME = "spiritwriter.json"
HOOK_NAME_PREFIX = "spiritwriter-"
HOOK_TIMEOUT_SECONDS = 10     # Kiro-side timeout written into the hook file (Kiro's default is 60)
WATCHDOG_SECONDS = 8          # self-imposed; shorter than the Kiro-side timeout
STDIN_MAX_BYTES = 8 * 1024 * 1024
RAW_STRING_MAX = 4000
ERROR_LOG_MAX_BYTES = 256 * 1024

# Kiro trigger -> the Claude-style hook name the trace format uses. Keys are normalized with
# :func:`_norm_trigger` (lowercase, letters and digits only), so "Prompt Submit", "promptSubmit",
# "UserPromptSubmit" and "userpromptsubmit" are one key. Legacy CLI 2.x spellings are included.
_TRIGGER_ALIASES: dict[str, str] = {
    "sessionstart": "SessionStart",
    "agentspawn": "SessionStart",
    "userpromptsubmit": "UserPromptSubmit",
    "promptsubmit": "UserPromptSubmit",
    "pretooluse": "PreToolUse",
    "posttooluse": "PostToolUse",
    "stop": "Stop",
    "agentstop": "Stop",
    "sessionend": "SessionEnd",
}

# Triggers the installer registers, in Kiro's PascalCase spelling.
INSTALL_TRIGGERS = ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd")
_TOOL_TRIGGERS = frozenset({"PreToolUse", "PostToolUse"})

_BUILTIN_CATEGORIES = frozenset({"read", "write", "shell", "web", "spec"})
# Substring heuristics over the normalized tool id; first match wins. Built-in tool ids are not
# documented, so these cover the names seen in Kiro IDE and CLI (fsWrite, strReplace, executeBash,
# fs_read, grepSearch, webFetch, remote_web_search ...). Anything else is kind "other".
_KIND_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("shell", ("bash", "shell", "execute", "terminal", "command")),
    ("web", ("web", "http", "fetch", "browse")),
    ("write", ("write", "replace", "edit", "delete", "create", "append", "rename", "patch", "remove", "move")),
    ("read", ("read", "grep", "glob", "list", "find", "search", "view", "open", "ls", "code")),
)
_PATH_KEYS = ("path", "file_path", "filePath", "filepath", "file", "target_file", "targetFile", "notebook_path")
_NESTED_KEYS = ("operations", "paths", "files", "edits", "ops")
_CONTENT_KEYS = ("content", "text", "fileText", "file_text", "new_str", "newStr", "new_string", "newString", "body")
_OLD_KEYS = ("old_str", "oldStr", "old_string", "oldString")
_SENSITIVE_KEY = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|authorization|credential|private[_-]?key|cookie)", re.I)


# === Trigger and tool normalization ======================================


def _norm_trigger(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def scrub_name(name: str) -> str:
    """:func:`scrub` for identifiers. The secret patterns need a word boundary, and ``_`` is a word
    character, so ``mcp_<key>_tool`` would slip through; scrub each delimiter-separated piece."""
    return "".join(p if re.fullmatch(r"[_/@:\s]", p) else scrub(p) for p in re.split(r"([_/@:\s])", name))


def normalize_trigger(name: Any) -> str:
    """Kiro trigger/hook name -> Claude-style hook name. Unknown names pass through (scrubbed)."""
    if not isinstance(name, str) or not name.strip():
        return "unknown"
    return _TRIGGER_ALIASES.get(_norm_trigger(name), scrub_name(name))


def classify_tool(tool_name: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Tool id -> ``{tool_kind, ...}``.

    ``tool_kind`` is one of ``read``, ``write``, ``shell``, ``web``, ``spec``, ``mcp`` or ``other``.
    ``mcp_<server>_<tool>`` yields ``mcp_server`` and ``mcp_tool``. The server is the text up to the
    first underscore after ``mcp_``; Kiro's id does not delimit server names containing an
    underscore, so such a server name is split early. ``mcp__server__tool`` and ``@server/tool``
    spellings are also split. A Power is marked ``power: true`` only when the payload says so
    (``power``, ``power_name``, ``powerName`` or ``tool_source``); the id format does not show it.
    """
    out: dict[str, Any] = {}
    name = tool_name if isinstance(tool_name, str) else ""
    server = tool = None
    if name.startswith("mcp__") and name.count("__") >= 2:
        _, server, tool = name.split("__", 2)
    elif name.startswith("mcp_") and "_" in name[4:]:
        server, tool = name[4:].split("_", 1)
    elif name.startswith("@") and "/" in name:
        server, tool = name[1:].split("/", 1)
    if server and tool:
        out.update(tool_kind="mcp", mcp_server=scrub_name(server), mcp_tool=scrub_name(tool))
    else:
        key = re.sub(r"[^a-z0-9]", "", name.lower())
        kind = "other"
        if name.lower() in _BUILTIN_CATEGORIES:
            kind = name.lower()
        else:
            for k, hints in _KIND_HINTS:
                if any(h in key for h in hints):
                    kind = k
                    break
        out["tool_kind"] = kind
    p = payload or {}
    power = next((p[k] for k in ("power", "power_name", "powerName") if p.get(k)), None)
    if power or str(p.get("tool_source", "")).lower() in ("power", "powers"):
        out["power"] = True
        if isinstance(power, str):
            out["power_name"] = scrub(power)
    return out


def _first_path(ti: dict[str, Any]) -> str:
    for k in _PATH_KEYS:
        if isinstance(ti.get(k), str) and ti[k]:
            return ti[k]
    for k in _NESTED_KEYS:
        seq = ti.get(k)
        if isinstance(seq, list):
            for item in seq:
                if isinstance(item, str) and item:
                    return item
                if isinstance(item, dict):
                    found = _first_path(item)
                    if found:
                        return found
    return ""


def summarize_tool_input(tool_name: Any, tool_input: Any) -> str:
    """Short, low-risk description of a tool call from allowlisted fields.

    Shell: the program name. Read/write: the file path. Web: host and path without the query, or
    the search query. MCP and unknown tools: argument names only, never values.
    """
    ti = tool_input if isinstance(tool_input, dict) else {}
    kind = classify_tool(tool_name)["tool_kind"]
    if kind == "shell":
        desc = ti.get("description")
        return scrub(str(desc) if desc else _bash_program(str(ti.get("command", ""))))
    if kind in ("read", "write"):
        path = _first_path(ti)
        if path:
            return scrub(path, keep="tail")
        if ti.get("pattern") or ti.get("query"):
            return scrub(str(ti.get("pattern") or ti.get("query")))
    if kind == "web":
        if ti.get("url"):
            return scrub(_url_summary(str(ti["url"])))
        if ti.get("query"):
            return scrub(str(ti["query"]))
    return scrub("(" + ", ".join(sorted(map(str, ti))) + ")")


def _count_lines(text: str) -> int:
    return len(text.splitlines()) if text else 0


def change_stats(tool_input: Any) -> dict[str, int]:
    """Size of a write from the tool input alone (counted, then discarded). Empty if no content."""
    ti = tool_input if isinstance(tool_input, dict) else {}
    new = next((ti[k] for k in _CONTENT_KEYS if isinstance(ti.get(k), str)), None)
    if new is None:
        return {}
    old = next((ti[k] for k in _OLD_KEYS if isinstance(ti.get(k), str)), "")
    return {
        "lines_added": _count_lines(new),
        "lines_removed": _count_lines(old),
        "bytes_written": len(new.encode("utf-8")),
    }


def _result_of(payload: dict[str, Any]) -> Any:
    for k in ("tool_response", "tool_result", "tool_output", "result", "response", "output"):
        if payload.get(k) is not None:
            return payload[k]
    return None


def _tool_ok(payload: dict[str, Any]) -> bool | None:
    """True/False when the payload says; None when it carries no result fields (undocumented)."""
    if payload.get("success") is False:
        return False
    result = _result_of(payload)
    if result is None:
        return None if payload.get("success") is None else True
    if isinstance(result, dict):
        if result.get("success") is False:
            return False
        return not any(result.get(k) for k in ("is_error", "isError", "error", "interrupted"))
    return True


def _prompt_text(payload: dict[str, Any], env: Any = None) -> str | None:
    env = os.environ if env is None else env
    for v in (env.get("USER_PROMPT"), payload.get("prompt"), payload.get("user_prompt")):
        if isinstance(v, str):
            return v
    return None


# === Session identity =====================================================


def session_key(payload: dict[str, Any], today: _dt.date | None = None) -> str:
    """Kiro's ``session_id``, or ``kiro-<sha256(cwd)[:12]>-<YYYYMMDD>`` when it has none."""
    sid = payload.get("session_id")
    if isinstance(sid, (str, int)) and str(sid).strip():
        return str(sid)
    cwd = str(payload.get("cwd") or "")
    day = (today or _dt.date.today()).strftime("%Y%m%d")
    return f"kiro-{hashlib.sha256(cwd.encode('utf-8')).hexdigest()[:12]}-{day}"


# === Mapping ==============================================================


def hook_to_event(payload: dict[str, Any], event: str | None = None, env: Any = None) -> tuple[str, dict[str, Any]]:
    """Map one Kiro hook payload to ``(event_type, fields)`` for :meth:`TraceEmitter.emit`.

    ``event`` is the trigger name the hook command was registered with (``--event``); it is used
    when the payload has no ``hook_event_name``. ``fields`` always has ``hook_event`` (the
    Claude-style name), ``kiro_trigger`` (what Kiro called it), ``source`` and ``agent_id``.
    Absent fields are left out, never written as null.
    """
    raw_name = payload.get("hook_event_name") or event
    hook = normalize_trigger(raw_name)
    fields: dict[str, Any] = {
        "source": SOURCE,
        "hook_event": hook,
        "agent_id": payload.get("agent_id") or MAIN_AGENT,
    }
    if isinstance(raw_name, str) and raw_name:
        fields["kiro_trigger"] = scrub_name(raw_name)
    for key in ("agent_type", "duration_ms"):
        if payload.get(key) is not None:
            fields[key] = payload[key]
    for key in ("tool_use_id", "toolUseId", "tool_call_id"):
        if isinstance(payload.get(key), str) and payload[key]:
            fields["tool_use_id"] = payload[key]
            break
    tool = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if isinstance(tool, str) and tool:
        fields["tool_name"] = scrub_name(tool)
        fields.update(classify_tool(tool, payload))

    if hook == "PreToolUse":
        if tool_input is not None:
            fields["args_sha256"] = _digest(tool_input)
        fields["args_summary"] = summarize_tool_input(tool, tool_input)
        if fields.get("tool_kind") == "write":
            fields.update(change_stats(tool_input))
        return "tool_call", fields
    if hook == "PostToolUse":
        ok = _tool_ok(payload)
        if ok is None:
            fields["ok_unknown"] = True  # Kiro's PostToolUse result fields are undocumented
        else:
            fields["ok"] = ok
        if tool_input is not None:
            fields["args_sha256"] = _digest(tool_input)  # lets readers pair a result with its call
        return "tool_result", fields
    if hook == "UserPromptSubmit":
        prompt = _prompt_text(payload, env)
        if prompt is None:
            fields["prompt_present"] = False
        else:
            fields["prompt_present"] = True
            fields.update(_text_receipt("prompt", prompt))
        fields.update(_project(payload))
        return "prompt_submitted", fields
    if hook == "SessionStart":
        fields.update(_project(payload))
        return "session_started", fields
    if hook == "SessionEnd":
        if isinstance(payload.get("reason"), str):
            fields["reason"] = scrub(payload["reason"])
        return "session_ended", fields
    if hook == "Stop":
        return "turn_completed", fields
    return "hook_event", fields


# === Recording ============================================================


def _base_dir(trace_dir: str | None) -> str:
    return os.path.expanduser(trace_dir or os.environ.get(TRACE_DIR_ENV) or DEFAULT_TRACE_DIR)


def trace_path(session_id: str, trace_dir: str | None = None) -> str:
    """Where a session's trace lives. ``session_id`` is reduced to a safe filename."""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id).lstrip(".")[:200] or "unknown"
    return os.path.join(_base_dir(trace_dir), safe + ".jsonl")


def record(
    payload: dict[str, Any],
    trace_dir: str | None = None,
    signer: Any | None = None,
    event: str | None = None,
    env: Any = None,
) -> dict[str, Any]:
    """Append one hook payload to its session's trace. Returns the emitted event."""
    session_id = session_key(payload)
    path = trace_path(session_id, trace_dir)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    if not os.path.exists(path):
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600))
    event_type, fields = hook_to_event(payload, event, env)
    if event_type == "tool_result" and fields.get("ok") and fields.get("tool_kind") == "write":
        path_arg = _first_path(payload["tool_input"]) if isinstance(payload.get("tool_input"), dict) else ""
        if path_arg:
            fields.update(artifact_receipt({"cwd": payload.get("cwd"), "tool_input": {"file_path": path_arg}}))
    agent_id = fields.pop("agent_id")
    emitter = TraceEmitter(session_id, agent_id, path, signer=signer, concurrent=True)
    return emitter.emit(event_type, **fields)


# === Raw capture ==========================================================


def redact_text(text: str) -> str:
    """Replace secret-shaped substrings with ``<REDACTED:class fp:...>``. No truncation."""
    for cls, pat in _SECRET_PATTERNS:
        text = pat.sub(lambda m, _c=cls: f"<REDACTED:{_c} fp:{hashlib.sha256(m.group(0).encode()).hexdigest()[:12]}>", text)
    return text


def redact_value(value: Any, depth: int = 0) -> Any:
    """Deep copy of a JSON value with secret shapes redacted, secret-named keys blanked, strings capped."""
    if depth > 20:
        return "<TRUNCATED:depth>"
    if isinstance(value, str):
        text = redact_text(value)
        return text if len(text) <= RAW_STRING_MAX else text[:RAW_STRING_MAX] + f"...<TRUNCATED:{len(text)} chars>"
    if isinstance(value, dict):
        return {
            str(k): ("<REDACTED:key>" if _SENSITIVE_KEY.search(str(k)) and not isinstance(v, (dict, list)) else redact_value(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact_value(v, depth + 1) for v in value[:200]]
    return value


def raw_enabled(flag: bool = False, env: Any = None) -> bool:
    env = os.environ if env is None else env
    return flag or str(env.get(RAW_ENV, "")).strip().lower() in ("1", "true", "yes", "on")


def raw_path(raw_file: str | None = None) -> str:
    return os.path.expanduser(raw_file or os.environ.get(RAW_FILE_ENV) or DEFAULT_RAW_FILE)


def capture_raw(
    stdin_text: str,
    event: str | None = None,
    raw_file: str | None = None,
    env: Any = None,
    argv: list[str] | None = None,
) -> dict[str, Any]:
    """Append one redacted raw record to the capture file (JSONL, 0600, flock-guarded). Returns it.

    Holds: the redacted stdin JSON (or, if it did not parse, a redacted text excerpt), the trigger
    name, whether ``USER_PROMPT`` was set and its length, and the names (never values) of
    ``KIRO*`` environment variables. Prompt text is included, redacted, only when
    ``SPIRITWRITER_KIRO_RAW_PROMPTS=1``.
    """
    env = os.environ if env is None else env
    rec: dict[str, Any] = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "event_arg": event,
        "argv": [redact_text(a) for a in (argv or [])],
        "env_keys": sorted(k for k in env if k.upper().startswith("KIRO")),
    }
    try:
        payload = json.loads(stdin_text or "{}")
        rec["payload"] = redact_value(payload)
        if isinstance(payload, dict):
            rec["payload_keys"] = sorted(map(str, payload))
            rec["hook_event_name"] = redact_value(payload.get("hook_event_name"))
    except ValueError as exc:
        rec["parse_error"] = type(exc).__name__
        rec["stdin_excerpt"] = redact_value((stdin_text or "")[:2000])
    prompt = env.get("USER_PROMPT")
    rec["user_prompt_present"] = isinstance(prompt, str)
    if isinstance(prompt, str):
        rec["user_prompt_chars"] = len(prompt)
        if str(env.get(RAW_PROMPTS_ENV, "")).strip().lower() in ("1", "true", "yes", "on"):
            rec["user_prompt_redacted"] = redact_value(prompt)
    path = raw_path(raw_file)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    line = (json.dumps(rec, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, line)
    finally:
        os.close(fd)  # closing releases the flock
    return rec


# === Hook entry point =====================================================


def _log_error(message: str) -> None:
    """Best-effort one-line error log; never raises, never touches stdout."""
    try:
        path = os.path.join(os.path.dirname(_base_dir(None)), "errors.log")
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        if os.path.exists(path) and os.path.getsize(path) > ERROR_LOG_MAX_BYTES:
            return
        stamp = _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(fd, f"{stamp} {redact_text(message)[:500]}\n".encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass
    if os.environ.get(DEBUG_ENV):
        try:
            print(f"spiritwriter-kiro-hook: {message}", file=sys.stderr)
        except Exception:
            pass


def _opt(args: list[str], name: str) -> str | None:
    if name in args:
        i = args.index(name)
        return args[i + 1] if i + 1 < len(args) else None
    return None


def _arm_watchdog(seconds: int) -> None:
    try:
        import signal

        def _expired(signum, frame):  # noqa: ARG001
            raise TimeoutError(f"watchdog expired after {seconds}s")

        signal.signal(signal.SIGALRM, _expired)
        signal.alarm(seconds)
    except (ImportError, AttributeError, ValueError):  # no SIGALRM (Windows) or not the main thread
        pass


def _read_stdin() -> str:
    if sys.stdin is None or sys.stdin.isatty():
        return ""
    data = sys.stdin.read(STDIN_MAX_BYTES + 1)
    if len(data) > STDIN_MAX_BYTES:
        raise ValueError("stdin payload too large")
    return data


def run_hook(argv: list[str]) -> int:
    """Record the hook payload on stdin. Always returns 0 and writes nothing to stdout."""
    event = _opt(argv, "--event")
    trace_dir = _opt(argv, "--dir")
    raw_file = _opt(argv, "--raw-file")
    stdin_text = ""
    _arm_watchdog(WATCHDOG_SECONDS)
    try:
        stdin_text = _read_stdin()
        if raw_enabled("--raw" in argv):
            try:
                capture_raw(stdin_text, event, raw_file, argv=argv)
            except Exception as exc:
                _log_error(f"raw capture failed: {type(exc).__name__}: {exc}")
        payload = json.loads(stdin_text or "{}")
        if not isinstance(payload, dict):
            raise ValueError("hook payload is not a JSON object")
        record(payload, trace_dir, event=event)
    except BaseException as exc:  # never fail the hook (includes the watchdog's TimeoutError)
        _log_error(f"{type(exc).__name__}: {exc}")
    finally:
        try:
            import signal

            signal.alarm(0)
        except Exception:
            pass
    return 0


# === Installer ============================================================


def hook_command(command: str | None = None, event: str | None = None, raw: bool = False) -> str:
    """The shell command a hook runs. Uses an absolute path to the executable."""
    parts = command or default_executable()
    if event:
        parts += f" --event {event}"
    if raw:
        parts += " --raw"
    return parts


def default_executable() -> str:
    """Shell-ready command: the quoted absolute path to ``spiritwriter-kiro-hook``, else
    ``<python> -m spiritwriter.agents.kiro`` with an absolute interpreter path."""
    here = os.path.join(os.path.dirname(os.path.abspath(sys.executable)), "spiritwriter-kiro-hook")
    for cand in (here, shutil.which("spiritwriter-kiro-hook")):
        if cand and os.path.isfile(cand) and os.access(cand, os.X_OK):
            return shlex.quote(os.path.abspath(cand))
    return f"{shlex.quote(os.path.abspath(sys.executable))} -m spiritwriter.agents.kiro"


def build_hooks(command: str | None = None, raw: bool = False, triggers: tuple[str, ...] = INSTALL_TRIGGERS,
                timeout: int = HOOK_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    """One command hook per trigger. An empty matcher on tool triggers means all tools."""
    hooks = []
    for trig in triggers:
        hook: dict[str, Any] = {
            "name": HOOK_NAME_PREFIX + trig,
            "description": f"Managed by spiritwriter: record {trig} to a local signed trace.",
            "trigger": trig,
        }
        if trig in _TOOL_TRIGGERS:
            hook["matcher"] = ""
        hook.update(action={"type": "command", "command": hook_command(command, trig, raw)}, timeout=timeout, enabled=True)
        hooks.append(hook)
    return hooks


def hook_file_path(workspace: str | None = None, global_: bool = False) -> str:
    if global_:
        return os.path.join(os.path.expanduser("~"), ".kiro", "hooks", HOOK_FILE_NAME)
    ws = os.path.abspath(os.path.expanduser(workspace or os.getcwd()))
    return os.path.join(ws, ".kiro", "hooks", HOOK_FILE_NAME)


def _is_ours(hook: Any) -> bool:
    return isinstance(hook, dict) and str(hook.get("name", "")).startswith(HOOK_NAME_PREFIX)


def plan_install(path: str, uninstall: bool = False, command: str | None = None, raw: bool = False,
                 triggers: tuple[str, ...] = INSTALL_TRIGGERS) -> tuple[str, str | None]:
    """Decide what to do for ``path``. Returns ``(action, new_text)``.

    ``action`` is ``create``, ``update``, ``unchanged``, ``remove`` or ``absent``; ``new_text`` is
    the file content to write (None for ``remove``/``absent``/``unchanged``). Only hooks named
    ``spiritwriter-*`` are touched; other hooks in the same file are preserved. Raises
    ``ValueError`` if the existing file is not a valid Kiro hook file, rather than overwrite it.
    """
    existing_text = None
    doc: dict[str, Any] = {"version": "v1", "hooks": []}
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing_text = f.read()
        try:
            loaded = json.loads(existing_text)
        except ValueError as exc:
            raise ValueError(f"{path} is not valid JSON ({exc}); not touching it") from exc
        if not isinstance(loaded, dict) or not isinstance(loaded.get("hooks", []), list):
            raise ValueError(f"{path} is not a Kiro hook file (expected an object with a 'hooks' list); not touching it")
        doc = loaded
        doc.setdefault("version", "v1")
        doc.setdefault("hooks", [])
    others = [h for h in doc["hooks"] if not _is_ours(h)]
    if uninstall:
        if existing_text is None or len(others) == len(doc["hooks"]):
            return "absent", None
        if not others:
            return "remove", None
        doc["hooks"] = others
    else:
        doc["hooks"] = others + build_hooks(command, raw, triggers)
    new_text = json.dumps(doc, indent=2) + "\n"
    if existing_text is None:
        return "create", new_text
    return ("unchanged", None) if new_text == existing_text else ("update", new_text)


def _atomic_write(path: str, text: str) -> None:
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".spiritwriter-", suffix=".tmp", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def run_install(argv: list[str], out: Any = None) -> int:
    """``spiritwriter-kiro-hook install [WORKSPACE] [--global] [--dry-run] [--uninstall] [--raw]
    [--command CMD] [--triggers A,B]``. Returns a process exit code (0 ok, 1 refused, 2 usage)."""
    import argparse

    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="spiritwriter-kiro-hook install", description="Install the spiritwriter hooks for Kiro.")
    ap.add_argument("workspace", nargs="?", help="workspace directory (default: the current directory)")
    ap.add_argument("--global", dest="global_", action="store_true", help="install to ~/.kiro/hooks (Kiro CLI V3)")
    ap.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    ap.add_argument("--uninstall", action="store_true", help="remove the spiritwriter hooks")
    ap.add_argument("--raw", action="store_true", help="register hooks with raw capture on")
    ap.add_argument("--command", help="hook command to register (default: absolute path of this executable)")
    ap.add_argument("--triggers", help=f"comma-separated subset of: {', '.join(INSTALL_TRIGGERS)}")
    try:
        ns = ap.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0) if exc.code != 0 else 0
    if ns.global_ and ns.workspace:
        print("error: give a workspace or --global, not both", file=sys.stderr)
        return 2
    triggers = INSTALL_TRIGGERS
    if ns.triggers:
        triggers = tuple(t.strip() for t in ns.triggers.split(",") if t.strip())
        bad = [t for t in triggers if t not in INSTALL_TRIGGERS]
        if bad or not triggers:
            print(f"error: unknown trigger(s) {bad}; choose from {', '.join(INSTALL_TRIGGERS)}", file=sys.stderr)
            return 2
    if ns.workspace and not os.path.isdir(os.path.expanduser(ns.workspace)):
        print(f"error: workspace {ns.workspace!r} is not a directory", file=sys.stderr)
        return 2
    path = hook_file_path(ns.workspace, ns.global_)
    try:
        action, text = plan_install(path, ns.uninstall, ns.command, ns.raw, triggers)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    verb = {"create": "would create", "update": "would update", "unchanged": "already up to date",
            "remove": "would remove", "absent": "nothing to remove"}[action]
    if not ns.dry_run:
        if text is not None:
            _atomic_write(path, text)
        elif action == "remove":
            os.unlink(path)
        verb = {"create": "created", "update": "updated", "unchanged": "already up to date",
                "remove": "removed", "absent": "nothing to remove"}[action]
    print(f"{verb}: {path}", file=out)
    if ns.dry_run and text is not None:
        print(text, file=out, end="")
    return 0


def main(argv: list[str] | None = None) -> int:
    """``spiritwriter-kiro-hook [install ...]``.

    Without a subcommand: record the hook payload on stdin (always exits 0, prints nothing).
    Options: ``--event NAME`` (trigger, used when the payload lacks ``hook_event_name``),
    ``--dir DIR``, ``--raw``, ``--raw-file FILE``.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "install":
        return run_install(args[1:])
    return run_hook(args)


if __name__ == "__main__":
    sys.exit(main())
