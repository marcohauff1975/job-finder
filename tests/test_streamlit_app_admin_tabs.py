"""
Regression test for the admin tab wiring in streamlit_app.py.

st.tabs()'s default behavior (on_change="ignore") runs every tab's body
on every rerun regardless of which tab is visually selected - only the
DOM placement is tab-scoped, not the underlying Python execution. That
let render_requirements_tab()'s `with st.sidebar:` block leak onto every
other admin tab, since st.sidebar is a page-level singleton, unaffected
by which tab container is on-screen. Fixed by adding on_change="rerun"
to both tab levels and gating each tab's body on its own .open, so a
hidden tab's code (sidebar included) never runs. See streamlit_app.py's
admin tab block.
"""

from streamlit.testing.v1 import AppTest


def _run_admin_app():
    at = AppTest.from_file("streamlit_app.py")
    at.query_params["admin"] = "1"
    at.session_state["admin_authed"] = True
    at.run(timeout=30)
    return at


class TestAdminSidebarOnlyOnRequestNewFeature:
    def test_sidebar_is_empty_on_the_default_overview_tab(self):
        at = _run_admin_app()

        assert not at.exception
        assert len(at.sidebar) == 0

    def test_top_level_tabs_render(self):
        at = _run_admin_app()

        assert [t.proto.label for t in at.tabs] == ["Overview", "Req2Prod", "AI Models", "CTO Cockpit"]
