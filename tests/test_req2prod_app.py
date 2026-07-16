"""
Unit tests for req2prod_app.py - the admin console as its own Streamlit app.

It exists so that restarting the public Job Finder never takes the console
down with it: they used to be one script behind ?admin=1, so a deploy
restarting jobfinder.service also killed the SDLC view someone was watching
that deploy through. See
docs/superpowers/specs/2026-07-16-split-admin-console-from-job-finder-design.md.

Network-free: get_admin_password() reaches AWS Secrets Manager, so every test
here stubs it. No test unlocks the console with a real password.
"""

import ast
from pathlib import Path

from streamlit.testing.v1 import AppTest

REPO_ROOT = Path(__file__).parent.parent

def _code_identifiers(filename: str) -> set[str]:
    """Every attribute and name the file actually *executes*, ignoring
    docstrings and comments - so a docstring that mentions st.stop() to explain
    its absence doesn't read as a call to it."""
    tree = ast.parse((REPO_ROOT / filename).read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


# Stubs the one call that would leave the machine, then runs the real script.
_SCRIPT = """
import boto3
boto3.client = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no network in tests"))
exec(open("req2prod_app.py").read())
"""


class TestTheConsoleStandsAlone:
    def test_it_asks_for_a_password_before_showing_anything(self):
        at = AppTest.from_string(_SCRIPT, default_timeout=60).run()

        assert not at.exception
        assert len(at.text_input) == 1
        assert at.text_input[0].label == "Password"
        assert at.tabs == [] or len(at.tabs) == 0, "no tabs before authenticating"

    def test_it_renders_the_four_tabs_once_authenticated(self):
        at = AppTest.from_string(_SCRIPT, default_timeout=60)
        at.session_state["admin_authed"] = True
        at.run()

        assert not at.exception
        # at.tabs is flat - nested sub-tabs are interleaved with their parents -
        # so check membership rather than the first four.
        labels = [t.label for t in at.tabs]
        for tab in ("Jobfinder Admin", "Req2Prod", "AI Models", "CTO Cockpit"):
            assert tab in labels

    def test_the_nested_tabs_render_too_not_just_their_parents(self):
        """The console rendering at all is the thing worth asserting: every
        tab's body runs on every rerun, so a broken one anywhere raises here."""
        at = AppTest.from_string(_SCRIPT, default_timeout=60)
        at.session_state["admin_authed"] = True
        at.run()

        assert not at.exception
        labels = [t.label for t in at.tabs]
        for tab in ("Request a New Feature", "Pipeline", "Documentation",
                    "Architecture", "Connectivity", "Cost"):
            assert tab in labels

    def test_it_does_not_gate_on_the_admin_query_param(self):
        """?admin=1 was the old gate. This app IS the console, so there is
        nothing left to gate on - reading query params here would mean the
        console could be reached without the password.

        Checks the parsed code, not the file text: both names appear in the
        module docstring, which explains what was removed and why."""
        assert "query_params" not in _code_identifiers("req2prod_app.py")

    def test_it_does_not_stop_the_script(self):
        """st.stop() existed only to keep the admin block from falling through
        into the public page below it. There is no page below it now."""
        assert "stop" not in _code_identifiers("req2prod_app.py")


class TestUnlimitedUserHasOneHome:
    """Both processes need it - the public app to exempt it from quotas, the
    console to report on it - so it lives in reporting.py rather than being
    copied into each. Two copies of an address is one rename away from an admin
    page quietly reporting on nobody."""

    def test_reporting_owns_it(self):
        from reporting import UNLIMITED_USER

        assert "@" in UNLIMITED_USER

    def test_streamlit_app_imports_rather_than_defines_it(self):
        source = (REPO_ROOT / "streamlit_app.py").read_text()

        assert "UNLIMITED_USER = " not in source
        assert "from reporting import UNLIMITED_USER" in source

    def test_the_console_imports_the_same_one(self):
        source = (REPO_ROOT / "req2prod_app.py").read_text()

        assert "UNLIMITED_USER = " not in source
        assert "from reporting import UNLIMITED_USER" in source


class TestThePublicAppNoLongerServesTheConsole:
    """The cut. streamlit_app.py served both behind ?admin=1, which made them
    one process under one systemd unit - so a deploy restarting jobfinder
    also killed the SDLC view someone was watching that deploy through."""

    def test_the_admin_block_is_gone(self):
        names = _code_identifiers("streamlit_app.py")

        assert "query_params" not in names, "?admin=1 should mean nothing now"
        assert "get_admin_password" not in names
        assert "boto3" not in names, "existed only for the password gate"

    def test_it_no_longer_imports_any_admin_module(self):
        source = (REPO_ROOT / "streamlit_app.py").read_text()

        for gone in ("cto_cockpit_admin", "jobfinder_admin", "req2prod.admin_ui"):
            assert f"import {gone}" not in source
            assert f"from {gone}" not in source

    def test_a_job_finder_change_does_not_restart_the_console(self):
        """The whole point of the split, asserted end to end: ship a change to
        the public app, and the process rendering the SDLC view keeps running."""
        from req2prod.deploy_targets import JOBFINDER, REQ2PROD, services_to_restart

        assert services_to_restart(["streamlit_app.py"]) == {JOBFINDER}
        assert REQ2PROD not in services_to_restart(["job_search.py"])
