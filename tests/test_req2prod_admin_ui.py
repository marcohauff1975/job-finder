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
