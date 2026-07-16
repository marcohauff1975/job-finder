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

from streamlit.testing.v1 import AppTest

import req2prod.admin_ui as m
from req2prod.model_registry import AGENT_DISPLAY_NAMES, TECH_EXCELLENCE_AGENT_KEYS

_SCRIPT = """
import req2prod.admin_ui as m
m.render_ai_models_tab()
"""


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

    def test_streamlit_app_renders_exactly_these_labels(self):
        """streamlit_app.py must not reintroduce its own literal list."""
        source = (Path(__file__).parent.parent / "streamlit_app.py").read_text()

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

    def test_the_injector_is_skipped_while_the_box_is_hidden(self):
        source = inspect.getsource(m.render_requirements_tab)

        assert "if not awaiting_push:\n        _prefill_chat_input_if_requested()" in source

    def test_submitting_clears_the_refine_escape(self):
        """It was opened for this message, and the message makes ready_pair
        None by itself - leaving it set would hold the box open through the
        next ready verdict too."""
        source = inspect.getsource(m.render_requirements_tab)

        assert 'st.session_state.pop("rc_refine_open", None)' in source


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
