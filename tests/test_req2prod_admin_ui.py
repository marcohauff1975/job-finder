"""
Unit tests for req2prod/admin_ui.py's "How this crew is built" section on
the AI Models tab - a live, per-agent role/backstory/lessons readout for
the Technology Excellence panel (technology_excellence_crew). Role and
backstory come from req2prod/config/agents.yaml, lessons from
req2prod/lessons/<agent_key>.md - both read fresh, independent of
req2prod.Req2Prod's own agents_config (which mutates backstory in place
to append lessons text at import time), so this section's "Backstory"
and "Lessons" stay genuinely separate rather than showing the same
lessons text twice. Skills isn't wired in yet, so that's still a
"coming soon" placeholder.
"""

import inspect
from contextlib import nullcontext
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import req2prod.admin_ui as m
from req2prod.model_registry import AGENT_DISPLAY_NAMES, TECH_EXCELLENCE_AGENT_KEYS
from req2prod.Req2Prod import ArchitectureDirectionResult, FeatureRequirementsResult

_PM = FeatureRequirementsResult(
    user_story="As a user, I want a thing",
    acceptance_criteria=["it works"],
    ready_for_development=True,
)
_ARCHITECT = ArchitectureDirectionResult(
    builds_on_existing_app=True,
    technical_notes="add it to the existing module",
    ready_for_development=True,
)

_SCRIPT = """
import req2prod.admin_ui as m
m.render_ai_models_tab()
"""

# Renders the real Request-a-New-Feature page with one already-challenged
# requirement on screen (both agents ready => Push is showing). REFINE and
# PENDING are substituted in per test. Nothing here patches module attributes:
# AppTest runs the script in this process against the same imported module, so
# a `m.x = ...` inside the script would outlive the test - use monkeypatch.
_REQUIREMENTS_SCRIPT = """
import req2prod.admin_ui as m
import streamlit as st
m.get_auto_deploy_mode = lambda: None
st.session_state["rc_messages"] = [
    {"role": "user", "content": "add a thing"},
    {"role": "product_manager", "content": "spec",
     "data": {"user_story": "s", "ready_for_development": True}},
    {"role": "software_architect", "content": "dir",
     "data": {"builds_on_existing_app": True, "technical_notes": "n",
              "ready_for_development": True}},
]
st.session_state["rc_session_id"] = "test"
st.session_state.setdefault("rc_refine_open", REFINE)
PENDING
m.render_requirements_tab()
"""


def _render(refine_open=True, pending=None):
    script = _REQUIREMENTS_SCRIPT.replace("REFINE", str(refine_open)).replace(
        "PENDING",
        f'st.session_state["rc_pending"] = {pending!r}' if pending else "",
    )
    return AppTest.from_string(script, default_timeout=60).run()


class TestLoadCleanAgentIdentity:
    def test_returns_role_and_backstory_for_a_real_agent(self):
        cfg = m._load_clean_agent_identity("cto")

        assert cfg["role"].strip()
        assert cfg["backstory"].strip()

    def test_backstory_has_no_lesson_content_baked_in(self):
        """The real agents.yaml backstory legitimately references
        "LESSONS LEARNED" as a forward-pointer ("Consult your LESSONS
        LEARNED below before you flag...") - that's expected prose, not
        the bug this guards against. What must NOT be present is the
        actual lesson *content* - unlike req2prod.Req2Prod's own
        agents_config (mutated in place by
        _augment_backstories_with_lessons to append the full lessons
        file text), reading agents.yaml directly should never contain
        a real lesson entry ID like "CT-01"."""
        cfg = m._load_clean_agent_identity("cto")

        assert "CT-01" not in cfg["backstory"]


class TestLoadLessons:
    def test_returns_markdown_for_an_agent_with_lessons(self):
        lessons = m._load_lessons("cto")

        assert lessons is not None
        assert "## CT-01" in lessons

    def test_returns_none_for_an_unknown_agent(self):
        assert m._load_lessons("not_a_real_agent_key") is None


class TestTechnologyExcellenceCrewDeepDive:
    def test_renders_with_no_exceptions(self):
        at = AppTest.from_string(_SCRIPT)

        at.run(timeout=30)

        assert not at.exception

    def test_one_expander_per_tech_excellence_agent(self):
        at = AppTest.from_string(_SCRIPT)

        at.run(timeout=30)

        labels = [e.label for e in at.expander]
        for agent_key in TECH_EXCELLENCE_AGENT_KEYS:
            assert AGENT_DISPLAY_NAMES[agent_key] in labels

    def test_shows_role_backstory_and_lessons(self):
        at = AppTest.from_string(_SCRIPT)

        at.run(timeout=30)

        markdown_text = "\n".join(md.value for md in at.markdown)
        assert "**Role**" in markdown_text
        assert "**Backstory**" in markdown_text
        assert "**Lessons**" in markdown_text
        # A real lesson entry from cto.md should be visible, not just
        # the section header - proves the file content actually renders.
        assert "CT-01" in markdown_text

    def test_shows_skills_as_coming_soon(self):
        at = AppTest.from_string(_SCRIPT)

        at.run(timeout=30)

        caption_text = "\n".join(c.value for c in at.caption)
        assert "Skills: coming soon" in caption_text


class TestJumpToPipelineButton:
    """The button's whole mechanism is matching a tab by its visible text, so
    the label it clicks and the label the tab renders have to be the same
    string - two literals that merely agree today is exactly how the
    subscription tool bridge broke (see req2prod/backend.py's
    TOOL_CLI_COMMAND)."""

    def test_pipeline_label_is_one_of_the_rendered_tab_labels(self):
        assert m.PIPELINE_TAB_LABEL in m.REQ2PROD_TAB_LABELS

    def test_the_console_renders_exactly_these_labels(self):
        """req2prod_app.py must not reintroduce its own literal list. It holds
        the tabs now - they moved there when the console became its own
        process."""
        source = (Path(__file__).parent.parent / "req2prod_app.py").read_text()

        assert "list(REQ2PROD_TAB_LABELS)" in source
        assert '"Request a New Feature", "Pipeline", "Documentation"' not in source

    def test_the_injected_script_carries_the_real_label(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            m.st.components.v1, "html", lambda html, **kw: captured.update(html=html)
        )
        monkeypatch.setattr(
            m.st, "session_state", {"rc_jump_to_pipeline": True, "rc_jump_nonce": 3}
        )

        m._jump_to_pipeline_tab_if_requested()

        assert f'"{m.PIPELINE_TAB_LABEL}"' in captured["html"]
        assert 'button[role="tab"]' in captured["html"]
        assert "const nonce = 3;" in captured["html"]

    def test_does_nothing_unless_the_button_was_clicked(self, monkeypatch):
        """This runs on every rerun of a tab whose reruns are live crew calls -
        it must be inert unless a click actually set the flag."""
        called = []
        monkeypatch.setattr(
            m.st.components.v1, "html", lambda html, **kw: called.append(html)
        )
        monkeypatch.setattr(m.st, "session_state", {})

        m._jump_to_pipeline_tab_if_requested()

        assert called == []

    def test_flag_survives_the_run_that_injects_the_script(self, monkeypatch):
        """Popping it here would tear the iframe down before its async script
        ran - the exact bug _prefill_chat_input_if_requested documents. The
        browser-side nonce is what makes it fire once, not a server-side pop."""
        state = {"rc_jump_to_pipeline": True, "rc_jump_nonce": 1}
        monkeypatch.setattr(m.st.components.v1, "html", lambda html, **kw: None)
        monkeypatch.setattr(m.st, "session_state", state)

        m._jump_to_pipeline_tab_if_requested()

        assert state["rc_jump_to_pipeline"] is True


class TestLatestBuildResult:
    def test_returns_the_result_when_the_build_is_the_latest_thing(self):
        messages = [
            {"role": "user", "content": "add a thing"},
            {"role": "software_engineer", "content": "done", "data": {"pr_url": "u"}},
        ]

        assert m._latest_build_result(messages) == {"pr_url": "u"}

    def test_none_when_something_was_said_after_the_build(self):
        messages = [
            {"role": "software_engineer", "content": "done", "data": {"pr_url": "u"}},
            {"role": "user", "content": "and another thing"},
        ]

        assert m._latest_build_result(messages) is None

    def test_none_for_a_failed_build_which_carries_no_data(self):
        """A failure appends a plain warning with no data - there's no PR to
        go and look at, so no button."""
        messages = [{"role": "software_engineer", "content": "⚠️ The build failed"}]

        assert m._latest_build_result(messages) is None

    def test_none_on_an_empty_conversation(self):
        assert m._latest_build_result([]) is None


class TestDocumentationTabLinks:
    """The tab is now two links to the deployed site rather than an embed of
    it (see render_documentation_tab's docstring for why). Nothing here hits
    the network - these pin the URLs to the files the deploy actually
    publishes, so renaming one in site/ can't quietly leave a dead link."""

    def test_urls_point_at_files_that_exist_in_site(self):
        site = Path(__file__).parent.parent / "site"

        assert m.REQ2PROD_SITE_URL == "https://req2prod.nl"
        assert (site / "index.html").exists(), "the Overview link's target"
        assert m.REQ2PROD_DETAILS_URL.endswith("/details.html")
        assert (site / "details.html").exists(), "the How it works link's target"

    def test_details_url_is_built_from_the_site_url(self):
        """One host, not two literals that agree until someone moves it."""
        assert m.REQ2PROD_DETAILS_URL.startswith(m.REQ2PROD_SITE_URL + "/")

    def test_renders_two_links_and_no_iframe(self, monkeypatch):
        links, htmls = [], []
        monkeypatch.setattr(m.st, "link_button", lambda label, url, **kw: links.append((label, url)))
        monkeypatch.setattr(m.st, "markdown", lambda *a, **kw: None)
        monkeypatch.setattr(m.st, "caption", lambda *a, **kw: None)
        monkeypatch.setattr(m.st, "columns", lambda n: (nullcontext(), nullcontext()))
        monkeypatch.setattr(m.st.components.v1, "html", lambda *a, **kw: htmls.append(a))

        m.render_documentation_tab()

        assert [u for _, u in links] == [m.REQ2PROD_SITE_URL, m.REQ2PROD_DETAILS_URL]
        assert htmls == [], "the embed is gone - no iframe should be rendered"


class TestInputHiddenWhileAwaitingPush:
    """Both agents ready means the page is asking one question - push it or
    don't - so the "describe a feature" box stops applying and only competes
    with the answer. These pin the state machine around hiding it, because
    hiding it naively is a trap: st.chat_input is the only way to add a
    message and there is no session switcher to start over with."""

    @staticmethod
    def _ready_messages():
        return [
            {"role": "user", "content": "add a thing"},
            {"role": "product_manager", "content": "spec",
             "data": {"user_story": "s", "ready_for_development": True}},
            {"role": "software_architect", "content": "direction",
             "data": {"builds_on_existing_app": True, "technical_notes": "n",
                      "ready_for_development": True}},
        ]

    def test_ready_pair_is_what_gates_the_box(self):
        """The gate is real: this pair is exactly the state that shows Push."""
        assert m._latest_ready_pair(self._ready_messages()) is not None

    def test_a_later_message_reopens_the_box_on_its_own(self):
        """No flag needed - saying anything makes the verdict stale, which is
        what returns the page to the ordinary conversation flow."""
        messages = self._ready_messages() + [{"role": "user", "content": "also do X"}]

        assert m._latest_ready_pair(messages) is None

    def test_seeding_a_demo_asks_for_the_box_back(self, monkeypatch):
        """A demo prefills the box, and the injector silently gives up when
        there's no textarea - so seeding one while Push is showing has to
        reopen it, or the button reads as dead."""
        state = {}
        monkeypatch.setattr(m.st, "session_state", state)

        m._seed_demo_request("a ready-made request")

        assert state["rc_demo_inject"] == "a ready-made request"
        assert state["rc_refine_open"] is True

    def test_seeding_bumps_the_nonce_so_each_click_injects_once(self, monkeypatch):
        state = {"rc_demo_nonce": 4}
        monkeypatch.setattr(m.st, "session_state", state)

        m._seed_demo_request("another one")

        assert state["rc_demo_nonce"] == 5

    def test_the_injector_is_skipped_while_the_box_is_hidden(self, monkeypatch):
        """Renders the real page rather than matching the source line that
        skips it: that assertion pinned one exact `if` condition, so adding a
        second reason to skip (rc_pending) broke the test while the behaviour
        it names was still correct."""
        calls = []
        monkeypatch.setattr(m, "_prefill_chat_input_if_requested", lambda: calls.append(1))

        at = _render(refine_open=False)

        assert not at.exception
        assert len(at.chat_input) == 0, "precondition: the box is hidden here"
        assert calls == []

    def test_submitting_clears_the_refine_escape(self):
        """It was opened for this message, and the message makes ready_pair
        None by itself - leaving it set would hold the box open through the
        next ready verdict too."""
        source = inspect.getsource(m.render_requirements_tab)

        assert 'st.session_state.pop("rc_refine_open", None)' in source


class TestInputDisabledWhileWorking:
    """While the agents think, the box sat there enabled and empty - it looked
    like the requirement had been swallowed, and invited a second one on top of
    a run already in flight. It can't be fixed in place: Streamlit cannot
    change a widget mid-run, so a box drawn before the crew call stays as drawn
    for the call's whole duration. Hence the split - _accept_requirement()
    records and reruns, _run_requirement_challenge() does the work on the way
    back, under a box already drawn disabled. These pin both halves."""

    @pytest.fixture
    def state(self, monkeypatch):
        st_state = {"rc_session_id": "test", "rc_messages": []}
        monkeypatch.setattr(m.st, "session_state", st_state)
        monkeypatch.setattr(m, "save_session", lambda *a, **kw: None)
        monkeypatch.setattr(m.st, "rerun", lambda: st_state.__setitem__("reran", True))
        return st_state

    def test_accepting_records_the_message_and_queues_it(self, state):
        m._accept_requirement("add a thing")

        assert state["rc_messages"] == [{"role": "user", "content": "add a thing"}]
        assert state["rc_pending"] == "add a thing"
        assert state["reran"] is True

    def test_accepting_does_not_run_the_crew(self, state, monkeypatch):
        """The whole point of the split. If the work happens here, it happens
        under the enabled box this change exists to get rid of."""
        monkeypatch.setattr(
            m, "challenge_requirement", lambda *a, **kw: pytest.fail("ran too early")
        )

        m._accept_requirement("add a thing")

    def test_the_queued_work_runs_and_appends_both_agents(self, state, monkeypatch):
        state["rc_messages"] = [{"role": "user", "content": "add a thing"}]
        state["rc_pending"] = "add a thing"
        monkeypatch.setattr(m.st, "spinner", lambda *a, **kw: nullcontext())
        monkeypatch.setattr(m, "_run_with_retry", lambda fn, arg: (_PM, _ARCHITECT))

        m._run_requirement_challenge()

        assert [msg["role"] for msg in state["rc_messages"]] == [
            "user",
            "product_manager",
            "software_architect",
        ]

    def test_the_flag_is_cleared_even_when_the_crew_blows_up(self, state, monkeypatch):
        """Cleared before the call, not after. A crew call that raises past the
        handler - or a client that disconnects mid-spinner, which this page has
        already been bitten by - would otherwise leave rc_pending set, and the
        box disabled forever while the same requirement reran on every rerun."""
        state["rc_pending"] = "add a thing"
        monkeypatch.setattr(m.st, "spinner", lambda *a, **kw: nullcontext())

        def boom(fn, arg):
            raise BaseException("client went away")

        monkeypatch.setattr(m, "_run_with_retry", boom)

        with pytest.raises(BaseException, match="client went away"):
            m._run_requirement_challenge()

        assert "rc_pending" not in state

    def test_the_box_is_disabled_while_a_requirement_is_pending(self, monkeypatch):
        monkeypatch.setattr(m, "_run_requirement_challenge", lambda: None)

        at = _render(pending="add a thing")

        assert not at.exception
        assert at.chat_input[0].disabled is True

    def test_the_box_is_enabled_when_nothing_is_pending(self):
        at = _render()

        assert not at.exception
        assert at.chat_input[0].disabled is False

    def test_the_placeholder_never_changes(self, monkeypatch):
        """The injector finds the box by its placeholder text
        (_prefill_chat_input_if_requested's selector). A "Working..." variant
        while pending would leave it silently matching nothing - the same
        two-literals-that-agree-until-they-don't trap as TOOL_CLI_COMMAND."""
        monkeypatch.setattr(m, "_run_requirement_challenge", lambda: None)

        idle = _render()
        working = _render(pending="add a thing")

        assert idle.chat_input[0].placeholder == m._REQUIREMENT_INPUT_PLACEHOLDER
        assert working.chat_input[0].placeholder == m._REQUIREMENT_INPUT_PLACEHOLDER

    def test_the_injector_is_skipped_while_the_box_is_disabled(self, monkeypatch):
        """Same reason it's skipped while hidden: a disabled textarea can't be
        typed into, so injecting into it would look like a dead button."""
        calls = []
        monkeypatch.setattr(m, "_run_requirement_challenge", lambda: None)
        monkeypatch.setattr(m, "_prefill_chat_input_if_requested", lambda: calls.append(1))

        _render(pending="add a thing")

        assert calls == []

    def test_the_work_runs_after_the_box_is_drawn(self):
        """Order is the fix. Drawing the box after the crew call would put an
        enabled one back on screen for the whole run."""
        source = inspect.getsource(m.render_requirements_tab)

        assert source.index("st.chat_input(") < source.index("_run_requirement_challenge()")


class TestDemoModeToggle:
    """The demo requests are presentation props on a page for describing real
    features, so they sit behind a toggle that's off by default.

    A toggle rather than a Demo tab of their own: every st.tabs() panel's code
    runs on every rerun whether or not it's the visible one, so a second tab
    rendering this same page would put every widget on it into the script
    twice - two chat inputs, two "Push to Software Engineer" buttons sharing
    one key.

    Renders the real page rather than reading its source, so these assert what
    someone actually sees. get_auto_deploy_mode() is stubbed because it calls
    the GitHub API on every render and this file stays network-free.
    """

    _SCRIPT = """
import req2prod.admin_ui as m
m.get_auto_deploy_mode = lambda: None
m.render_requirements_tab()
"""

    def test_the_page_renders_with_no_demo_buttons_by_default(self):
        at = AppTest.from_string(self._SCRIPT, default_timeout=60).run()

        assert not at.exception
        assert [t.value for t in at.toggle] == [False]
        assert at.toggle[0].label == "🎬 Demo mode"
        assert [b.label for b in at.button] == []

    def test_switching_it_on_reveals_exactly_the_two_demo_requests(self):
        at = AppTest.from_string(self._SCRIPT, default_timeout=60).run()

        at.toggle[0].set_value(True).run()

        assert not at.exception
        assert [b.label for b in at.button] == [
            "➕ Demo: Add Req2Prod Logo",
            "➖ Demo: Remove Req2Prod Logo",
        ]

    def test_switching_it_back_off_hides_them_again(self):
        at = AppTest.from_string(self._SCRIPT, default_timeout=60).run()
        at.toggle[0].set_value(True).run()

        at.toggle[0].set_value(False).run()

        assert not at.exception
        assert [b.label for b in at.button] == []


class TestPushAndRefineSitTogether:
    """Both agents ready means one question with two answers - ship it, or say
    more first - so both buttons sit on one row. Renders the real page, so
    these assert what's actually on screen rather than what the source says."""

    _SCRIPT = """
import req2prod.admin_ui as m
import streamlit as st
m.get_auto_deploy_mode = lambda: None
st.session_state["rc_messages"] = [
    {"role": "user", "content": "add a thing"},
    {"role": "product_manager", "content": "spec",
     "data": {"user_story": "s", "ready_for_development": True}},
    {"role": "software_architect", "content": "dir",
     "data": {"builds_on_existing_app": True, "technical_notes": "n",
              "ready_for_development": True}},
]
st.session_state["rc_session_id"] = "test"
st.session_state.setdefault("rc_refine_open", REFINE)
m.render_requirements_tab()
"""

    def _run(self, refine_open):
        return AppTest.from_string(
            self._SCRIPT.replace("REFINE", str(refine_open)), default_timeout=60
        ).run()

    def test_both_buttons_render_while_a_requirement_waits_to_be_pushed(self):
        at = self._run(False)

        assert not at.exception
        assert [b.label for b in at.button] == [
            "🚀 Push to Software Engineer",
            "✏️ Add something first",
        ]
        assert len(at.chat_input) == 0, "the box stays hidden until asked for"

    def test_refine_goes_away_once_the_box_is_back(self):
        """Nothing to offer once the thing it asks for is already there."""
        at = self._run(True)

        assert not at.exception
        assert [b.label for b in at.button] == ["🚀 Push to Software Engineer"]
        assert len(at.chat_input) == 1

    def test_push_survives_asking_for_the_box(self):
        """The point of the escape is adding to the requirement, not
        abandoning it - Push has to still be there."""
        at = self._run(True)

        assert "🚀 Push to Software Engineer" in [b.label for b in at.button]
