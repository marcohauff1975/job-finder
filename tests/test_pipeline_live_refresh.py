"""
Unit tests for the Pipeline tab refreshing itself while a PR is moving.

Auto-refresh has been removed from this tab twice (4c32f0a: "it was still
interrupting crew calls"). Every st.tabs() panel's code runs on every script
rerun whether or not it's the visible tab, so a plain timer kept firing while a
*different* tab was mid-crew-call and made Streamlit cancel that run before it
could save. These pin the two rules that keep the fragment from repeating it:
it only exists while something is in flight, and nothing here ever fires a full
st.rerun() to stop it.

Network-free: get_latest_pr_flow is stubbed everywhere.
"""

import ast
import inspect
import textwrap

import pytest

import req2prod.admin_ui as m


def _stage(state):
    return {"kind": "code_review", "label": "Code Review", "agent": "x",
            "state": state, "summary": "", "url": "u"}


class TestInFlight:
    def test_a_running_stage_is_in_flight(self):
        assert m._flow_is_in_flight([_stage("ok"), _stage("running")]) is True

    def test_all_finished_is_not(self):
        assert m._flow_is_in_flight([_stage("ok"), _stage("ok")]) is False

    def test_no_stages_is_not(self):
        assert m._flow_is_in_flight([]) is False

    def test_a_failed_stage_is_not_in_flight(self):
        """Failed is finished. Polling a dead PR forever is the thing the
        mount condition exists to avoid."""
        assert m._flow_is_in_flight([_stage("failed")]) is False


class TestTheTimerOnlyExistsWhileSomethingMoves:
    @pytest.fixture
    def spy(self, monkeypatch):
        calls = {"draws": 0, "fragments": []}
        monkeypatch.setattr(m, "_draw_pr_flow", lambda: calls.__setitem__("draws", calls["draws"] + 1))

        def fake_fragment(**kwargs):
            calls["fragments"].append(kwargs.get("run_every"))
            return lambda fn: fn

        monkeypatch.setattr(m.st, "fragment", fake_fragment)
        return calls

    def test_no_fragment_is_mounted_when_nothing_is_running(self, spy, monkeypatch):
        monkeypatch.setattr(m, "get_latest_pr_flow", lambda: (None, [_stage("ok")], None))

        m._render_pr_flow_live()

        assert spy["fragments"] == [], "an idle tab should carry no timer at all"
        assert spy["draws"] == 1

    def test_a_fragment_is_mounted_while_a_pr_moves(self, spy, monkeypatch):
        monkeypatch.setattr(m, "get_latest_pr_flow", lambda: (None, [_stage("running")], None))

        m._render_pr_flow_live()

        assert spy["fragments"] == [m._FLOW_POLL_SECONDS]

    def test_the_flow_is_drawn_exactly_once_either_way(self, spy, monkeypatch):
        """It drew twice at first - once directly, once inside the fragment."""
        monkeypatch.setattr(m, "get_latest_pr_flow", lambda: (None, [_stage("running")], None))

        m._render_pr_flow_live()

        assert spy["draws"] == 1

    def test_an_unreachable_api_does_not_mount_a_timer(self, spy, monkeypatch):
        def boom():
            raise RuntimeError("github is down")

        monkeypatch.setattr(m, "get_latest_pr_flow", boom)

        m._render_pr_flow_live()

        assert spy["fragments"] == []
        assert spy["draws"] == 1, "still renders, so the error message shows"


class TestItNeverFiresAFullRerun:
    """The one rule that matters. Re-decorating the fragment to stop the timer
    would need a full st.rerun(), and a full rerun mid-crew-call is exactly
    what broke this twice. The timer is left to expire on the next
    interaction instead - free, because get_latest_pr_flow is cached."""

    def test_no_rerun_in_the_live_flow_path(self):
        """Reads the parsed code, not the file text: these docstrings say
        "rerun" repeatedly precisely to explain that they never call it, and a
        substring search fails on its own prose."""
        for fn in (m._render_pr_flow_live, m._draw_pr_flow, m._flow_is_in_flight):
            tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
            called = {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }
            assert "rerun" not in called, f"{fn.__name__} calls st.rerun()"
