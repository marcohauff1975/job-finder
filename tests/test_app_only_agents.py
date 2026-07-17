"""
Which agents can never use the Claude subscription in production.

The admin "AI Models" tab lets every agent be given a Subscription model. For
three of them that setting can never take effect in production, and the table
said nothing about it - so it read as a live choice when it silently wasn't.

The reason is not that they lack a code path: product_manager,
software_architect and software_engineer all honour AGENT_BACKEND and have real
run_via_subscription() branches. It is that run_via_subscription() shells out
to `claude -p` **in the same process** - the subscription backend is not a
remote call. These three are only ever reached from req2prod/admin_ui.py, so in
production the process is the Lightsail box, and that box has no Claude login.
req2prod_agent_backend_mode.py states it outright: "Production Streamlit never
has a subscription login available, so it always runs on the API path
regardless of what this toggle is set to."

Every other agent is reached from a CI runner or a manual run, on a machine
where a login can exist.

APP_ONLY_AGENT_KEYS is therefore a claim about the call graph, and a claim goes
stale silently. If a workflow ever runs the engineer, or the app ever runs the
reviewer, the footnote becomes a lie with nothing to catch it. These tests
re-derive the claim from the source.
"""

import re
from pathlib import Path

import pytest

import req2prod.admin_ui as ui
from req2prod.model_registry import (
    AGENT_DISPLAY_NAMES,
    APP_ONLY_AGENT_KEYS,
    load_agent_models,
)

_ROOT = Path(__file__).parent.parent
_WORKFLOWS = _ROOT / ".github" / "workflows"

# The functions that run the three app-only agents.
_APP_ONLY_ENTRY_POINTS = ("challenge_requirement", "build_feature")


class TestTheClaimIsStillTrue:
    def test_the_app_only_agents_are_real_registry_agents(self):
        for key in APP_ONLY_AGENT_KEYS:
            assert key in AGENT_DISPLAY_NAMES
            assert key in load_agent_models()

    @pytest.mark.parametrize("entry_point", _APP_ONLY_ENTRY_POINTS)
    def test_no_workflow_runs_their_entry_point(self, entry_point):
        """The heart of it. These agents are 'app only' because nothing in CI
        reaches them. A workflow that called build_feature would move
        software_engineer onto a runner - where a subscription login can
        exist - and the footnote would be wrong."""
        for wf in _WORKFLOWS.glob("*.yml"):
            assert entry_point not in wf.read_text(), (
                f"{wf.name} now references {entry_point}(). If CI runs it, these "
                "agents are no longer app-only and APP_ONLY_AGENT_KEYS (plus the "
                "table footnote) must be updated."
            )

    def test_the_app_never_dispatches_a_workflow(self):
        """The other way the claim could break: the app handing the work to
        GitHub instead of running it in-process."""
        for module in ("req2prod/admin_ui.py", "req2prod/Req2Prod.py"):
            src = (_ROOT / module).read_text()
            for trigger in ("workflow_dispatch", "gh workflow run", "/dispatches"):
                assert trigger not in src, (
                    f"{module} now triggers a workflow ({trigger!r}). If the agents "
                    "run on a runner, they are no longer app-only."
                )

    def test_agents_reached_only_from_ci_are_not_marked(self):
        """The footnote must not spread to agents whose subscription column is
        real - code_reviewer and friends run on a runner that can be Marco's
        own Mac, where the subscription is exactly the point."""
        for key in ("code_reviewer", "pr_fix_agent", "pr_arbiter", "prod_tester"):
            assert key not in APP_ONLY_AGENT_KEYS


class TestTheTableSaysSo:
    def test_marked_agents_are_daggered_and_others_are_not(self, monkeypatch):
        captured = {"rows": None, "captions": []}
        monkeypatch.setattr(ui.st, "data_editor", lambda rows, **kw: captured.update(rows=rows) or rows)
        monkeypatch.setattr(ui.st, "caption", lambda text, **kw: captured["captions"].append(text))
        monkeypatch.setattr(ui.st, "button", lambda *a, **kw: False)

        ui._render_agent_model_table(["product_manager", "code_reviewer"], "test")

        by_agent = {r["Agent"]: r for r in captured["rows"]}
        assert any(name.endswith(" †") for name in by_agent), "app-only agent not marked"
        assert not any(
            name.endswith(" †") and "reviewer" in name.lower() for name in by_agent
        ), "a CI agent was marked as app-only"

    def test_the_footnote_explains_the_dagger(self, monkeypatch):
        captions = []
        monkeypatch.setattr(ui.st, "data_editor", lambda rows, **kw: rows)
        monkeypatch.setattr(ui.st, "caption", lambda text, **kw: captions.append(text))
        monkeypatch.setattr(ui.st, "button", lambda *a, **kw: False)

        ui._render_agent_model_table(list(APP_ONLY_AGENT_KEYS), "test")

        assert any("†" in c and "never applies in production" in c for c in captions)

    def test_no_footnote_when_the_group_has_no_marked_agents(self, monkeypatch):
        """The Technology Excellence table shows no dagger, so it must not
        carry a footnote explaining one."""
        captions = []
        monkeypatch.setattr(ui.st, "data_editor", lambda rows, **kw: rows)
        monkeypatch.setattr(ui.st, "caption", lambda text, **kw: captions.append(text))
        monkeypatch.setattr(ui.st, "button", lambda *a, **kw: False)

        ui._render_agent_model_table(["code_reviewer", "pr_arbiter"], "test")

        assert not any("†" in c for c in captions)
