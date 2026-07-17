"""
The demo instructions have to keep describing the app they target.

Both demo requests (req2prod/admin_ui.py's DEMO_ADD_LOGO_REQUIREMENT and
DEMO_REMOVE_LOGO_REQUIREMENT) tell the software_engineer agent where to put the
logo by quoting real code from streamlit_app.py. That makes the instruction a
second copy of a fact the app already owns - and a copy that agrees with the
original right up until someone edits one of them. This repo has been bitten by
exactly that shape before: bash_tool_instructions said `python` while the
allowlist said `python3` (req2prod/backend.py's TOOL_CLI_COMMAND), and the
jump-to-tab script matched a label literal that st.tabs also declared.

It bit the demo on 2026-07-16. The instruction navigated by "the admin block
that ends at the `st.stop()` around line 253". That block stopped existing when
the admin console became its own process (req2prod_app.py), and nothing told the
instruction. The engineer improvised a new location on every run - across eight
"add logo" commits the line landed at 277, 267, 412, 322, 411, 322, 323, 320 -
and, left to interpret "public landing page" alone, one run gated the logo on
`not st.session_state.get("authentication_status")`, making it invisible to
every signed-in user.

These tests are the thing that was missing. They fail when the instruction goes
stale, instead of a demo failing in front of an audience.
"""

from pathlib import Path

import pytest

import req2prod.admin_ui as m

_APP = Path(__file__).parent.parent / "streamlit_app.py"


@pytest.fixture
def app_source():
    return _APP.read_text()


class TestTheAnchorIsReal:
    """The instruction tells the engineer to place the logo immediately after a
    specific block. If that block is renamed, moved into a function, or deleted
    the way the admin block was, the instruction becomes a map to nowhere."""

    def test_the_anchor_exists_in_the_app(self, app_source):
        assert m.DEMO_LOGO_ANCHOR in app_source, (
            f"DEMO_LOGO_ANCHOR ({m.DEMO_LOGO_ANCHOR!r}) is no longer in "
            "streamlit_app.py. The demo instruction points the engineer at code "
            "that does not exist, which is what made it invent a new location on "
            "every run. Update the anchor and the instruction together."
        )

    def test_the_anchor_appears_exactly_once(self, app_source):
        """An ambiguous anchor is barely better than a missing one - two
        matches means "immediately after" names two different places."""
        assert app_source.count(m.DEMO_LOGO_ANCHOR) == 1

    def test_the_add_instruction_quotes_the_anchor(self):
        """The whole point of the shared constant: the instruction must not
        drift into carrying its own copy of the anchor text."""
        assert m.DEMO_LOGO_ANCHOR in m.DEMO_ADD_LOGO_REQUIREMENT


class TestTheInstructionsCarryNoStaleLandmarks:
    """The previous instruction referenced an "admin block", "admin tabs" and a
    hardcoded line number, all of which stopped being true."""

    @pytest.mark.parametrize(
        "requirement",
        [m.DEMO_ADD_LOGO_REQUIREMENT, m.DEMO_REMOVE_LOGO_REQUIREMENT],
        ids=["add", "remove"],
    )
    def test_no_hardcoded_line_numbers(self, requirement):
        """"around line 253" survived the code it described by six hours. A
        line number in prose is stale the moment anyone inserts a line."""
        assert "line 253" not in requirement
        assert "around line" not in requirement

    @pytest.mark.parametrize(
        "requirement",
        [m.DEMO_ADD_LOGO_REQUIREMENT, m.DEMO_REMOVE_LOGO_REQUIREMENT],
        ids=["add", "remove"],
    )
    def test_no_reference_to_the_admin_block_that_no_longer_exists(self, requirement):
        assert "admin block" not in requirement
        assert "admin tabs" not in requirement

    def test_the_admin_block_really_is_gone(self, app_source):
        """Pins the fact the above rests on: streamlit_app.py has no admin
        page any more (it moved to req2prod_app.py). If an admin block ever
        comes back here, these instructions need rewriting again."""
        assert "?admin=1" not in app_source
        assert 'query_params.get("admin")' not in app_source


class TestTheAddInstructionForbidsTheBugThatShipped:
    """The engineer read "public landing page" as "logged-out only" and gated
    the logo accordingly. The code was coherent, review passed, and the logo
    was invisible to every signed-in user. Prose is the only place this can be
    prevented, so the prose has to say it."""

    def test_it_names_the_condition_that_must_not_be_used(self):
        assert "authentication_status" in m.DEMO_ADD_LOGO_REQUIREMENT

    def test_it_says_the_logo_must_be_visible_to_signed_in_users(self):
        assert "signed-in users" in m.DEMO_ADD_LOGO_REQUIREMENT

    def test_it_defines_what_public_does_not_mean(self):
        assert "logged-out visitors" in m.DEMO_ADD_LOGO_REQUIREMENT

    def test_it_names_the_file_to_edit_and_the_one_to_leave_alone(self):
        assert "streamlit_app.py" in m.DEMO_ADD_LOGO_REQUIREMENT
        assert "req2prod_app.py" in m.DEMO_ADD_LOGO_REQUIREMENT


class TestAddAndRemoveStayInverses:
    """Add and Remove are run against each other all day. They agree on the id
    or the pair silently stops being a pair."""

    def test_both_pin_the_same_container_id(self):
        assert 'id="req2prod-demo-logo"' in m.DEMO_ADD_LOGO_REQUIREMENT
        assert 'id="req2prod-demo-logo"' in m.DEMO_REMOVE_LOGO_REQUIREMENT

    def test_the_id_matches_the_svg_actually_handed_to_the_engineer(self):
        assert 'id="req2prod-demo-logo"' in m._DEMO_LOGO_SVG

    def test_remove_tolerates_an_already_absent_logo(self):
        """The demo is run repeatedly and the two requests race. "Nothing to
        remove" is an ordinary outcome - an engineer that invents a change
        rather than saying so is how a no-op run turns into a broken app."""
        assert "make no code change" in m.DEMO_REMOVE_LOGO_REQUIREMENT
