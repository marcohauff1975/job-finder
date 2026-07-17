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


class TestTheTableOffersNoChoiceItCannotHonour:
    """A dagger and a footnote were the first attempt. They were not enough:
    the Subscription dropdown was still live, so the page still offered a
    setting that could never take effect, and a footnote does not stop someone
    changing it and expecting something. The column is now absent for these
    agents entirely."""

    @pytest.fixture
    def render(self, monkeypatch):
        def _run(agent_keys, show_subscription=True):
            captured = {"rows": None, "columns": None, "disabled": None}

            def fake_editor(rows, column_config=None, disabled=None, **kw):
                captured["rows"] = rows
                captured["columns"] = column_config
                captured["disabled"] = disabled
                return rows

            monkeypatch.setattr(ui.st, "data_editor", fake_editor)
            monkeypatch.setattr(ui.st, "caption", lambda *a, **kw: None)
            monkeypatch.setattr(ui.st, "button", lambda *a, **kw: False)
            ui._render_agent_model_table(agent_keys, "t", show_subscription=show_subscription)
            return captured

        return _run

    def test_app_only_agents_get_no_subscription_column(self, render):
        out = render(list(APP_ONLY_AGENT_KEYS), show_subscription=False)

        for row in out["rows"]:
            assert "Subscription model" not in row
            assert "Recommended (Subscription)" not in row
        assert "Subscription model" not in out["columns"]

    def test_ci_agents_keep_a_real_subscription_column(self, render):
        """The toggle genuinely drives these - the column must stay editable."""
        out = render(["code_reviewer", "pr_arbiter"])

        assert all("Subscription model" in row for row in out["rows"])
        assert "Subscription model" in out["columns"]
        assert "Subscription model" not in (out["disabled"] or [])

    def test_the_why_text_drops_the_subscription_rationale_too(self, render):
        """Explaining a choice that isn't offered only raises the question of
        where to make it."""
        out = render(list(APP_ONLY_AGENT_KEYS), show_subscription=False)

        assert all("Subscription:" not in row["Why"] for row in out["rows"])

    def test_saving_never_writes_a_subscription_model_for_them(self, monkeypatch):
        """The real guarantee. Reading a column that isn't rendered would raise;
        writing one would persist a value the UI never offered."""
        writes = []
        monkeypatch.setattr(ui, "set_agent_model", lambda k, m, tier: writes.append(tier))
        monkeypatch.setattr(ui.st, "data_editor", lambda rows, **kw: [
            dict(r, **{"API model": "Opus 4.8 (strongest, slowest/priciest)"}) for r in rows
        ])
        monkeypatch.setattr(ui.st, "caption", lambda *a, **kw: None)
        monkeypatch.setattr(ui.st, "button", lambda *a, **kw: True)
        monkeypatch.setattr(ui.st, "success", lambda *a, **kw: None)
        monkeypatch.setattr(ui.st, "rerun", lambda: None)
        monkeypatch.setattr(ui.st, "info", lambda *a, **kw: None)

        ui._render_agent_model_table(
            list(APP_ONLY_AGENT_KEYS), "t", show_subscription=False
        )

        assert writes, "the API change should still save"
        assert "subscription" not in writes


class TestTheTabActuallyPassesTheFlag:
    """The gap the first version of these tests left. They proved
    _render_agent_model_table *can* hide the column, and nothing proved
    render_ai_models_tab asks it to - so deleting `show_subscription=False` at
    the call site put the dropdown straight back with every test still green.
    That is the exact regression worth catching."""

    @pytest.fixture
    def tab_calls(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            ui,
            "_render_agent_model_table",
            lambda keys, prefix, show_subscription=True: calls.append(
                {"keys": tuple(keys), "prefix": prefix, "subscription": show_subscription}
            ),
        )
        for name in ("markdown", "caption", "divider", "subheader", "write", "link_button"):
            monkeypatch.setattr(ui.st, name, lambda *a, **kw: None, raising=False)
        monkeypatch.setattr(ui, "get_agent_backend", lambda: "subscription", raising=False)
        monkeypatch.setattr(ui.st, "toggle", lambda *a, **kw: False, raising=False)
        ui.render_ai_models_tab()
        return calls

    def test_the_app_group_is_rendered_without_a_subscription_column(self, tab_calls):
        app = [c for c in tab_calls if set(c["keys"]) == set(APP_ONLY_AGENT_KEYS)]
        assert app, "no table rendered for the app-run agents"
        assert app[0]["subscription"] is False, (
            "render_ai_models_tab stopped passing show_subscription=False - the "
            "Subscription dropdown is back for agents that can never use it."
        )

    def test_the_ci_group_keeps_its_subscription_column(self, tab_calls):
        ci = [c for c in tab_calls if "code_reviewer" in c["keys"]]
        assert ci, "no table rendered for the CI agents"
        assert ci[0]["subscription"] is True

    def test_no_app_only_agent_leaks_into_the_ci_group(self, tab_calls):
        for call in tab_calls:
            if call["subscription"]:
                assert not (set(call["keys"]) & set(APP_ONLY_AGENT_KEYS)), (
                    "an app-run agent is in a table that offers a subscription model"
                )
