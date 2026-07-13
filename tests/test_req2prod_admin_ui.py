"""
Unit tests for req2prod/admin_ui.py's "How this crew is built" section on
the AI Models tab - a live, per-agent role/backstory readout for the
Technology Excellence panel (technology_excellence_crew), read straight
from req2prod/config/agents.yaml rather than a static writeup. Lessons
and skills aren't wired in yet, so those are just "coming soon"
placeholders - not tested beyond confirming the caption text is present.
"""

from streamlit.testing.v1 import AppTest

from req2prod.model_registry import AGENT_DISPLAY_NAMES, TECH_EXCELLENCE_AGENT_KEYS

_SCRIPT = """
import req2prod.admin_ui as m
m.render_ai_models_tab()
"""


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

    def test_shows_role_and_backstory(self):
        at = AppTest.from_string(_SCRIPT)

        at.run(timeout=30)

        markdown_text = "\n".join(md.value for md in at.markdown)
        assert "**Role**" in markdown_text
        assert "**Backstory**" in markdown_text

    def test_shows_lessons_and_skills_as_coming_soon(self):
        at = AppTest.from_string(_SCRIPT)

        at.run(timeout=30)

        caption_text = "\n".join(c.value for c in at.caption)
        assert "Lessons: coming soon" in caption_text
        assert "Skills: coming soon" in caption_text
