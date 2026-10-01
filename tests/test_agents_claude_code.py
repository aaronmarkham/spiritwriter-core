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


# === Artifacts ===========================================================


class TestArtifacts:
    def test_long_paths_keep_the_filename(self):
        path = "/very/" + "deep/" * 40 + "classify.py"
        s = cc.summarize_tool_input("Edit", {"file_path": path})
        assert s.startswith("…") and s.endswith("/classify.py") and len(s) <= cc.SUMMARY_MAX

    def test_other_summaries_still_keep_the_head(self):
        s = cc.summarize_tool_input("Bash", {"description": "x" * 300})
        assert s.endswith("…") and s.startswith("xxx")

    def test_edit_stats_are_a_line_diff(self):
        st = cc.change_stats("Edit", {"old_string": "a\nb\nc", "new_string": "a\nB\nc\nd"})
        assert st == {"lines_added": 2, "lines_removed": 1, "bytes_written": len("a\nB\nc\nd")}

    def test_multiedit_sums_edits(self):
        st = cc.change_stats("MultiEdit", {"edits": [{"old_string": "x", "new_string": "y"}, {"old_string": "", "new_string": "p\nq"}]})
        assert st["lines_added"] == 3 and st["lines_removed"] == 1

    def test_write_counts_lines_added_only(self):
        st = cc.change_stats("Write", {"content": "one\ntwo\nthree\n"})
        assert st == {"lines_added": 3, "lines_removed": 0, "bytes_written": 14}

    def test_non_write_tools_have_no_stats(self):
        assert cc.change_stats("Read", {"file_path": "/x"}) == {}

    def test_stats_land_on_tool_call_without_content(self):
        _, f = cc.hook_to_event({"hook_event_name": "PreToolUse", "tool_name": "Write",
                                 "tool_input": {"file_path": "/x/y.py", "content": "SECRET_BODY\n"}})
        assert f["lines_added"] == 1 and "SECRET_BODY" not in json.dumps(f)

    @posix_only
    def test_write_result_hashes_the_file(self, tmp_path):
        target = tmp_path / "out.txt"
        target.write_text("hello\n")
        base = {"session_id": "s", "tool_name": "Write", "tool_use_id": "t1", "cwd": str(tmp_path),
                "tool_input": {"file_path": "out.txt", "content": "hello\n"}}
        evt = cc.record({**base, "hook_event_name": "PostToolUse", "tool_response": {}}, trace_dir=str(tmp_path / "tr"))
        import hashlib
        assert evt["artifact_sha256"] == hashlib.sha256(b"hello\n").hexdigest() and evt["artifact_bytes"] == 6

    @posix_only
    def test_no_hash_for_failed_missing_or_huge(self, tmp_path, monkeypatch):
        big = tmp_path / "big.bin"
        big.write_bytes(b"x" * 100)
        monkeypatch.setattr(cc, "ARTIFACT_MAX_BYTES", 10)
        tr = str(tmp_path / "tr")
        for ti, resp in (({"file_path": str(big)}, {}),                        # too large
                         ({"file_path": str(tmp_path / "nope")}, {}),          # missing
                         ({"file_path": str(big)}, {"is_error": True})):       # failed write
            evt = cc.record({"hook_event_name": "PostToolUse", "session_id": "s", "tool_name": "Write",
                             "tool_input": ti, "tool_response": resp}, trace_dir=tr)
            assert "artifact_sha256" not in evt

    def test_reads_are_not_hashed(self, tmp_path):
        (tmp_path / "r.txt").write_text("x")
        _, f = cc.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "Read", "tool_input": {"file_path": str(tmp_path / "r.txt")}})
        assert "artifact_sha256" not in f


class TestProject:
    def test_session_events_carry_project_basename(self):
        for hook in ("SessionStart", "UserPromptSubmit"):
            _, f = cc.hook_to_event({"hook_event_name": hook, "cwd": "/workplace/me/AgentCrossing/src/AgentCrossing/"})
            assert f["project"] == "AgentCrossing"

    def test_no_cwd_no_project_and_tools_never_carry_it(self):
        assert "project" not in cc.hook_to_event({"hook_event_name": "SessionStart"})[1]
        assert "project" not in cc.hook_to_event({"hook_event_name": "PreToolUse", "cwd": "/x/y", "tool_name": "Read"})[1]


class TestSecretLeakFixes:
    def test_bash_inline_env_assignment_not_leaked(self):
        for cmd in ("GH_TOKEN=hunter2secret gh api /user", "DB_PASSWORD='p@ss w0rd' psql -h db", "AWS_SECRET=abc123 aws s3 ls"):
            s = cc.summarize_tool_input("Bash", {"command": cmd})
            assert "=" not in s and "hunter2secret" not in s and "p@ss" not in s and "abc123" not in s
        assert cc.summarize_tool_input("Bash", {"command": "GH_TOKEN=x gh api /user"}) == "gh"

    def test_bash_wrappers_skipped(self):
        assert cc.summarize_tool_input("Bash", {"command": "sudo env FOO=1 /usr/bin/pytest -q"}) == "pytest"
        assert cc.summarize_tool_input("Bash", {"command": "time curl https://x"}) == "curl"

    def test_bash_plain_command_unchanged(self):
        assert cc.summarize_tool_input("Bash", {"command": "/usr/bin/git push"}) == "git"
        assert cc.summarize_tool_input("Bash", {"command": ""}) == ""

    def test_webfetch_drops_userinfo_and_query(self):
        s = cc.summarize_tool_input("WebFetch", {"url": "https://admin:SuperSecret123@internal.example.com/api?token=abc#f"})
        assert "SuperSecret123" not in s and "admin" not in s and "token" not in s
        assert s == "internal.example.com/api"

    def test_webfetch_keeps_port(self):
        assert cc.summarize_tool_input("WebFetch", {"url": "http://host.example:8080/p?q=1"}) == "host.example:8080/p"

    def test_webfetch_malformed_url_strips_userinfo(self):
        assert "secret" not in cc.summarize_tool_input("WebFetch", {"url": "user:secret@nohost/path?x=1"})

    def test_spawn_nondict_tool_input_does_not_crash(self):
        etype, f = cc.hook_to_event({"hook_event_name": "PostToolUse", "tool_name": "Agent",
                                     "tool_input": ["not", "a", "dict"], "tool_response": {"agentId": "kid"}})
        assert etype == "spawn_with_shards" and f["child_agent_id"] == "kid" and f["task"] == ""
