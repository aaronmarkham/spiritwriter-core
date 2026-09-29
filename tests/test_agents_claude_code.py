"""Tests for the Claude Code hook recorder (spiritwriter.agents.claude_code).

The fixture ``fixtures/claude_code/parallel_subagents.jsonl`` holds real
hook payloads captured from Claude Code 2.1 while the main agent ran two
subagents in parallel (long values truncated at capture; paths
anonymized). If Claude Code changes its payload format, these tests are
where it shows up.
"""

from __future__ import annotations

import io
import json
import os
import stat
from pathlib import Path

import pytest

from spiritwriter.agents import claude_code as cc
from spiritwriter.fabric.emitter import fcntl, verify_chain

FIXTURE = Path(__file__).parent / "fixtures" / "claude_code" / "parallel_subagents.jsonl"
posix_only = pytest.mark.skipif(fcntl is None, reason="recorder uses concurrent mode (POSIX)")


def _payloads():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _record_all(tmp_path):
    for p in _payloads():
        cc.record(p, trace_dir=str(tmp_path))
    (trace,) = list(tmp_path.glob("*.jsonl"))
    return [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]


# === Mapping =============================================================


class TestHookToEvent:
    def test_main_agent_tool_call(self):
        etype, f = cc.hook_to_event(
            {"hook_event_name": "PreToolUse", "session_id": "s", "tool_name": "Bash",
             "tool_use_id": "t1", "tool_input": {"command": "ls -la /secret", "description": "List files"}}
        )
        assert etype == "tool_call"
        assert f["agent_id"] == cc.MAIN_AGENT
        assert f["tool_use_id"] == "t1"
        assert f["args_summary"] == "List files"
        assert len(f["args_sha256"]) == 64
        assert "ls -la" not in json.dumps(f)

    def test_subagent_identity_passes_through(self):
        _, f = cc.hook_to_event({"hook_event_name": "PreToolUse", "agent_id": "a1", "agent_type": "general-purpose",
                                 "tool_name": "Read", "tool_input": {"file_path": "/x/y.py"}})
        assert f["agent_id"] == "a1" and f["agent_type"] == "general-purpose"
        assert f["args_summary"] == "/x/y.py"

    def test_spawn_records_child_link_not_prompt(self):
        etype, f = cc.hook_to_event({
            "hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_use_id": "t9",
            "tool_input": {"description": "Probe", "prompt": "do secret things", "subagent_type": "general-purpose"},
            "tool_response": {"agentId": "child-1", "resolvedModel": "claude-sonnet-5-5", "prompt": "do secret things"},
        })
        assert etype == "spawn_with_shards"
        assert f["child_agent_id"] == "child-1"
        assert f["task"] == "Probe" and f["model"] == "claude-sonnet-5-5"
        assert f["task_prompt_chars"] == len("do secret things")
        assert "secret things" not in json.dumps(f)

    def test_spawn_without_agent_id_is_plain_result(self):
        etype, _ = cc.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "Agent", "tool_response": {}})
        assert etype == "tool_result"

    def test_failed_tool_result(self):
        _, f = cc.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                 "tool_response": {"interrupted": True}})
        assert f["ok"] is False

    def test_prompt_and_stop_record_only_receipts(self):
        _, f = cc.hook_to_event({"hook_event_name": "UserPromptSubmit", "prompt": "my password is hunter2"})
        assert f["prompt_chars"] == 22 and "hunter2" not in json.dumps(f)
        etype, f = cc.hook_to_event({"hook_event_name": "SubagentStop", "agent_id": "a", "last_assistant_message": "done"})
        assert etype == "agent_completed" and f["last_message_chars"] == 4

    @pytest.mark.parametrize("hook,etype", [
        ("SubagentStart", "agent_started"), ("SessionStart", "session_started"),
        ("SessionEnd", "session_ended"), ("Stop", "turn_completed"), ("SomethingNew", "hook_event"),
    ])
    def test_event_names(self, hook, etype):
        t, f = cc.hook_to_event({"hook_event_name": hook})
        assert t == etype and f["hook_event"] == hook

    def test_missing_fields_are_omitted_not_null(self):
        _, f = cc.hook_to_event({"hook_event_name": "PreToolUse"})
        assert None not in f.values()


class TestSummaries:
    def test_bash_without_description_keeps_program_only(self):
        assert cc.summarize_tool_input("Bash", {"command": "/usr/bin/curl -H 'Authorization: x' https://a"}) == "curl"

    def test_webfetch_drops_query(self):
        assert cc.summarize_tool_input("WebFetch", {"url": "https://ex.com/p?token=abc#f"}) == "ex.com/p"

    def test_unknown_and_mcp_tools_record_arg_names_only(self):
        s = cc.summarize_tool_input("mcp__srv__send", {"to": "bob", "body": "private"})
        assert s == "(body, to)"

    def test_secret_shapes_scrubbed(self):
        s = cc.summarize_tool_input("Bash", {"description": "use AKIAABCDEFGHIJKLMNOP and ghp_" + "a" * 36})
        assert "AKIA" not in s and "ghp_" not in s
        assert s.count("<REDACTED:") == 2

    def test_summary_capped_single_line(self):
        s = cc.scrub("x\n" * 500)
        assert len(s) <= cc.SUMMARY_MAX and "\n" not in s


# === Recording ===========================================================


@posix_only
class TestRecord:
    def test_fixture_records_one_valid_chain(self, tmp_path):
        events = _record_all(tmp_path)
        assert len(events) == len(_payloads())
        assert verify_chain(events)
        assert {e["run_id"] for e in events} == {_payloads()[0]["session_id"]}

    def test_fixture_lineage(self, tmp_path):
        """Every subagent event is attributable, and every subagent links to the spawn that made it."""
        events = _record_all(tmp_path)
        spawns = {e["child_agent_id"]: e for e in events if e["type"] == "spawn_with_shards"}
        subagents = {e["agent_id"] for e in events if e["agent_id"] != cc.MAIN_AGENT}
        assert len(spawns) == 2 and set(spawns) == subagents
        for child, spawn in spawns.items():
            assert spawn["agent_id"] == cc.MAIN_AGENT
            # The spawn result shares tool_use_id with the parent's call.
            (call,) = [e for e in events if e["type"] == "tool_call" and e.get("tool_use_id") == spawn["tool_use_id"]]
            assert call["agent_id"] == cc.MAIN_AGENT
            kinds = [e["type"] for e in events if e["agent_id"] == child]
            assert kinds[0] == "agent_started" and kinds[-1] == "agent_completed"
            # Each child tool call has exactly one result with the same id.
            for e in events:
                if e["agent_id"] == child and e["type"] == "tool_call":
                    assert sum(1 for r in events if r["type"] == "tool_result" and r.get("tool_use_id") == e["tool_use_id"]) == 1

    def test_no_raw_tool_io_in_trace(self, tmp_path):
        _record_all(tmp_path)
        (trace,) = list(tmp_path.glob("*.jsonl"))
        text = trace.read_text(encoding="utf-8")
        for raw in ("harmless instrumentation probe", "probe-A-step1 && sleep", "[project]", "outputFile"):
            assert raw not in text

    def test_file_permissions(self, tmp_path):
        d = tmp_path / "traces"
        cc.record({"hook_event_name": "Stop", "session_id": "s1"}, trace_dir=str(d))
        assert stat.S_IMODE(os.stat(d / "s1.jsonl").st_mode) == 0o600
        assert stat.S_IMODE(os.stat(d).st_mode) == 0o700

    def test_session_id_cannot_escape_trace_dir(self, tmp_path):
        for sid in ("../../etc/passwd", "..", "/abs/path", "a\\b"):
            p = cc.trace_path(sid, str(tmp_path))
            assert os.path.dirname(p) == str(tmp_path)
            assert not os.path.basename(p).startswith(".")

    def test_env_var_sets_trace_dir(self, tmp_path, monkeypatch):
        monkeypatch.setenv(cc.TRACE_DIR_ENV, str(tmp_path))
        assert cc.trace_path("s") == str(tmp_path / "s.jsonl")


class TestMain:
    def _run(self, monkeypatch, capsys, stdin, argv):
        monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        rc = cc.main(argv)
        return rc, capsys.readouterr()

    @posix_only
    def test_records_and_is_silent(self, tmp_path, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys, json.dumps({"hook_event_name": "Stop", "session_id": "s"}), ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == "" and out.err == ""
        assert (tmp_path / "s.jsonl").exists()

    def test_bad_input_never_fails_the_hook(self, tmp_path, monkeypatch, capsys):
        rc, out = self._run(monkeypatch, capsys, "not json", ["--dir", str(tmp_path)])
        assert rc == 0 and out.out == ""
        assert "spiritwriter-claude-hook" in out.err
