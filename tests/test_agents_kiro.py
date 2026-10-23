"""Tests for the Kiro hook recorder (spiritwriter.agents.kiro).

``fixtures/kiro/session.jsonl`` holds one synthetic session shaped like the
documented Kiro hook payloads (``hook_event_name``, ``cwd``, ``session_id``,
``tool_name``, ``tool_input``). Kiro does not document the PostToolUse result
fields or the Stop and SessionEnd payloads, so those fixtures are best
guesses; the recorder must tolerate them being different.
"""

from __future__ import annotations

import io
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from spiritwriter.agents import kiro
from spiritwriter.fabric.emitter import fcntl, verify_chain

FIXTURE = Path(__file__).parent / "fixtures" / "kiro" / "session.jsonl"
posix_only = pytest.mark.skipif(fcntl is None, reason="recorder uses concurrent mode (POSIX)")

# Built at runtime so no secret-shaped literal sits in the repo.
AWS_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
ANT_KEY = "sk-ant-" + "api03-abcdefghijklmnopqrstuvwxyz0123"


def _payloads():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Keep every test off the real home directory and the real environment."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in (kiro.TRACE_DIR_ENV, kiro.RAW_ENV, kiro.RAW_FILE_ENV, kiro.RAW_PROMPTS_ENV, kiro.DEBUG_ENV, "USER_PROMPT"):
        monkeypatch.delenv(var, raising=False)


# === Mapping =============================================================


class TestTriggers:
    @pytest.mark.parametrize("name,expected", [
        ("SessionStart", "SessionStart"), ("agentSpawn", "SessionStart"), ("AgentSpawn", "SessionStart"),
        ("UserPromptSubmit", "UserPromptSubmit"), ("userPromptSubmit", "UserPromptSubmit"), ("Prompt Submit", "UserPromptSubmit"),
        ("PreToolUse", "PreToolUse"), ("preToolUse", "PreToolUse"),
        ("PostToolUse", "PostToolUse"), ("postToolUse", "PostToolUse"),
        ("Stop", "Stop"), ("stop", "Stop"), ("Agent Stop", "Stop"),
        ("SessionEnd", "SessionEnd"), ("Session End", "SessionEnd"),
    ])
    def test_names_map_to_claude_style(self, name, expected):
        assert kiro.normalize_trigger(name) == expected

    def test_unknown_or_missing_trigger(self):
        assert kiro.normalize_trigger("PostFileSave") == "PostFileSave"
        assert kiro.normalize_trigger(None) == "unknown"
        assert kiro.normalize_trigger(42) == "unknown"


class TestHookToEvent:
    def test_session_start(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "agentSpawn", "cwd": "/w/demo-app", "session_id": "s"})
        assert etype == "session_started"
        assert f["source"] == "kiro" and f["hook_event"] == "SessionStart" and f["kiro_trigger"] == "agentSpawn"
        assert f["agent_id"] == kiro.MAIN_AGENT and f["project"] == "demo-app"

    def test_prompt_from_env_is_hashed_not_stored(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "UserPromptSubmit"}, env={"USER_PROMPT": "my secret plan"})
        assert etype == "prompt_submitted"
        assert f["prompt_present"] is True and f["prompt_chars"] == len("my secret plan")
        assert len(f["prompt_sha256"]) == 64
        assert "secret plan" not in json.dumps(f)

    def test_prompt_absent(self):
        _, f = kiro.hook_to_event({"hook_event_name": "UserPromptSubmit"}, env={})
        assert f["prompt_present"] is False and "prompt_sha256" not in f

    def test_pre_tool_use_builtin_write(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": "fsWrite",
                                       "tool_input": {"path": "src/a.py", "text": "x\ny\n"}})
        assert etype == "tool_call"
        assert f["tool_kind"] == "write" and f["args_summary"] == "src/a.py"
        assert f["lines_added"] == 2 and f["bytes_written"] == 4
        assert len(f["args_sha256"]) == 64
        assert "x\\ny" not in json.dumps(f)

    @pytest.mark.parametrize("tool,kind", [
        ("read", "read"), ("write", "write"), ("shell", "shell"), ("web", "web"), ("spec", "spec"),
        ("fsRead", "read"), ("fs_read", "read"), ("readFile", "read"), ("grepSearch", "read"),
        ("strReplace", "write"), ("fs_write", "write"), ("deleteFile", "write"),
        ("executeBash", "shell"), ("execute_bash", "shell"),
        ("webFetch", "web"), ("remote_web_search", "web"),
        ("use_aws", "other"), ("somethingNew", "other"),
        ("codegen", "other"), ("credit", "other"), ("tools_x", "other"), ("openai_helper", "other"), ("readCode", "read"),
    ])
    def test_tool_kinds(self, tool, kind):
        assert kiro.classify_tool(tool)["tool_kind"] == kind

    def test_mcp_tool_id_is_split(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": "mcp_postgres_query",
                                       "tool_input": {"sql": "select 1", "password": "hunter2"}})
        assert etype == "tool_call"
        assert (f["tool_kind"], f["mcp_server"], f["mcp_tool"]) == ("mcp", "postgres", "query")
        assert f["args_summary"] == "(password, sql)"  # names only
        assert "hunter2" not in json.dumps(f) and "select 1" not in json.dumps(f)

    def test_mcp_tool_name_with_underscores_and_other_spellings(self):
        assert kiro.classify_tool("mcp_github_create_issue")["mcp_tool"] == "create_issue"
        c = kiro.classify_tool("mcp__github__create_issue")
        assert (c["mcp_server"], c["mcp_tool"]) == ("github", "create_issue")
        c = kiro.classify_tool("@github/create_issue")
        assert (c["mcp_server"], c["mcp_tool"]) == ("github", "create_issue")
        assert kiro.classify_tool("mcp_lonely")["tool_kind"] == "other"

    def test_power_marked_only_when_payload_says_so(self):
        assert "power" not in kiro.classify_tool("mcp_stripe_charge", {"tool_name": "mcp_stripe_charge"})
        c = kiro.classify_tool("mcp_stripe_charge", {"power_name": "stripe"})
        assert c["power"] is True and c["power_name"] == "stripe"
        assert kiro.classify_tool("mcp_x_y", {"tool_source": "power"})["power"] is True

    def test_post_tool_use_ok_and_failure(self):
        _, f = kiro.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "executeBash", "tool_response": {"success": True}})
        assert f["ok"] is True
        _, f = kiro.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "executeBash", "tool_response": {"error": "boom"}})
        assert f["ok"] is False
        _, f = kiro.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "executeBash", "success": False})
        assert f["ok"] is False

    def test_post_tool_use_without_result_fields_is_unknown_not_ok(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "readFile", "tool_input": {"path": "a"}})
        assert etype == "tool_result"
        assert "ok" not in f and f["ok_unknown"] is True

    def test_unknown_extra_fields_are_ignored(self):
        payload = {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "readFile", "tool_input": {"path": "a.py"},
                   "brand_new_field": {"nested": [1, 2, 3]}, "tool_tags": ["x"], "future": None}
        etype, f = kiro.hook_to_event(payload)
        assert etype == "tool_call" and "brand_new_field" not in f and "future" not in f

    def test_unknown_trigger_is_recorded_as_hook_event(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "PostFileSave", "cwd": "/w"})
        assert etype == "hook_event" and f["hook_event"] == "PostFileSave"

    def test_identity_fields_are_scrubbed_and_type_checked(self):
        _, f = kiro.hook_to_event({"hook_event_name": "Stop", "agent_id": f"a:{ANT_KEY}", "agent_type": ["x"],
                                   "tool_use_id": f"t:{AWS_KEY}", "duration_ms": "fast"})
        text = json.dumps(f)
        assert ANT_KEY not in text and AWS_KEY not in text
        assert "agent_type" not in f and "duration_ms" not in f
        _, f = kiro.hook_to_event({"hook_event_name": "Stop", "agent_id": {"x": 1}, "duration_ms": 12})
        assert f["agent_id"] == kiro.MAIN_AGENT and f["duration_ms"] == 12

    def test_invalid_url_port_does_not_drop_the_event(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": "webFetch",
                                       "tool_input": {"url": "http://host:99999999/x"}})
        assert etype == "tool_call" and f["args_summary"] == "(unparseable url)"

    def test_event_argument_fills_a_missing_hook_event_name(self):
        etype, f = kiro.hook_to_event({"session_id": "s"}, event="Stop")
        assert etype == "turn_completed" and f["kiro_trigger"] == "Stop"

    def test_session_end_reason(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "SessionEnd", "reason": "exit"})
        assert etype == "session_ended" and f["reason"] == "exit"

    @pytest.mark.parametrize("payload", [
        {}, {"hook_event_name": None}, {"hook_event_name": 7, "tool_name": 3, "tool_input": "str"},
        {"hook_event_name": "PreToolUse", "tool_name": None, "tool_input": [1, 2]},
        {"hook_event_name": "PostToolUse", "tool_response": "text", "cwd": 5},
        {"hook_event_name": "UserPromptSubmit", "prompt": 12},
    ])
    def test_garbage_shapes_do_not_raise(self, payload):
        etype, f = kiro.hook_to_event(payload, env={})
        assert isinstance(etype, str) and f["source"] == "kiro"


class TestSummaries:
    def test_shell_summary_is_program_only(self):
        s = kiro.summarize_tool_input("executeBash", {"command": "GH_TOKEN='a b c' git push"})
        assert s == "git"
        assert "a b c" not in s

    def test_web_summary_drops_query_and_userinfo(self):
        s = kiro.summarize_tool_input("webFetch", {"url": "https://user:pw@example.com/a/b?token=zzz"})
        assert s == "example.com/a/b"

    def test_nested_path_shape(self):
        assert kiro.summarize_tool_input("fs_read", {"operations": [{"mode": "Line", "path": "/x/y.py"}]}) == "/x/y.py"

    def test_non_dict_input(self):
        assert kiro.summarize_tool_input("readFile", "oops") == "()"


# === Redaction ===========================================================


class TestRedaction:
    def test_secret_in_path_and_description_is_scrubbed(self):
        _, f = kiro.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": "executeBash",
                                   "tool_input": {"command": "x", "description": f"deploy with {AWS_KEY}"}})
        assert AWS_KEY not in json.dumps(f) and "REDACTED" in f["args_summary"]

    def test_secret_in_tool_name_and_trigger(self):
        _, f = kiro.hook_to_event({"hook_event_name": f"Weird:{ANT_KEY}", "tool_name": f"mcp_{ANT_KEY}_x"})
        assert ANT_KEY not in json.dumps(f)

    def test_secret_in_cwd_project(self):
        _, f = kiro.hook_to_event({"hook_event_name": "SessionStart", "cwd": f"/w/{ANT_KEY}"})
        assert ANT_KEY not in json.dumps(f)

    def test_redact_value_covers_patterns_and_secret_keys(self):
        out = kiro.redact_value({"a": f"key {AWS_KEY} here", "password": "hunter2", "n": {"api_key": "zzz", "ok": 1},
                                 "l": [f"{ANT_KEY}"]})
        text = json.dumps(out)
        assert AWS_KEY not in text and ANT_KEY not in text and "hunter2" not in text and "zzz" not in text
        assert out["n"]["ok"] == 1

    def test_redact_value_caps_long_strings(self):
        out = kiro.redact_value("x" * (kiro.RAW_STRING_MAX + 500))
        assert len(out) < kiro.RAW_STRING_MAX + 100 and "TRUNCATED" in out


# === Session identity ====================================================


class TestSessionKey:
    def test_uses_session_id(self):
        assert kiro.session_key({"session_id": "abc"}) == "abc"

    def test_fallback_is_cwd_plus_date(self):
        import datetime
        day = datetime.date(2026, 10, 23)
        a = kiro.session_key({"cwd": "/w/a"}, today=day)
        assert a.startswith("kiro-") and a.endswith("-20261023")
        assert a == kiro.session_key({"cwd": "/w/a", "session_id": ""}, today=day)
        assert a != kiro.session_key({"cwd": "/w/b"}, today=day)

    def test_fallback_without_cwd(self):
        assert kiro.session_key({}).startswith("kiro-")

    @posix_only
    def test_missing_session_id_still_records_one_chain(self, tmp_path):
        kiro.record({"hook_event_name": "SessionStart", "cwd": "/w/a"}, trace_dir=str(tmp_path))
        kiro.record({"hook_event_name": "Stop", "cwd": "/w/a"}, trace_dir=str(tmp_path))
        (trace,) = list(tmp_path.glob("*.jsonl"))
        events = _read(trace)
        assert len(events) == 2 and verify_chain(events)
        assert events[0]["run_id"] == kiro.session_key({"cwd": "/w/a"})


# === Recording ===========================================================


def _record_all(tmp_path):
    for p in _payloads():
        kiro.record(p, trace_dir=str(tmp_path))
    (trace,) = list(tmp_path.glob("*.jsonl"))
    return _read(trace)


@posix_only
class TestRecord:
    def test_fixture_records_one_valid_chain(self, tmp_path):
        events = _record_all(tmp_path)
        assert len(events) == len(_payloads())
        assert verify_chain(events)
        assert {e["run_id"] for e in events} == {"kiro-sess-1"}
        assert {e["source"] for e in events} == {"kiro"}
        assert {e["agent_id"] for e in events} == {kiro.MAIN_AGENT}

    def test_fixture_event_types(self, tmp_path):
        types = [e["type"] for e in _record_all(tmp_path)]
        assert types == ["session_started", "prompt_submitted", "tool_call", "tool_result", "tool_call", "tool_result",
                         "tool_call", "turn_completed", "session_ended"]

    def test_existing_readers_work(self, tmp_path):
        from spiritwriter.fabric import ChainVerifier, read_events_since
        _record_all(tmp_path)
        (trace,) = list(tmp_path.glob("*.jsonl"))
        events, _ = read_events_since(str(trace))
        v = ChainVerifier()
        assert all(v.feed(e) for e in events)

    def test_call_and_result_pair_by_args_hash(self, tmp_path):
        events = _record_all(tmp_path)
        call, result = events[2], events[3]
        assert call["args_sha256"] == result["args_sha256"] and result["ok"] is True

    def test_write_result_carries_artifact_hash(self, tmp_path):
        ws = tmp_path / "ws"
        (ws / "src").mkdir(parents=True)
        (ws / "src" / "client.py").write_text("import time\n")
        kiro.record({"hook_event_name": "PostToolUse", "session_id": "s", "cwd": str(ws), "tool_name": "fsWrite",
                     "tool_input": {"path": "src/client.py", "text": "import time\n"}, "tool_response": {"success": True}},
                    trace_dir=str(tmp_path / "t"))
        (e,) = _read(tmp_path / "t" / "s.jsonl")
        assert len(e["artifact_sha256"]) == 64 and e["artifact_bytes"] == len("import time\n")

    def test_no_raw_tool_io_or_secrets_in_trace(self, tmp_path):
        _record_all(tmp_path)
        (trace,) = list(tmp_path.glob("*.jsonl"))
        text = trace.read_text(encoding="utf-8")
        for raw in ("hunter2", "select 1", "a b c", "add a retry", "import time", "print('hi')"):
            assert raw not in text

    def test_file_permissions(self, tmp_path):
        d = tmp_path / "traces"
        kiro.record({"hook_event_name": "Stop", "session_id": "s1"}, trace_dir=str(d))
        assert stat.S_IMODE(os.stat(d / "s1.jsonl").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700

    def test_session_id_cannot_escape_trace_dir(self, tmp_path):
        for sid in ("../../etc/passwd", "..", "/abs/path", "a\\b", "." * 5):
            p = kiro.trace_path(sid, str(tmp_path))
            assert os.path.dirname(p) == str(tmp_path)
            assert not os.path.basename(p).startswith(".")

    def test_distinct_session_ids_never_share_a_file(self, tmp_path):
        d = str(tmp_path)
        assert kiro.trace_path("a/b", d) != kiro.trace_path("a_b", d)
        assert kiro.trace_path("x" * 300 + "1", d) != kiro.trace_path("x" * 300 + "2", d)
        assert kiro.trace_path("clean-id_1.x", d) == str(tmp_path / "clean-id_1.x.jsonl")

    def test_default_dir_is_kiro_and_env_overrides(self, tmp_path, monkeypatch):
        assert kiro.trace_path("s") == str(tmp_path / "home" / ".spiritwriter" / "kiro" / "traces" / "s.jsonl")
        monkeypatch.setenv(kiro.TRACE_DIR_ENV, str(tmp_path / "x"))
        assert kiro.trace_path("s") == str(tmp_path / "x" / "s.jsonl")

    def test_concurrent_processes_keep_one_chain(self, tmp_path):
        code = ("import sys; from spiritwriter.agents import kiro; "
                "[kiro.record({'hook_event_name':'Stop','session_id':'shared','n':i}, trace_dir=sys.argv[1]) for i in range(10)]")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(tmp_path)]) for _ in range(4)]
        assert all(p.wait(timeout=60) == 0 for p in procs)
        events = _read(tmp_path / "shared.jsonl")
        assert len(events) == 40 and verify_chain(events)


# === Hook entry point ====================================================


class TestMain:
    def _run(self, monkeypatch, capsys, stdin, argv):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        rc = kiro.main(argv)
        return rc, capsys.readouterr()

    @posix_only
    def test_records_and_is_silent(self, tmp_path, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys, json.dumps({"hook_event_name": "SessionStart", "session_id": "s"}),
                            ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == "" and out.err == ""
        assert (tmp_path / "s.jsonl").exists()

    @posix_only
    def test_event_flag_used_when_payload_has_no_name(self, tmp_path, monkeypatch, capsys):
        rc, _ = self._run(monkeypatch, capsys, json.dumps({"session_id": "s"}), ["--event", "Stop", "--dir", str(tmp_path)])
        assert rc == 0
        assert _read(tmp_path / "s.jsonl")[0]["type"] == "turn_completed"

    @pytest.mark.parametrize("stdin", ["not json", "", "[1,2]", "null", '{"hook_event_name": ', "\x00\xff", "{" * 5000])
    def test_malformed_stdin_exits_zero_silently(self, tmp_path, monkeypatch, capsys, stdin):
        rc, out = self._run(monkeypatch, capsys, stdin, ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == "" and out.err == ""

    def test_unwritable_trace_dir_exits_zero_silently(self, tmp_path, monkeypatch, capsys):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        rc, out = self._run(monkeypatch, capsys, '{"session_id": "s"}', ["--dir", str(blocker / "sub")])
        assert rc == 0 and out.out == "" and out.err == ""

    def test_failure_is_logged_not_printed(self, tmp_path, monkeypatch, capsys):
        self._run(monkeypatch, capsys, "not json", ["--dir", str(tmp_path)])
        log = tmp_path / "errors.log"  # next to the traces in use
        assert log.exists() and "JSONDecodeError" in log.read_text()

    def test_debug_env_prints_to_stderr_only(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv(kiro.DEBUG_ENV, "1")
        rc, out = self._run(monkeypatch, capsys, "not json", ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == "" and "spiritwriter-kiro-hook" in out.err

    def test_flag_values_are_not_swallowed(self, tmp_path, monkeypatch, capsys):
        raw = tmp_path / "raw.jsonl"
        rc, _ = self._run(monkeypatch, capsys, '{"session_id": "s", "hook_event_name": "Stop"}',
                          ["--event", "--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")])
        assert rc == 0 and kiro._opt(["--event", "--raw"], "--event") is None
        assert not (Path.cwd() / "--raw").exists()

    def test_alarm_firing_inside_the_error_handler_still_exits_zero(self, tmp_path, monkeypatch, capsys):
        def boom(*a, **k):
            raise TimeoutError("late watchdog")
        monkeypatch.setattr(kiro, "_log_error", boom)
        rc, out = self._run(monkeypatch, capsys, "not json", ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == "" and out.err == ""

    def test_oversized_stdin_is_rejected_quietly(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(kiro, "STDIN_MAX_BYTES", 10)
        rc, out = self._run(monkeypatch, capsys, '{"session_id": "' + "a" * 50 + '"}', ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == "" and not list(tmp_path.glob("*.jsonl"))

    @posix_only
    def test_watchdog_ends_a_stalled_hook(self, tmp_path, monkeypatch, capsys):
        import time
        monkeypatch.setattr(kiro, "WATCHDOG_SECONDS", 1)
        monkeypatch.setattr(kiro, "record", lambda *a, **k: time.sleep(10))
        t0 = time.time()
        rc, out = self._run(monkeypatch, capsys, '{"session_id": "s"}', ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == "" and time.time() - t0 < 5

    @posix_only
    def test_stdin_that_never_closes_does_not_stall_the_hook(self, tmp_path):
        import time
        env = dict(os.environ, HOME=str(tmp_path / "h"))
        p = subprocess.Popen([sys.executable, "-m", "spiritwriter.agents.kiro", "--dir", str(tmp_path)],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            t0 = time.time()
            p.stdin.write(b'{"hook_event_name": "Stop", "session_id": "open-pipe"}')
            p.stdin.flush()  # payload sent, but the pipe stays open
            out, err = p.stdout.read(), p.stderr.read()
            assert p.wait(timeout=10) == 0 and out == b"" and err == b""
            assert time.time() - t0 < kiro.WATCHDOG_SECONDS
        finally:
            p.kill()
        assert (tmp_path / "open-pipe.jsonl").exists()

    def test_subprocess_exit_zero_and_empty_stdout(self, tmp_path):
        env = dict(os.environ, HOME=str(tmp_path / "h"))
        r = subprocess.run([sys.executable, "-m", "spiritwriter.agents.kiro", "--event", "SessionStart", "--dir", str(tmp_path)],
                           input="}{ garbage", text=True, capture_output=True, env=env, timeout=60)
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


# === Raw capture =========================================================


class TestRawCapture:
    def _run(self, monkeypatch, stdin, argv, **env):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        for k, v in env.items():
            monkeypatch.setenv(k, v)
        return kiro.main(argv)

    def test_off_by_default(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, '{"session_id": "s"}', ["--dir", str(tmp_path / "t"), "--raw-file", str(raw)])
        assert not raw.exists()

    def test_env_var_enables_and_redacts(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        payload = {"hook_event_name": "PostToolUse", "session_id": "s", "novel": {"deep": 1},
                   "tool_input": {"cmd": f"curl -H 'x: {AWS_KEY}'", "password": "hunter2"}}
        self._run(monkeypatch, json.dumps(payload), ["--event", "PostToolUse", "--dir", str(tmp_path / "t"), "--raw-file", str(raw)],
                  **{kiro.RAW_ENV: "1"})
        (rec,) = _read(raw)
        assert rec["event_arg"] == "PostToolUse" and rec["hook_event_name"] == "PostToolUse"
        assert "novel" in rec["payload_keys"] and rec["payload"]["novel"] == {"deep": 1}
        text = raw.read_text()
        assert AWS_KEY not in text and "hunter2" not in text
        assert stat.S_IMODE(os.stat(raw).st_mode) == 0o600

    def test_flag_enables(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, '{"session_id": "s"}', ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")])
        assert len(_read(raw)) == 1

    def test_prompt_presence_but_not_content_by_default(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, '{"hook_event_name": "UserPromptSubmit"}', ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")],
                  USER_PROMPT=f"private plan {AWS_KEY}")
        (rec,) = _read(raw)
        assert rec["user_prompt_present"] is True and rec["user_prompt_chars"] > 0
        assert "private plan" not in raw.read_text() and AWS_KEY not in raw.read_text()

    def test_prompt_content_only_redacted_and_opt_in(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, "{}", ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")],
                  USER_PROMPT=f"plan {AWS_KEY}", **{kiro.RAW_PROMPTS_ENV: "1"})
        (rec,) = _read(raw)
        assert rec["user_prompt_redacted"].startswith("plan ") and AWS_KEY not in raw.read_text()

    def test_no_prompt_env(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, "{}", ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")])
        assert _read(raw)[0]["user_prompt_present"] is False

    def test_prompt_fields_in_payload_are_omitted_unless_opted_in(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        payload = json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "refactor my private module", "user_prompt": "also this"})
        self._run(monkeypatch, payload, ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")])
        text = raw.read_text()
        assert "private module" not in text and "also this" not in text and "OMITTED:prompt" in text
        raw.unlink()
        self._run(monkeypatch, payload, ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")], **{kiro.RAW_PROMPTS_ENV: "1"})
        assert "private module" in raw.read_text()

    def test_unparseable_stdin_is_captured_redacted(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, f"oops {AWS_KEY}", ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")])
        (rec,) = _read(raw)
        assert rec["parse_error"] == "JSONDecodeError" and AWS_KEY not in raw.read_text()

    def test_env_values_are_never_captured(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, "{}", ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")], KIRO_SESSION_X="topsecretvalue")
        (rec,) = _read(raw)
        assert "KIRO_SESSION_X" in rec["env_keys"] and "topsecretvalue" not in raw.read_text()

    def test_raw_failure_does_not_stop_recording(self, tmp_path, monkeypatch):
        blocker = tmp_path / "file"
        blocker.write_text("x")
        rc = self._run(monkeypatch, '{"session_id": "s"}', ["--raw", "--raw-file", str(blocker / "r.jsonl"), "--dir", str(tmp_path / "t")])
        assert rc == 0 and (tmp_path / "t" / "s.jsonl").exists()

    def test_concurrent_appends_stay_whole_lines(self, tmp_path):
        raw = tmp_path / "raw.jsonl"
        code = ("import sys, json; from spiritwriter.agents import kiro; "
                "[kiro.capture_raw(json.dumps({'i': i, 'pad': 'x' * 5000}), raw_file=sys.argv[1]) for i in range(20)]")
        procs = [subprocess.Popen([sys.executable, "-c", code, str(raw)]) for _ in range(4)]
        assert all(p.wait(timeout=60) == 0 for p in procs)
        assert len(_read(raw)) == 80


# === Installer ===========================================================


def _install(ws, *args, command="/opt/bin/spiritwriter-kiro-hook"):
    out = io.StringIO()
    rc = kiro.run_install([str(ws), "--command", command, *args], out=out)
    return rc, out.getvalue()


class TestInstaller:
    def test_writes_one_hook_per_trigger(self, tmp_path):
        rc, _ = _install(tmp_path)
        assert rc == 0
        doc = json.loads((tmp_path / ".kiro" / "hooks" / "spiritwriter.json").read_text())
        assert doc["version"] == "v1"
        assert [h["trigger"] for h in doc["hooks"]] == ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"]
        for h in doc["hooks"]:
            assert h["name"].startswith("spiritwriter-") and h["enabled"] is True
            assert h["action"]["type"] == "command"
            assert h["action"]["command"] == f"/opt/bin/spiritwriter-kiro-hook --event {h['trigger']}"
            assert 0 < h["timeout"] <= 10
            assert ("matcher" in h) == (h["trigger"] in ("PreToolUse", "PostToolUse"))
            assert h.get("matcher", "") == ""

    def test_idempotent(self, tmp_path):
        _install(tmp_path)
        path = tmp_path / ".kiro" / "hooks" / "spiritwriter.json"
        first = path.read_text()
        mtime = os.stat(path).st_mtime_ns
        rc, out = _install(tmp_path)
        assert rc == 0 and "already up to date" in out
        assert path.read_text() == first and os.stat(path).st_mtime_ns == mtime

    def test_reinstall_with_new_options_updates(self, tmp_path):
        _install(tmp_path)
        rc, out = _install(tmp_path, "--raw")
        assert "updated" in out
        text = (tmp_path / ".kiro" / "hooks" / "spiritwriter.json").read_text()
        assert text.count("--raw") == 6

    def test_dry_run_writes_nothing(self, tmp_path):
        rc, out = _install(tmp_path, "--dry-run")
        assert rc == 0 and "would create" in out and "spiritwriter-SessionStart" in out
        assert not (tmp_path / ".kiro").exists()

    def test_dry_run_uninstall_keeps_file(self, tmp_path):
        _install(tmp_path)
        rc, out = _install(tmp_path, "--uninstall", "--dry-run")
        assert "would remove" in out and (tmp_path / ".kiro" / "hooks" / "spiritwriter.json").exists()

    def test_uninstall_removes_only_our_file(self, tmp_path):
        hooks = tmp_path / ".kiro" / "hooks"
        hooks.mkdir(parents=True)
        other = hooks / "lint.json"
        other.write_text('{"version": "v1", "hooks": []}')
        _install(tmp_path)
        rc, out = _install(tmp_path, "--uninstall")
        assert rc == 0 and "removed" in out
        assert not (hooks / "spiritwriter.json").exists() and other.read_text() == '{"version": "v1", "hooks": []}'

    def test_uninstall_when_absent(self, tmp_path):
        rc, out = _install(tmp_path, "--uninstall")
        assert rc == 0 and "nothing to remove" in out and not (tmp_path / ".kiro").exists()

    def test_does_not_clobber_other_hook_files(self, tmp_path):
        hooks = tmp_path / ".kiro" / "hooks"
        hooks.mkdir(parents=True)
        other = hooks / "mine.json"
        body = '{"version": "v1", "hooks": [{"name": "mine", "trigger": "Stop", "action": {"type": "command", "command": "echo"}}]}'
        other.write_text(body)
        _install(tmp_path)
        assert other.read_text() == body
        assert sorted(p.name for p in hooks.iterdir()) == ["mine.json", "spiritwriter.json"]

    def test_foreign_hooks_inside_our_file_are_preserved(self, tmp_path):
        _install(tmp_path)
        path = tmp_path / ".kiro" / "hooks" / "spiritwriter.json"
        doc = json.loads(path.read_text())
        doc["hooks"].append({"name": "user-hook", "trigger": "Stop", "action": {"type": "command", "command": "echo hi"}})
        path.write_text(json.dumps(doc))
        _install(tmp_path)
        names = [h["name"] for h in json.loads(path.read_text())["hooks"]]
        assert "user-hook" in names and names.count("spiritwriter-Stop") == 1
        _install(tmp_path, "--uninstall")
        assert [h["name"] for h in json.loads(path.read_text())["hooks"]] == ["user-hook"]

    @pytest.mark.parametrize("content", ["not json", "[1]", '{"hooks": "x"}'])
    def test_refuses_to_overwrite_an_unreadable_file(self, tmp_path, content):
        hooks = tmp_path / ".kiro" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "spiritwriter.json").write_text(content)
        rc, _ = _install(tmp_path)
        assert rc == 1 and (hooks / "spiritwriter.json").read_text() == content
        assert _install(tmp_path, "--uninstall")[0] == 1

    def test_hooks_dir_that_is_a_file_is_an_error_not_a_traceback(self, tmp_path):
        (tmp_path / ".kiro").mkdir()
        (tmp_path / ".kiro" / "hooks").write_text("x")
        assert _install(tmp_path)[0] == 1
        assert (tmp_path / ".kiro" / "hooks").read_text() == "x"

    def test_refuses_without_posix_locking(self, tmp_path, monkeypatch):
        monkeypatch.setattr(kiro, "fcntl", None)
        assert _install(tmp_path)[0] == 1 and not (tmp_path / ".kiro").exists()

    def test_workspace_must_exist(self, tmp_path):
        assert kiro.run_install([str(tmp_path / "nope")], out=io.StringIO()) == 2

    def test_default_workspace_is_cwd(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert kiro.run_install(["--command", "/x/hook"], out=io.StringIO()) == 0
        assert (tmp_path / ".kiro" / "hooks" / "spiritwriter.json").exists()

    def test_global_goes_to_home(self, tmp_path):
        assert kiro.run_install(["--global", "--command", "/x/hook"], out=io.StringIO()) == 0
        assert (tmp_path / "home" / ".kiro" / "hooks" / "spiritwriter.json").exists()
        assert kiro.run_install(["--global", str(tmp_path)], out=io.StringIO()) == 2

    def test_trigger_subset(self, tmp_path):
        _install(tmp_path, "--triggers", "SessionStart,Stop")
        doc = json.loads((tmp_path / ".kiro" / "hooks" / "spiritwriter.json").read_text())
        assert [h["trigger"] for h in doc["hooks"]] == ["SessionStart", "Stop"]
        assert kiro.run_install([str(tmp_path), "--triggers", "Bogus"], out=io.StringIO()) == 2

    def test_workspace_with_spaces_and_default_command_is_absolute_and_quoted(self, tmp_path):
        ws = tmp_path / "my project"
        ws.mkdir()
        assert kiro.run_install([str(ws)], out=io.StringIO()) == 0
        doc = json.loads((ws / ".kiro" / "hooks" / "spiritwriter.json").read_text())
        import shlex
        for h in doc["hooks"]:
            argv = shlex.split(h["action"]["command"])
            exe = argv[0]
            assert os.path.isabs(exe)
            assert argv[-2:] == ["--event", h["trigger"]] or "-m" in argv

    def test_installed_command_runs_and_records(self, tmp_path):
        """End to end: take the command the installer wrote, run it with a payload, verify the trace."""
        import shlex
        _install(tmp_path, command=shlex.quote(sys.executable) + " -m spiritwriter.agents.kiro")
        doc = json.loads((tmp_path / ".kiro" / "hooks" / "spiritwriter.json").read_text())
        env = dict(os.environ, HOME=str(tmp_path / "h"))
        for h in doc["hooks"]:
            r = subprocess.run(h["action"]["command"], shell=True, input=json.dumps({"session_id": "e2e", "cwd": str(tmp_path)}),
                               text=True, capture_output=True, env=env, timeout=60)
            assert r.returncode == 0 and r.stdout == ""
        events = _read(tmp_path / "h" / ".spiritwriter" / "kiro" / "traces" / "e2e.jsonl")
        assert [e["hook_event"] for e in events] == ["SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SessionEnd"]
        assert verify_chain(events)

    def test_main_dispatches_install(self, tmp_path, capsys):
        assert kiro.main(["install", str(tmp_path), "--dry-run", "--command", "/x/hook"]) == 0
        assert "would create" in capsys.readouterr().out
