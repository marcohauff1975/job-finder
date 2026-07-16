"""
Unit tests for req2prod/backend.py's dual-backend dispatch. subprocess.run is
mocked everywhere - no test in this file ever spawns a real `claude`
process, so running the suite never costs a cent of subscription usage or
touches the filesystem outside what a test explicitly sets up.
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from req2prod import backend


class FakeVerdict(BaseModel):
    safe_to_merge: bool
    reasoning: str


class TestResolveClaudeBinary:
    def test_prefers_a_plain_path_lookup_when_it_resolves(self, monkeypatch):
        monkeypatch.setattr(backend.shutil, "which", lambda name: "/opt/homebrew/bin/claude")

        assert backend._resolve_claude_binary() == "/opt/homebrew/bin/claude"

    def test_falls_back_to_the_documented_install_location(self, monkeypatch, tmp_path):
        monkeypatch.setattr(backend.shutil, "which", lambda name: None)
        monkeypatch.setattr(backend.Path, "home", lambda: tmp_path)
        fake_claude = tmp_path / ".local" / "bin" / "claude"
        fake_claude.parent.mkdir(parents=True)
        fake_claude.write_text("")

        assert backend._resolve_claude_binary() == str(fake_claude)

    def test_falls_back_to_the_bare_name_when_nothing_is_found(self, monkeypatch, tmp_path):
        monkeypatch.setattr(backend.shutil, "which", lambda name: None)
        monkeypatch.setattr(backend.Path, "home", lambda: tmp_path)

        assert backend._resolve_claude_binary() == "claude"


class TestBashToolInstructions:
    def test_lists_each_tool_with_its_real_description(self):
        text = backend.bash_tool_instructions(["check_pypi_package_version"])

        from req2prod.tool_cli import TOOLS_BY_NAME

        assert "check_pypi_package_version" in text
        assert TOOLS_BY_NAME["check_pypi_package_version"].description in text

    def test_documents_the_exact_command_form(self):
        text = backend.bash_tool_instructions(["check_anthropic_model_id"])

        assert backend.TOOL_CLI_COMMAND in text
        assert "<tool_name>" in text
        assert "--workspace-dir" not in text

    def test_the_documented_interpreter_actually_exists(self):
        """The command an agent is told to run has to resolve on the machine
        it's told to run it on. A bare "python" doesn't exist on macOS - only
        "python3" - and macOS is exactly what the self-hosted runner
        subscription mode uses is, so this used to be a command no agent could
        ever execute (see TOOL_CLI_COMMAND's own comment)."""
        interpreter = backend.TOOL_CLI_COMMAND.split(" -m ")[0]

        assert Path(interpreter).exists(), f"{interpreter} does not exist"
        assert os.access(interpreter, os.X_OK), f"{interpreter} is not executable"

    def test_the_granted_permission_matches_the_documented_command(self):
        """The instruction and the allowlist are the two halves of one
        contract: claude -p matches the command it's asked to run against
        --allowedTools literally, so if these two ever disagree the agent is
        told to run something it is not permitted to run - which fails as an
        approval prompt in a context with nobody to approve it, not as an
        error anyone would recognise."""
        text = backend.bash_tool_instructions(["check_anthropic_model_id"])

        assert backend.TOOL_CLI_ALLOWED_TOOLS == f"Bash({backend.TOOL_CLI_COMMAND} *)"
        assert backend.TOOL_CLI_COMMAND in text

    def test_includes_workspace_dir_flag_when_given(self):
        text = backend.bash_tool_instructions(["File Writer Tool"], workspace_dir="/tmp/build-abc")

        assert "--workspace-dir /tmp/build-abc" in text


def _envelope(result_text: str) -> str:
    return json.dumps({"result": result_text, "session_id": "abc", "total_cost_usd": 0.0})


def _ok(stdout: str, returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class TestRunViaSubscription:
    def _call(self, **overrides):
        kwargs = dict(
            role="Reviewer",
            goal="Review it",
            backstory="You are careful.",
            task_description="Review this diff: {diff}".format(diff="+x"),
            expected_output="A verdict",
            output_model=FakeVerdict,
            model="anthropic/claude-sonnet-5",
            cwd="/tmp",
        )
        kwargs.update(overrides)
        return backend.run_via_subscription(**kwargs)

    def test_parses_bare_json_response(self, monkeypatch):
        raw = json.dumps({"safe_to_merge": True, "reasoning": "looks fine"})
        monkeypatch.setattr(backend.subprocess, "run", lambda *a, **k: _ok(_envelope(raw)))

        result = self._call()

        assert result == FakeVerdict(safe_to_merge=True, reasoning="looks fine")

    def test_parses_json_wrapped_in_a_markdown_fence(self, monkeypatch):
        raw = "Here you go:\n```json\n" + json.dumps(
            {"safe_to_merge": False, "reasoning": "not safe"}
        ) + "\n```\nThat's my answer."
        monkeypatch.setattr(backend.subprocess, "run", lambda *a, **k: _ok(_envelope(raw)))

        result = self._call()

        assert result == FakeVerdict(safe_to_merge=False, reasoning="not safe")

    def test_parses_json_with_surrounding_prose_but_no_fence(self, monkeypatch):
        raw = "Sure, my verdict is: " + json.dumps(
            {"safe_to_merge": True, "reasoning": "ok"}
        ) + " - let me know if you need more."
        monkeypatch.setattr(backend.subprocess, "run", lambda *a, **k: _ok(_envelope(raw)))

        result = self._call()

        assert result == FakeVerdict(safe_to_merge=True, reasoning="ok")

    def test_passes_selected_model_and_tool_scope_to_the_cli(self, monkeypatch):
        # _resolve_claude_binary() is exercised on its own in
        # TestResolveClaudeBinary - fixed here so this test doesn't
        # depend on whatever is or isn't actually installed on whatever
        # machine runs the suite.
        monkeypatch.setattr(backend, "_resolve_claude_binary", lambda: "claude")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _ok(_envelope(json.dumps({"safe_to_merge": True, "reasoning": "ok"})))

        monkeypatch.setattr(backend.subprocess, "run", fake_run)

        self._call(model="anthropic/claude-opus-4-8", allowed_tools="Read,Edit")

        assert captured["cmd"][0] == "claude"
        assert "-p" in captured["cmd"]
        assert "--model" in captured["cmd"]
        assert captured["cmd"][captured["cmd"].index("--model") + 1] == "claude-opus-4-8"
        assert "--allowedTools" in captured["cmd"]
        assert captured["cmd"][captured["cmd"].index("--allowedTools") + 1] == "Read,Edit"
        assert "--tools" not in captured["cmd"]

    def test_disables_all_tools_when_none_requested(self, monkeypatch):
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _ok(_envelope(json.dumps({"safe_to_merge": True, "reasoning": "ok"})))

        monkeypatch.setattr(backend.subprocess, "run", fake_run)

        self._call(allowed_tools="")

        assert "--tools" in captured["cmd"]
        assert captured["cmd"][captured["cmd"].index("--tools") + 1] == ""
        assert "--allowedTools" not in captured["cmd"]

    def test_returns_none_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            backend.subprocess, "run", lambda *a, **k: _ok("", returncode=1, stderr="not logged in")
        )

        assert self._call() is None

    def test_returns_none_on_timeout(self, monkeypatch):
        def raise_timeout(*a, **k):
            raise backend.subprocess.TimeoutExpired(cmd="claude", timeout=600)

        monkeypatch.setattr(backend.subprocess, "run", raise_timeout)

        assert self._call() is None

    def test_returns_none_when_the_claude_binary_is_not_on_path(self, monkeypatch):
        """Regression test for a real observed failure: on a freshly set
        up self-hosted runner, `claude` isn't on the restricted PATH
        GitHub Actions steps run with, and subprocess.run raised an
        unhandled FileNotFoundError that crashed the whole review run
        instead of degrading to None like every other failure mode."""

        def raise_not_found(*a, **k):
            raise FileNotFoundError(2, "No such file or directory", "claude")

        monkeypatch.setattr(backend.subprocess, "run", raise_not_found)

        assert self._call() is None

    def test_returns_none_on_non_json_envelope(self, monkeypatch):
        monkeypatch.setattr(backend.subprocess, "run", lambda *a, **k: _ok("not json at all"))

        assert self._call() is None

    def test_returns_none_when_no_json_object_in_response(self, monkeypatch):
        monkeypatch.setattr(backend.subprocess, "run", lambda *a, **k: _ok(_envelope("no json here")))

        assert self._call() is None

    def test_returns_none_when_response_json_does_not_match_schema(self, monkeypatch):
        raw = json.dumps({"totally": "unrelated"})
        monkeypatch.setattr(backend.subprocess, "run", lambda *a, **k: _ok(_envelope(raw)))

        assert self._call() is None


class TestRunAgentDispatch:
    def _common_kwargs(self, **overrides):
        kwargs = dict(
            agent_key="pr_arbiter",
            task_key="pr_arbiter_task",
            inputs={"diff": "+x", "findings": "none"},
            output_model=FakeVerdict,
            kickoff=lambda: pytest.fail("kickoff should not be called"),
            agents_config={
                "pr_arbiter": {"role": "Arbiter", "goal": "Decide", "backstory": "Careful."}
            },
            tasks_config={
                "pr_arbiter_task": {
                    "description": "diff={diff} findings={findings}",
                    "expected_output": "a verdict",
                }
            },
            model="anthropic/claude-opus-4-8",
            cwd="/tmp",
        )
        kwargs.update(overrides)
        return kwargs

    def test_api_mode_calls_kickoff_and_extracts_pydantic(self, monkeypatch):
        monkeypatch.delenv("AGENT_BACKEND", raising=False)
        crew_result = SimpleNamespace(pydantic=FakeVerdict(safe_to_merge=True, reasoning="ok"))

        result = backend.run_agent(**self._common_kwargs(kickoff=lambda: crew_result))

        assert result == FakeVerdict(safe_to_merge=True, reasoning="ok")

    def test_api_mode_returns_none_when_kickoff_raises(self, monkeypatch):
        monkeypatch.delenv("AGENT_BACKEND", raising=False)

        def raise_exc():
            raise RuntimeError("crew blew up")

        result = backend.run_agent(**self._common_kwargs(kickoff=raise_exc))

        assert result is None

    def test_api_mode_returns_none_when_pydantic_is_none(self, monkeypatch):
        monkeypatch.delenv("AGENT_BACKEND", raising=False)
        crew_result = SimpleNamespace(pydantic=None)

        result = backend.run_agent(**self._common_kwargs(kickoff=lambda: crew_result))

        assert result is None

    def test_subscription_mode_never_touches_kickoff(self, monkeypatch):
        monkeypatch.setenv("AGENT_BACKEND", "subscription")
        raw = json.dumps({"safe_to_merge": True, "reasoning": "ok"})
        monkeypatch.setattr(backend.subprocess, "run", lambda *a, **k: _ok(_envelope(raw)))

        result = backend.run_agent(**self._common_kwargs())

        assert result == FakeVerdict(safe_to_merge=True, reasoning="ok")

    def test_subscription_mode_interpolates_task_inputs_into_the_prompt(self, monkeypatch):
        monkeypatch.setenv("AGENT_BACKEND", "subscription")
        captured = {}

        def fake_run(cmd, **kwargs):
            captured["prompt"] = cmd[cmd.index("-p") + 1]
            raw = json.dumps({"safe_to_merge": True, "reasoning": "ok"})
            return _ok(_envelope(raw))

        monkeypatch.setattr(backend.subprocess, "run", fake_run)

        backend.run_agent(**self._common_kwargs(inputs={"diff": "+eval(x)", "findings": "eval() risk"}))

        assert "diff=+eval(x)" in captured["prompt"]
        assert "findings=eval() risk" in captured["prompt"]
