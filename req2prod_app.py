"""
The admin console - Jobfinder Admin, Req2Prod, AI Models, CTO Cockpit.

Run:
    streamlit run req2prod_app.py --server.port 8502

A second Streamlit process, served at https://req2prod.nl/app, so that
restarting the public Job Finder app never takes the console down with it.

Why this file exists at all: the console used to be this same script behind
`?admin=1`, which made it one process under one systemd unit - so a deploy
restarting jobfinder.service also killed the SDLC view someone was watching
that very deploy through. The view you watch a deploy with cannot be the
thing the deploy restarts. See
docs/superpowers/specs/2026-07-16-split-admin-console-from-job-finder-design.md.

Everything below is the old `if st.query_params.get("admin") is not None:`
block from streamlit_app.py, unchanged except for losing the `?admin=1` gate
and the st.stop() that used to keep it from falling through into the public
page - neither has anything to gate or stop now that this is its own app.
"""

import os

import boto3
import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from cto_cockpit_admin import render_architecture_tab, render_connectivity_tab, render_cost_tab
from jobfinder_admin import render_overview_tab
from reporting import UNLIMITED_USER
from req2prod.admin_ui import (
    REQ2PROD_TAB_LABELS,
    render_ai_models_tab,
    render_documentation_tab,
    render_req2prod_pipeline_tab,
    render_requirements_tab,
)

load_dotenv()

ADMIN_SECRET_NAME = "job-finder/admin-password"
AWS_REGION = os.getenv("AWS_REGION", "eu-north-1")


@st.cache_resource
def get_admin_password() -> str | None:
    """Fetches the admin-dashboard password from AWS Secrets Manager
    (see crewai-infra/secrets.tf) - cached for the life of the process
    so this only calls Secrets Manager once, not on every Streamlit
    rerun. Uses the same default AWS credential chain (AWS_ACCESS_KEY_ID
    / AWS_SECRET_ACCESS_KEY in .env) the app already uses for SES.
    Returns None if the secret can't be fetched, in which case the admin
    dashboard simply can't be unlocked - there is no hardcoded
    fallback."""
    try:
        client = boto3.client("secretsmanager", region_name=AWS_REGION)
        return client.get_secret_value(SecretId=ADMIN_SECRET_NAME)["SecretString"]
    except (BotoCoreError, ClientError):
        return None


st.set_page_config(
    page_title="Req2Prod — Admin",
    page_icon="🔀",
    layout="centered",
)

if not st.session_state.get("admin_authed"):
    # Autofocuses the password field on load, since it's the only
    # thing to do on this screen - saves a click before typing.
    # Runs in components.v1.html's own sandboxed iframe, so it has
    # to reach back into the real page via window.parent. Scoped
    # to this field's own aria-label (not just input[type=password]
    # generally) and wrapped in try/catch: if the browser ever
    # blocks parent-frame access or the selector doesn't match,
    # this silently does nothing - the user just clicks the field
    # manually, exactly like before this change. It never blocks
    # or alters form submission either way.
    st.components.v1.html(
        """
        <script>
        try {
            setTimeout(function () {
                const input = window.parent.document.querySelector(
                    'input[aria-label="Password"][type="password"]'
                );
                if (input) { input.focus(); }
            }, 150);
        } catch (e) {}
        </script>
        """,
        height=0,
    )
    # st.form (rather than a bare st.button) is what lets pressing
    # Enter submit, not just clicking - form_submit_button still
    # submits exactly once per click/Enter, same single-submission
    # behavior as the original bare st.button; nothing about the
    # submit-and-check-password logic below changed.
    with st.form("admin_login_form"):
        password = st.text_input("Password", type="password", key="admin_password")
        submitted = st.form_submit_button("Enter")
    if submitted:
        admin_password = get_admin_password()
        if admin_password is None:
            st.error("Admin password unavailable (couldn't reach Secrets Manager).")
        elif password == admin_password:
            st.session_state["admin_authed"] = True
            st.rerun()
        else:
            st.error("Incorrect password.")
else:
    tab_overview, tab_req2prod, tab_models, tab_cto_cockpit = st.tabs(
        ["Jobfinder Admin", "Req2Prod", "AI Models", "CTO Cockpit"]
    )

    with tab_overview:
        render_overview_tab(UNLIMITED_USER)
    with tab_req2prod:
        # Ordered the way a change actually travels: describe it, watch it
        # ship, then read how the machinery works.
        # Labels come from req2prod/admin_ui.py, not a literal here: the
        # "View it in the Pipeline" button finds its tab in the rendered
        # DOM by this exact text, so a rename has to move both at once.
        sub_requirements, sub_pipeline, sub_documentation = st.tabs(
            list(REQ2PROD_TAB_LABELS)
        )
        with sub_requirements:
            render_requirements_tab()
        with sub_pipeline:
            render_req2prod_pipeline_tab()
        with sub_documentation:
            render_documentation_tab()
    with tab_models:
        render_ai_models_tab()
    with tab_cto_cockpit:
        sub_architecture, sub_connectivity, sub_cost = st.tabs(
            ["Architecture", "Connectivity", "Cost"]
        )
        with sub_architecture:
            render_architecture_tab()
        with sub_connectivity:
            render_connectivity_tab()
        with sub_cost:
            render_cost_tab()
