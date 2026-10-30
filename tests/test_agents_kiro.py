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


def _mcp_config(ws, *names):
    d = Path(ws) / ".kiro" / "settings"
    d.mkdir(parents=True, exist_ok=True)
    (d / "mcp.json").write_text(json.dumps({"mcpServers": {n: {"command": "x"} for n in names}}))


def _payloads():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    """Keep every test off the real home directory and the real environment."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for var in (kiro.TRACE_DIR_ENV, kiro.RAW_ENV, kiro.RAW_FILE_ENV, kiro.RAW_PROMPTS_ENV, kiro.DEBUG_ENV, kiro.KIRO_SESSION_ENV, "USER_PROMPT"):
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

    def test_prompt_from_payload_wins_over_env(self):
        _, f = kiro.hook_to_event({"hook_event_name": "UserPromptSubmit", "prompt": "from payload"}, env={"USER_PROMPT": "from env, longer"})
        assert f["prompt_present"] is True and f["prompt_chars"] == len("from payload")

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
        ("invoke_sub_agent", "spawn"), ("subagent_response", "spawn"), ("report_progress", "other"),
        ("codegen", "other"), ("credit", "other"), ("tools_x", "other"), ("openai_helper", "other"), ("readCode", "read"),
    ])
    def test_tool_kinds(self, tool, kind):
        assert kiro.classify_tool(tool)["tool_kind"] == kind

    def test_mcp_tool_id_is_split_when_server_is_configured(self, tmp_path):
        _mcp_config(tmp_path, "postgres")
        etype, f = kiro.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": "mcp_postgres_query", "cwd": str(tmp_path),
                                       "tool_input": {"sql": "select 1", "password": "hunter2"}})
        assert etype == "tool_call"
        assert (f["tool_kind"], f["mcp_id"], f["mcp_server"], f["mcp_tool"]) == ("mcp", "mcp_postgres_query", "postgres", "query")
        assert f["args_summary"] == "(password, sql)"  # names only
        assert "hunter2" not in json.dumps(f) and "select 1" not in json.dumps(f)

    def test_mcp_tool_name_with_underscores_and_other_spellings(self):
        assert kiro.classify_tool("mcp_github_create_issue", None, {"github"})["mcp_tool"] == "create_issue"
        c = kiro.classify_tool("mcp__github__create_issue")
        assert (c["mcp_server"], c["mcp_tool"]) == ("github", "create_issue")
        c = kiro.classify_tool("@github/create_issue")
        assert (c["mcp_server"], c["mcp_tool"]) == ("github", "create_issue")
        assert kiro.classify_tool("mcp_lonely")["tool_kind"] == "other"

    def test_mcp_unconfigured_server_keeps_only_the_raw_id(self, tmp_path):
        c = kiro.classify_tool("mcp_github_create_issue")
        assert c == {"tool_kind": "mcp", "mcp_id": "mcp_github_create_issue"}
        _, f = kiro.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": "mcp_github_create_issue", "cwd": str(tmp_path)})
        assert f["mcp_id"] == "mcp_github_create_issue" and "mcp_server" not in f and "mcp_tool" not in f

    def test_mcp_hyphenated_servers_split_like_kiro_names_them(self, tmp_path):
        _mcp_config(tmp_path, "my-docs-server", "Acme Tools", "acme", "my.weird/Server")
        cwd = str(tmp_path)

        def split(tool):
            _, f = kiro.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": cwd})
            return f.get("mcp_server"), f.get("mcp_tool"), f["mcp_id"]
        assert split("mcp_my_docs_server_search_docs") == ("my_docs_server", "search_docs", "mcp_my_docs_server_search_docs")
        assert split("mcp_acme_tools_run") == ("acme_tools", "run", "mcp_acme_tools_run")  # longest match beats "acme"
        assert split("mcp_acme_other")[:2] == ("acme", "other")
        assert split("mcp_myweirdserver_ping")[:2] == ("myweirdserver", "ping")
        assert split("mcp_unknown_server_tool")[:2] == (None, None)

    def _mcp(self, tmp_path, tool):
        return kiro.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": tool, "cwd": str(tmp_path)})[1]

    def test_mcp_exact_match_has_no_exact_flag(self, tmp_path):
        _mcp_config(tmp_path, "acme-tools")
        f = self._mcp(tmp_path, "mcp_acme_tools_get_page")
        assert (f["mcp_server"], f["mcp_tool"], f["mcp_id"]) == ("acme_tools", "get_page", "mcp_acme_tools_get_page")
        assert "mcp_tool_exact" not in f

    def test_mcp_truncated_id_still_splits_but_is_not_exact(self, tmp_path):
        _mcp_config(tmp_path, "acme-tools")
        long_id = "mcp_acme_tools_" + "x" * 49
        assert len(long_id) == 64
        f = self._mcp(tmp_path, long_id)
        assert f["mcp_id"] == long_id and f["mcp_server"] == "acme_tools" and f["mcp_tool"] == "x" * 49
        assert f["mcp_tool_exact"] is False

    @pytest.mark.parametrize("tool,remainder", [("mcp_acme_tools_get_page_2", "get_page_2"), ("mcp_acme_tools_run_1", "run_1"),
                                                ("mcp_acme_tools_run_12", "run_12")])
    def test_mcp_numeric_suffix_still_splits_but_is_not_exact(self, tmp_path, tool, remainder):
        _mcp_config(tmp_path, "acme-tools")
        f = self._mcp(tmp_path, tool)
        assert f["mcp_server"] == "acme_tools" and f["mcp_tool"] == remainder and f["mcp_tool_exact"] is False

    def test_mcp_no_configured_server_records_only_the_id(self, tmp_path):
        _mcp_config(tmp_path, "other-server")
        for tool in ("mcp_acme_tools_get_page", "mcp_acme_tools_get_page_2", "mcp_acme_tools_" + "x" * 49):
            f = self._mcp(tmp_path, tool)
            assert f["mcp_id"] == tool
            assert not {"mcp_server", "mcp_tool", "mcp_tool_exact"} & set(f)

    def test_mcp_servers_come_from_home_config_too_and_fail_soft(self, tmp_path):
        home = tmp_path / "home" / ".kiro" / "settings"
        home.mkdir(parents=True)
        (home / "mcp.json").write_text(json.dumps({"mcpServers": {"my-docs-server": {}}}))
        assert kiro.configured_mcp_servers(None) == {"my_docs_server"}
        bad = tmp_path / "ws" / ".kiro" / "settings"
        bad.mkdir(parents=True)
        for text in ("not json", "[1]", '{"mcpServers": []}', '{"mcpServers": {"": {}}}'):
            (bad / "mcp.json").write_text(text)
            assert kiro.configured_mcp_servers(str(tmp_path / "ws")) == {"my_docs_server"}
        assert kiro.configured_mcp_servers(123) == {"my_docs_server"}

    def test_mcp_config_is_cached_until_it_changes(self, tmp_path, monkeypatch):
        _mcp_config(tmp_path, "one-server")
        assert kiro.configured_mcp_servers(str(tmp_path)) == {"one_server"}
        opens = []
        real_open = open
        with monkeypatch.context() as m:  # scope the patch; the autouse temp HOME must stay in place
            m.setattr("builtins.open", lambda *a, **k: (opens.append(a[0]), real_open(*a, **k))[1])
            assert kiro.configured_mcp_servers(str(tmp_path)) == {"one_server"}
            assert not [o for o in opens if str(o).endswith("mcp.json")]
        _mcp_config(tmp_path, "one-server", "two-server")
        assert kiro.configured_mcp_servers(str(tmp_path)) == {"one_server", "two_server"}

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
        etype, f = kiro.hook_to_event({"hook_event_name": "SomeFutureTrigger", "cwd": "/w"})
        assert etype == "hook_event" and f["hook_event"] == "SomeFutureTrigger"

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
        monkeypatch.setattr(kiro, "_read_stdin", lambda: time.sleep(10))
        t0 = time.time()
        rc, out = self._run(monkeypatch, capsys, '{"session_id": "s"}', ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == "" and time.time() - t0 < 5

    @posix_only
    def test_watchdog_is_cancelled_before_the_trace_is_written(self, tmp_path, monkeypatch, capsys):
        """A SIGALRM inside the emit could tear the last line and break the chain, so the alarm
        must be gone once stdin has been read."""
        import signal
        remaining = []
        monkeypatch.setattr(kiro, "record", lambda *a, **k: remaining.append(signal.alarm(0)))
        rc, _ = self._run(monkeypatch, capsys, '{"session_id": "s"}', ["--dir", str(tmp_path)])
        assert rc == 0 and remaining == [0]

    @posix_only
    def test_watchdog_is_armed_during_the_stdin_read(self, tmp_path, monkeypatch, capsys):
        import signal
        seen = []
        monkeypatch.setattr(kiro, "_read_stdin", lambda: (seen.append(signal.alarm(0)), "{}")[1])
        self._run(monkeypatch, capsys, "{}", ["--dir", str(tmp_path)])
        assert seen and seen[0] > 0

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
        assert rec["prompt_present"] is True and rec["prompt_source"] == "env" and rec["prompt_chars"] > 0
        assert "private plan" not in raw.read_text() and AWS_KEY not in raw.read_text()

    def test_prompt_content_only_redacted_and_opt_in(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, "{}", ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")],
                  USER_PROMPT=f"plan {AWS_KEY}", **{kiro.RAW_PROMPTS_ENV: "1"})
        (rec,) = _read(raw)
        assert rec["prompt_redacted"].startswith("plan ") and AWS_KEY not in raw.read_text()

    def test_no_prompt_env(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, "{}", ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")])
        assert _read(raw)[0]["prompt_present"] is False

    def test_raw_capture_keeps_session_values_as_received(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, json.dumps({"hook_event_name": "Stop", "session_id": "child-9"}),
                  ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")], KIRO_SESSION_ID="root-1")
        (rec,) = _read(raw)
        assert rec["KIRO_SESSION_ID"] == "root-1" and rec["payload"]["session_id"] == "child-9"
        assert "root_session_id" not in rec

    def test_payload_prompt_counts_as_present(self, tmp_path, monkeypatch):
        raw = tmp_path / "raw.jsonl"
        self._run(monkeypatch, json.dumps({"hook_event_name": "UserPromptSubmit", "prompt": "refactor the parser"}),
                  ["--raw", "--raw-file", str(raw), "--dir", str(tmp_path / "t")])
        (rec,) = _read(raw)
        assert rec["prompt_present"] is True and rec["prompt_source"] == "payload" and rec["prompt_chars"] == len("refactor the parser")
        assert "refactor" not in raw.read_text()

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
            assert h["name"].startswith("spiritwriter-")
            assert h["enabled"] is (h["trigger"] not in ("PreToolUse", "PostToolUse"))  # tool hooks are off by default
            assert h["action"]["type"] == "command"
            assert h["action"]["command"] == f"/opt/bin/spiritwriter-kiro-hook --event {h['trigger']} >/dev/null 2>&1 || true"
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
            assert h["action"]["command"].endswith(" >/dev/null 2>&1 || true")
            assert "--event" in argv and h["trigger"] in argv or "-m" in argv

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


# === Hook command guard, lineage, extra triggers =========================


class TestCommandGuard:
    def test_every_generated_command_ends_with_or_true(self):
        for raw in (False, True):
            hooks = kiro.build_hooks("/opt/bin/spiritwriter-kiro-hook", raw=raw, triggers=kiro.INSTALL_TRIGGERS + kiro.EXTRA_TRIGGERS)
            assert len(hooks) == len(kiro.INSTALL_TRIGGERS) + len(kiro.EXTRA_TRIGGERS)
            for h in hooks:
                assert h["action"]["command"].endswith(" >/dev/null 2>&1 || true")
        assert kiro.build_hooks()[0]["action"]["command"].endswith("|| true")  # default executable too

    def test_broken_interpreter_exits_zero_and_prints_nothing(self, tmp_path):
        cmd = kiro.hook_command("/nonexistent/python -m spiritwriter.agents.kiro", "Stop")
        r = subprocess.run(cmd, shell=True, input="{}", text=True, capture_output=True, timeout=60)
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
        r = subprocess.run(kiro.hook_command(shlex_quote(sys.executable) + " -c 'import no_such_module_xyz'", "Stop"),
                           shell=True, text=True, capture_output=True, timeout=60)
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""

    def _old_form_file(self, tmp_path, extra=()):
        hooks = tmp_path / ".kiro" / "hooks"
        hooks.mkdir(parents=True)
        old = [{"name": "spiritwriter-" + t, "description": "old", "trigger": t,
                "action": {"type": "command", "command": f"/opt/bin/spiritwriter-kiro-hook --event {t}"}, "timeout": 10, "enabled": True}
               for t in kiro.INSTALL_TRIGGERS]
        doc = {"version": "v1", "hooks": old + list(extra)}
        (hooks / "spiritwriter.json").write_text(json.dumps(doc))
        return hooks / "spiritwriter.json"

    def test_upgrade_replaces_old_form_hooks_without_duplicates(self, tmp_path):
        path = self._old_form_file(tmp_path)
        rc, out = _install(tmp_path)
        assert rc == 0 and "updated" in out
        hooks = json.loads(path.read_text())["hooks"]
        assert [h["name"] for h in hooks] == ["spiritwriter-" + t for t in kiro.INSTALL_TRIGGERS]
        assert all(h["action"]["command"].endswith("|| true") for h in hooks)
        assert "already up to date" in _install(tmp_path)[1]

    def test_uninstall_removes_old_form_hooks_and_keeps_foreign_ones(self, tmp_path):
        mine = {"name": "user-hook", "trigger": "Stop", "action": {"type": "command", "command": "echo hi"}}
        path = self._old_form_file(tmp_path, extra=[mine])
        assert _install(tmp_path, "--uninstall")[0] == 0
        assert json.loads(path.read_text())["hooks"] == [mine]
        only_ours = tmp_path / "other"
        only_ours.mkdir()
        path2 = self._old_form_file(only_ours)
        assert _install(only_ours, "--uninstall")[0] == 0 and not path2.exists()


def shlex_quote(text):
    import shlex
    return shlex.quote(text)


class TestLineage:
    def test_no_root_env_means_main_keyed_by_payload(self):
        _, f = kiro.hook_to_event({"hook_event_name": "Stop", "session_id": "s1"}, env={})
        assert f["agent_id"] == kiro.MAIN_AGENT and "root_session_id" not in f
        assert kiro.lineage({"session_id": "s1"}, {}) == ("s1", kiro.MAIN_AGENT)

    def test_same_id_is_main(self):
        _, f = kiro.hook_to_event({"hook_event_name": "Stop", "session_id": "s1"}, env={"KIRO_SESSION_ID": "s1"})
        assert f["agent_id"] == kiro.MAIN_AGENT and "root_session_id" not in f
        assert kiro.lineage({"session_id": "s1"}, {"KIRO_SESSION_ID": "s1"})[0] == "s1"

    def test_child_conversation_keys_by_root_with_agent_id(self):
        env = {"KIRO_SESSION_ID": "root-1"}
        assert kiro.lineage({"session_id": "child-9"}, env) == ("root-1", "child-9")
        _, f = kiro.hook_to_event({"hook_event_name": "PreToolUse", "session_id": "child-9", "tool_name": "readFile"}, env=env)
        assert f["agent_id"] == "child-9" and "root_session_id" not in f

    def test_missing_payload_session_falls_back_to_root(self):
        assert kiro.lineage({}, {"KIRO_SESSION_ID": "root-1"})[:2] == ("root-1", kiro.MAIN_AGENT)

    @posix_only
    def test_root_and_child_events_share_one_chain(self, tmp_path):
        env = {"KIRO_SESSION_ID": "root-1"}
        kiro.record({"hook_event_name": "SessionStart", "session_id": "root-1"}, trace_dir=str(tmp_path), env=env)
        kiro.record({"hook_event_name": "PreToolUse", "session_id": "child-9", "tool_name": "readFile"}, trace_dir=str(tmp_path), env=env)
        kiro.record({"hook_event_name": "Stop", "session_id": "root-1"}, trace_dir=str(tmp_path), env=env)
        (trace,) = list(tmp_path.glob("*.jsonl"))
        assert trace.name == "root-1.jsonl"
        events = _read(trace)
        assert [e["run_id"] for e in events] == ["root-1"] * 3
        assert [e["agent_id"] for e in events] == [kiro.MAIN_AGENT, "child-9", kiro.MAIN_AGENT]
        assert all("root_session_id" not in e for e in events) and verify_chain(events)

    def test_env_is_read_from_the_process_environment_by_default(self, monkeypatch):
        monkeypatch.setenv("KIRO_SESSION_ID", "root-2")
        assert kiro.lineage({"session_id": "c"})[:2] == ("root-2", "c")


class TestExtraTriggers:
    def test_task_events(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "PreTaskExec", "spec_name": "my-feature", "task_name": "1.2 Add parser"})
        assert etype == "spec_task_started" and f["tool_kind"] == "spec"
        assert f["spec_name"] == "my-feature" and f["task_name"] == "1.2 Add parser" and "task_success" not in f
        etype, f = kiro.hook_to_event({"hook_event_name": "PostTaskExec", "spec_name": "s", "task_name": "t", "task_success": False})
        assert etype == "spec_task_completed" and f["task_success"] is False and f["hook_event"] == "PostTaskExec"

    @pytest.mark.parametrize("trigger,etype", [("PostFileCreate", "file_created"), ("PostFileSave", "file_saved"), ("PostFileDelete", "file_deleted")])
    def test_file_events(self, trigger, etype):
        got, f = kiro.hook_to_event({"hook_event_name": trigger, "file_path": "/w/demo/src/a.py"})
        assert got == etype and f["hook_event"] == trigger and f["file_path"].endswith("a.py")

    def test_manual(self):
        etype, f = kiro.hook_to_event({"hook_event_name": "Manual", "cwd": "/w/demo-app"})
        assert etype == "manual_trigger" and f["hook_event"] == "Manual" and f["project"] == "demo-app"

    def test_stop_user_decision_and_session_end_reason(self):
        _, f = kiro.hook_to_event({"hook_event_name": "Stop", "user_decision": "accepted"})
        assert f["user_decision"] == "accepted"
        _, f = kiro.hook_to_event({"hook_event_name": "Stop"})
        assert "user_decision" not in f
        _, f = kiro.hook_to_event({"hook_event_name": "SessionEnd", "reason": "user_exit"})
        assert f["reason"] == "user_exit"

    def test_installer_accepts_extra_triggers_but_does_not_install_them_by_default(self, tmp_path):
        _install(tmp_path)
        path = tmp_path / ".kiro" / "hooks" / "spiritwriter.json"
        assert not {h["trigger"] for h in json.loads(path.read_text())["hooks"]} & set(kiro.EXTRA_TRIGGERS)
        assert _install(tmp_path, "--triggers", ",".join(kiro.INSTALL_TRIGGERS + kiro.EXTRA_TRIGGERS))[0] == 0
        assert [h["trigger"] for h in json.loads(path.read_text())["hooks"]][-6:] == list(kiro.EXTRA_TRIGGERS)
        assert all("matcher" not in h for h in json.loads(path.read_text())["hooks"] if h["trigger"] in kiro.EXTRA_TRIGGERS)

    @posix_only
    def test_extra_triggers_record_a_valid_chain(self, tmp_path):
        for p in ({"hook_event_name": "PreTaskExec", "session_id": "x", "spec_name": "s", "task_name": "t"},
                  {"hook_event_name": "PostFileSave", "session_id": "x", "file_path": "a.py"},
                  {"hook_event_name": "Manual", "session_id": "x"}):
            kiro.record(p, trace_dir=str(tmp_path))
        events = _read(tmp_path / "x.jsonl")
        assert [e["type"] for e in events] == ["spec_task_started", "file_saved", "manual_trigger"] and verify_chain(events)


class TestToolResponse:
    def _post(self, response, responses=True, **extra):
        return kiro.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "executeBash", "tool_response": response, **extra},
                                  env={}, responses=responses)[1]

    def test_response_is_recorded_and_ok_derived(self):
        f = self._post({"success": True, "stdout": "done"})
        assert f["ok"] is True and "ok_unknown" not in f and f["tool_response"] == {"success": True, "stdout": "done"}

    def test_body_is_off_by_default_but_ok_is_still_derived(self):
        f = self._post({"success": True, "stdout": "private output"}, responses=False)
        assert f["ok"] is True and "tool_response" not in f and "private output" not in json.dumps(f)
        assert self._post({"error": "boom"}, responses=False)["ok"] is False

    def test_env_opt_in_keeps_the_body(self):
        f = kiro.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "x", "tool_response": {"success": True}},
                               env={kiro.RESPONSES_ENV: "1"})[1]
        assert f["tool_response"] == {"success": True}

    def test_hook_flag_and_installer_option(self, tmp_path):
        assert kiro.hook_command("/x/hook", "PostToolUse", responses=True).startswith("/x/hook --event PostToolUse --responses")
        _install(tmp_path, "--with-responses")
        doc = json.loads((tmp_path / ".kiro" / "hooks" / "spiritwriter.json").read_text())
        assert all(" --responses" in h["action"]["command"] for h in doc["hooks"])
        _install(tmp_path)
        doc = json.loads((tmp_path / ".kiro" / "hooks" / "spiritwriter.json").read_text())
        assert not any("--responses" in h["action"]["command"] for h in doc["hooks"])

    @posix_only
    def test_recorded_frame_has_body_only_with_the_flag(self, tmp_path):
        p = {"hook_event_name": "PostToolUse", "session_id": "r", "tool_name": "x", "tool_response": {"success": True, "stdout": "hi"}}
        kiro.record(p, trace_dir=str(tmp_path), env={})
        kiro.record(p, trace_dir=str(tmp_path), env={}, responses=True)
        a, b = _read(tmp_path / "r.jsonl")
        assert "tool_response" not in a and b["tool_response"]["stdout"] == "hi"

    @pytest.mark.parametrize("response,ok", [
        ({"exit_code": 0}, True), ({"exit_code": 1}, False), ({"exitCode": 2}, False),
        ({"status": "error"}, False), ({"status": "success"}, True),
        ({"isError": True}, False), ({"error": "boom"}, False), ("plain text", True),
    ])
    def test_ok_from_response_shape(self, response, ok):
        f = self._post(response)
        assert f["ok"] is ok and "ok_unknown" not in f

    def test_no_response_stays_unknown_without_tool_response(self):
        _, f = kiro.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "readFile"})
        assert f["ok_unknown"] is True and "ok" not in f and "tool_response" not in f

    def test_response_is_redacted_and_capped(self):
        f = self._post({"stdout": f"key {AWS_KEY}", "password": "hunter2"})
        assert AWS_KEY not in json.dumps(f) and "hunter2" not in json.dumps(f)
        big = self._post("x" * 50_000)["tool_response"]
        assert len(big) < kiro.RESPONSE_MAX + 100 and "TRUNCATED" in big
        big = self._post({"rows": ["y" * 100] * 500})["tool_response"]
        assert isinstance(big, str) and len(big) < kiro.RESPONSE_MAX + 100 and "TRUNCATED" in big


# === Synthesized call ids ================================================


def _tool(trace_dir, kind, tool, session="p", agent_session=None, **extra):
    payload = {"hook_event_name": kind, "session_id": agent_session or session, "tool_name": tool, "tool_input": {"path": "a"}, **extra}
    return kiro.record(payload, trace_dir=str(trace_dir), env={"KIRO_SESSION_ID": session})


@posix_only
class TestCallIds:
    def test_post_gets_the_id_of_its_pre(self, tmp_path):
        pre = _tool(tmp_path, "PreToolUse", "fsRead")
        post = _tool(tmp_path, "PostToolUse", "fsRead")
        assert pre["tool_use_id"] == post["tool_use_id"] and len(pre["tool_use_id"]) == 16
        assert pre["tool_use_id_synthesized"] is True and post["tool_use_id_synthesized"] is True
        assert not list(tmp_path.glob("*.pending.json"))  # nothing left pending

    def test_two_different_tools_interleaved(self, tmp_path):
        a = _tool(tmp_path, "PreToolUse", "fsRead")
        b = _tool(tmp_path, "PreToolUse", "executeBash")
        pb = _tool(tmp_path, "PostToolUse", "executeBash")
        pa = _tool(tmp_path, "PostToolUse", "fsRead")
        assert pb["tool_use_id"] == b["tool_use_id"] and pa["tool_use_id"] == a["tool_use_id"]
        assert a["tool_use_id"] != b["tool_use_id"]

    def test_same_tool_pairs_in_order(self, tmp_path):
        p1 = _tool(tmp_path, "PreToolUse", "fsRead")
        p2 = _tool(tmp_path, "PreToolUse", "fsRead")
        r1 = _tool(tmp_path, "PostToolUse", "fsRead")
        r2 = _tool(tmp_path, "PostToolUse", "fsRead")
        assert (r1["tool_use_id"], r2["tool_use_id"]) == (p1["tool_use_id"], p2["tool_use_id"])
        assert p1["tool_use_id"] != p2["tool_use_id"]

    def test_post_without_a_pending_pre_mints_a_marked_id(self, tmp_path):
        post = _tool(tmp_path, "PostToolUse", "fsRead")
        assert post["tool_use_id_synthesized"] is True and len(post["tool_use_id"]) == 16

    def test_orphaned_pre_does_not_mispair_after_the_ttl(self, tmp_path, monkeypatch):
        clock = [1000.0]
        monkeypatch.setattr(kiro, "_now", lambda: clock[0])
        orphan = _tool(tmp_path, "PreToolUse", "fsRead")
        clock[0] += kiro.PENDING_TTL_SECONDS + 1
        post = _tool(tmp_path, "PostToolUse", "fsRead")
        assert post["tool_use_id"] != orphan["tool_use_id"]
        # within the TTL it would have paired
        clock[0] = 5000.0
        pre = _tool(tmp_path, "PreToolUse", "fsRead")
        clock[0] += kiro.PENDING_TTL_SECONDS - 1
        assert _tool(tmp_path, "PostToolUse", "fsRead")["tool_use_id"] == pre["tool_use_id"]

    def test_purge_drops_stale_entries_from_the_file(self, tmp_path):
        trace = str(tmp_path / "s.jsonl")
        f1, f2 = {"tool_name": "t"}, {"tool_name": "t"}
        kiro.assign_tool_use_id(trace, "tool_call", "main", f1, now=1000.0)
        kiro.assign_tool_use_id(trace, "tool_call", "main", f2, now=1000.0 + kiro.PENDING_TTL_SECONDS + 5)
        state = json.loads(Path(kiro._pending_path(trace)).read_text())
        assert [e["id"] for e in state["main"]] == [f2["tool_use_id"]]

    def test_state_is_bounded(self, tmp_path):
        trace = str(tmp_path / "s.jsonl")
        for i in range(kiro.PENDING_MAX + 25):
            kiro.assign_tool_use_id(trace, "tool_call", "main", {"tool_name": "t"}, now=1000.0 + i)
        state = json.loads(Path(kiro._pending_path(trace)).read_text())
        assert sum(len(v) for v in state.values()) == kiro.PENDING_MAX

    def test_agents_are_paired_separately(self, tmp_path):
        main_pre = _tool(tmp_path, "PreToolUse", "fsRead")
        child_pre = _tool(tmp_path, "PreToolUse", "fsRead", agent_session="child-1")
        child_post = _tool(tmp_path, "PostToolUse", "fsRead", agent_session="child-1")
        main_post = _tool(tmp_path, "PostToolUse", "fsRead")
        assert child_post["tool_use_id"] == child_pre["tool_use_id"] and main_post["tool_use_id"] == main_pre["tool_use_id"]

    def test_a_real_id_is_used_and_skips_the_state(self, tmp_path):
        pre = _tool(tmp_path, "PreToolUse", "fsRead", tool_use_id="real-1")
        assert pre["tool_use_id"] == "real-1" and "tool_use_id_synthesized" not in pre
        assert not list(tmp_path.glob("*.pending.json*"))

    def test_corrupt_state_is_ignored(self, tmp_path):
        _tool(tmp_path, "PreToolUse", "fsRead")
        (state,) = tmp_path.glob("*.pending.json")
        state.write_text("{not json")
        post = _tool(tmp_path, "PostToolUse", "fsRead")
        assert post["tool_use_id_synthesized"] is True

    def test_concurrent_hooks_pair_correctly_and_keep_the_chain(self, tmp_path):
        import threading
        results, errors = {}, []

        def work(i):
            try:
                tool = f"tool{i % 4}"  # four tools, several threads per tool
                results[i] = (_tool(tmp_path, "PreToolUse", tool), _tool(tmp_path, "PostToolUse", tool))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        threads = [threading.Thread(target=work, args=(i,)) for i in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors
        events = _read(tmp_path / "p.jsonl")
        assert len(events) == 32 and verify_chain(events)
        pre_ids = [e["tool_use_id"] for e in events if e["type"] == "tool_call"]
        post_ids = [e["tool_use_id"] for e in events if e["type"] == "tool_result"]
        assert len(set(pre_ids)) == 16 and sorted(pre_ids) == sorted(post_ids)  # every post paired with exactly one pre
        # per tool, post ids are consumed in the order the pres were queued
        for tool in ("tool0", "tool1", "tool2", "tool3"):
            pres = [e["tool_use_id"] for e in events if e["type"] == "tool_call" and e["tool_name"] == tool]
            posts = [e["tool_use_id"] for e in events if e["type"] == "tool_result" and e["tool_name"] == tool]
            assert len(pres) == len(posts) == 4 and set(pres) == set(posts)
        assert not list(tmp_path.glob("*.pending.json"))

    def test_the_state_lock_is_exclusive(self, tmp_path):
        import fcntl as _f
        trace = str(tmp_path / "s.jsonl")
        with kiro._state_lock(trace):
            fd = os.open(kiro._pending_path(trace) + ".lock", os.O_WRONLY)
            try:
                with pytest.raises(BlockingIOError):
                    _f.flock(fd, _f.LOCK_EX | _f.LOCK_NB)
            finally:
                os.close(fd)


# === on / off / status ====================================================


class TestToggle:
    def _file(self, ws):
        return Path(ws) / ".kiro" / "hooks" / "spiritwriter.json"

    def _enabled(self, ws):
        return {h["trigger"]: h["enabled"] for h in json.loads(self._file(ws).read_text())["hooks"]}

    def _toggle(self, action, *args):
        out = io.StringIO()
        return kiro.run_toggle(action, list(args), out=out), out.getvalue()

    def test_install_default_disables_only_the_tool_hooks(self, tmp_path):
        _install(tmp_path)
        assert self._enabled(tmp_path) == {"SessionStart": True, "UserPromptSubmit": True, "PreToolUse": False,
                                           "PostToolUse": False, "Stop": True, "SessionEnd": True}

    def test_on_and_off_flip_the_tool_hooks_and_are_idempotent(self, tmp_path):
        _install(tmp_path)
        rc, out = self._toggle("on", "--workspace", str(tmp_path))
        assert rc == 0 and "updated" in out
        assert self._enabled(tmp_path)["PreToolUse"] is True and self._enabled(tmp_path)["PostToolUse"] is True
        first = self._file(tmp_path).read_text()
        mtime = os.stat(self._file(tmp_path)).st_mtime_ns
        rc, out = self._toggle("on", "--workspace", str(tmp_path))
        assert rc == 0 and "already on" in out and self._file(tmp_path).read_text() == first
        assert os.stat(self._file(tmp_path)).st_mtime_ns == mtime
        rc, out = self._toggle("off", "--workspace", str(tmp_path))
        assert rc == 0 and self._enabled(tmp_path)["PreToolUse"] is False
        assert "already off" in self._toggle("off", "--workspace", str(tmp_path))[1]
        assert self._enabled(tmp_path)["Stop"] is True  # other hooks untouched

    def test_status_reports_the_state(self, tmp_path):
        _install(tmp_path)
        assert "tool tracing: off" in self._toggle("status", "--workspace", str(tmp_path))[1]
        self._toggle("on", "--workspace", str(tmp_path))
        rc, out = self._toggle("status", "--workspace", str(tmp_path))
        assert rc == 0 and "tool tracing: on" in out and "PreToolUse: enabled" in out
        doc = json.loads(self._file(tmp_path).read_text())
        doc["hooks"][2]["enabled"] = False  # PreToolUse only
        self._file(tmp_path).write_text(json.dumps(doc))
        assert "tool tracing: mixed" in self._toggle("status", "--workspace", str(tmp_path))[1]

    def test_never_touches_other_hook_files_or_foreign_hooks(self, tmp_path):
        hooks = tmp_path / ".kiro" / "hooks"
        hooks.mkdir(parents=True)
        other = hooks / "mine.json"
        body = '{"version": "v1", "hooks": [{"name": "mine", "trigger": "PreToolUse", "enabled": false, "action": {"type": "command", "command": "echo"}}]}'
        other.write_text(body)
        _install(tmp_path)
        doc = json.loads(self._file(tmp_path).read_text())
        foreign = {"name": "user-hook", "trigger": "PreToolUse", "enabled": False, "action": {"type": "command", "command": "echo hi"}}
        doc["hooks"].append(foreign)
        self._file(tmp_path).write_text(json.dumps(doc))
        self._toggle("on", "--workspace", str(tmp_path))
        assert other.read_text() == body
        assert foreign in json.loads(self._file(tmp_path).read_text())["hooks"]

    def test_global_and_errors(self, tmp_path):
        assert kiro.run_install(["--global", "--command", "/x/hook"], out=io.StringIO()) == 0
        assert self._toggle("on", "--global")[0] == 0
        doc = json.loads((tmp_path / "home" / ".kiro" / "hooks" / "spiritwriter.json").read_text())
        assert all(h["enabled"] for h in doc["hooks"])
        assert self._toggle("on", "--global", "--workspace", str(tmp_path))[0] == 2
        assert self._toggle("on", "--workspace", str(tmp_path / "nope"))[0] == 2
        assert self._toggle("on", "--workspace", str(tmp_path))[0] == 1  # nothing installed here
        (tmp_path / ".kiro" / "hooks").mkdir(parents=True)
        self._file(tmp_path).write_text("not json")
        assert self._toggle("on", "--workspace", str(tmp_path))[0] == 1 and self._file(tmp_path).read_text() == "not json"

    def test_reinstall_keeps_the_state_set_by_on(self, tmp_path):
        _install(tmp_path)
        self._toggle("on", "--workspace", str(tmp_path))
        _install(tmp_path, "--raw")
        assert self._enabled(tmp_path)["PreToolUse"] is True

    def test_main_dispatches(self, tmp_path, capsys):
        _install(tmp_path)
        assert kiro.main(["status", "--workspace", str(tmp_path)]) == 0
        assert "tool tracing: off" in capsys.readouterr().out
