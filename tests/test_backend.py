"""
Unit tests for sdlc/backend.py's dual-backend dispatch. subprocess.run is
mocked everywhere - no test in this file ever spawns a real `claude`
process, so running the suite never costs a cent of subscription usage or
touches the filesystem outside what a test explicitly sets up.
"""

import json
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from sdlc import backend


class FakeVerdict(BaseModel):
    safe_to_merge: bool
    reasoning: str


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
