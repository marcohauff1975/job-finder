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
