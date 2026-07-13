"""
Regression test for the admin tab wiring in streamlit_app.py.

st.tabs() runs every tab's body on every rerun regardless of which tab
is visually selected - only the DOM placement is tab-scoped, not the
underlying Python execution (Streamlit has no way to gate a tab body on
whether it's the one currently on-screen). That let
render_requirements_tab()'s `with st.sidebar:` block leak onto every
other admin tab, since st.sidebar is a page-level singleton, unaffected
by which tab container is on-screen. Fixed by replacing that block with
an `st.expander` rendered inside the "Request a New Feature" tab itself
(req2prod/admin_ui.py's render_requirements_tab) - a normal container is
properly scoped to its tab's DOM subtree, unlike st.sidebar.
"""

import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

import auth
import reporting


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh SQLite file with auth.py's and reporting.py's schemas
    applied, with reporting.DB_PATH pointed at it for the duration of
    the test, so the admin path's get_report() call doesn't hit the
    real data/auth.db."""
    db_path = tmp_path / "test_auth.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(auth.SCHEMA)
    conn.executescript(reporting.SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setattr(reporting, "DB_PATH", db_path)
    return db_path


def _run_admin_app():
    at = AppTest.from_file("streamlit_app.py")
    at.query_params["admin"] = "1"
    at.session_state["admin_authed"] = True
    at.run(timeout=30)
    return at


class TestAdminSidebarOnlyOnRequestNewFeature:
    def test_sidebar_is_empty_on_the_default_jobfinder_admin_tab(self, db):
        at = _run_admin_app()

        assert not at.exception
        assert len(at.sidebar) == 0

    def test_top_level_tabs_render(self, db):
        at = _run_admin_app()

        assert [t.proto.label for t in at.tabs] == ["Jobfinder Admin", "Req2Prod", "AI Models", "CTO Cockpit"]
