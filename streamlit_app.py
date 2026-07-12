"""
Multi-user browser UI for the Job Finder + Company Researcher + Resume
Tailor agents.

Run:
    streamlit run streamlit_app.py

This file only handles page layout and wiring - the actual work is
split into:
    auth.py        - registration, login, session handling
    job_search.py   - the CrewAI agents (Job Finder, Company Researcher,
                      Resume Tailor) and resume file handling

Once logged in, each user uploads their own resume, searches for jobs,
and gets a tailored resume back as a direct download - nothing is
saved to a shared folder on this machine, which is what will let this
work the same way once it's deployed for real (the AWS step, later).
Every user's uploaded resume and search history lives under
users/<their username>/.
"""

import json
import os
import re
import subprocess
import time
from datetime import date
from pathlib import Path

import boto3
import streamlit as st
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import load_dotenv

from auth import AuthManager, delete_user, set_user_password
from reporting import (
    VALID_TIERS,
    delete_user_data,
    get_report,
    get_serper_balance,
    get_user_tier,
    record_cv_generated,
    set_user_tier,
)
from job_search import (
    FORMAT_TEMPLATES,
    build_tailored_docx_bytes,
    extract_resume_content,
    find_jobs,
    load_tailored_resumes,
    render_resume_in_format,
    research_company,
    review_resume,
    save_resume_upload,
    save_tailored_resume,
    tailor_resume_for_job,
)
from ai_viewer import render_sidebar_toggle, setup_layout
from sdlc.SDLC import (
    ArchitectureDirectionResult,
    FeatureRequirementsResult,
    build_feature,
    challenge_requirement,
)
from sdlc.model_registry import (
    AGENT_DISPLAY_NAMES,
    MODEL_DISPLAY_NAMES,
    RECOMMENDATIONS,
    TECH_EXCELLENCE_AGENT_KEYS,
    load_agent_models,
    set_agent_model,
)
from sdlc.requirements_sessions import (
    delete_session,
    list_sessions,
    load_session,
    new_session_id,
    save_session,
)
from sdlc_agent_steps import get_agent_activity
from sdlc_deploy_mode import get_auto_deploy_mode, set_auto_deploy_mode
from sdlc_pr_flow import get_latest_pr_flow, render_pr_flow_svg

FORMAT_PREVIEWS_DIR = Path(__file__).parent / "assets" / "format_previews"

load_dotenv()

USERS_DIR = Path(__file__).parent / "users"
DAILY_SEARCH_LIMIT = 5
DAILY_RESEARCH_LIMIT = 1
UNLIMITED_USER = "marco.hauff@gmail.com"

ADMIN_SECRET_NAME = "job-finder/admin-password"
AWS_REGION = "eu-north-1"


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


def _get_app_version() -> str:
    """The commit this running process was actually started from - read
    once at import time, not on every rerun, so it reflects what code
    is really loaded (not just what's on disk), which is exactly what
    diverges when a deploy pulls new code but the service never
    restarts."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


APP_VERSION = _get_app_version()


def _check_daily_quota(usage_path: Path, limit: int) -> tuple[bool, int]:
    """Returns (allowed, uses_today) without incrementing anything. Usage
    is tracked in the given per-user JSON file and resets automatically
    when the date changes, so no separate cleanup job is needed."""
    today = date.today().isoformat()
    usage = {}
    if usage_path.exists():
        try:
            usage = json.loads(usage_path.read_text())
        except (json.JSONDecodeError, OSError):
            usage = {}
    if usage.get("date") != today:
        return True, 0
    return usage.get("count", 0) < limit, usage.get("count", 0)


def _increment_daily_quota(usage_path: Path) -> None:
    """Records one more use against today's count, resetting the counter
    first if the stored usage is from a previous day."""
    today = date.today().isoformat()
    usage = {"date": today, "count": 0}
    if usage_path.exists():
        try:
            existing = json.loads(usage_path.read_text())
            if existing.get("date") == today:
                usage = existing
        except (json.JSONDecodeError, OSError):
            pass
    usage["count"] = usage.get("count", 0) + 1
    usage_path.write_text(json.dumps(usage))


def _load_last_search(user_dir: Path) -> dict:
    """Returns the role/location/remote this user searched with last
    time, so a returning user doesn't have to retype them. Empty
    strings/defaults if they've never searched."""
    last_search_path = user_dir / "last_search.json"
    if last_search_path.exists():
        try:
            return json.loads(last_search_path.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"role": "", "location": "", "remote": True}


def _save_last_search(user_dir: Path, role: str, location: str, remote: bool) -> None:
    (user_dir / "last_search.json").write_text(
        json.dumps({"role": role, "location": location, "remote": remote})
    )


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _run_with_retry(func, *args, retries=1, **kwargs):
    """Run func(*args, **kwargs), retrying on failure (useful for flaky
    network/API calls). Re-raises the last error if all attempts fail."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(1)
    raise last_error


def _format_pm_result(result) -> str:
    """Renders a FeatureRequirementsResult (see sdlc/SDLC.py) as the
    Product Manager's chat bubble content."""
    lines = ["**Senior Product Manager**", "", f"**User story:** {result.user_story}"]
    lines.append("")
    lines.append("**Acceptance criteria:**")
    for criterion in result.acceptance_criteria:
        lines.append(f"- {criterion}")
    if result.open_questions:
        lines.append("")
        lines.append("**Open questions:**")
        for question in result.open_questions:
            lines.append(f"- {question}")
    return "\n".join(lines)


def _format_architect_result(result) -> str:
    """Renders an ArchitectureDirectionResult (see sdlc/SDLC.py) as the
    Software Architect's chat bubble content."""
    lines = ["**Senior Software Architect**", ""]
    lines.append(
        "Builds on the existing app as-is."
        if result.builds_on_existing_app
        else "Needs new infrastructure or a separate service."
    )
    if result.new_infrastructure_needed:
        lines.append("")
        lines.append("**New infrastructure needed:**")
        for item in result.new_infrastructure_needed:
            lines.append(f"- {item}")
    if result.non_functional_requirements:
        lines.append("")
        lines.append("**Non-functional requirements:**")
        for item in result.non_functional_requirements:
            lines.append(f"- {item}")
    lines.append("")
    lines.append(f"**Technical notes:** {result.technical_notes}")
    if result.clarifications_needed:
        lines.append("")
        lines.append("**Clarifications needed:**")
        for item in result.clarifications_needed:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _format_engineer_result(result) -> str:
    """Renders a FeatureBuildResult (see sdlc/SDLC.py) as the Software
    Engineer's chat bubble content."""
    lines = ["**Senior Software Engineer**", "", f"**Branch:** `{result.branch_name}`"]
    if result.pr_url:
        lines.append(f"**Pull request:** {result.pr_url}")
    lines.append("")
    lines.append(f"**Summary:** {result.summary}")
    if result.files_changed:
        lines.append("")
        lines.append("**Files changed:**")
        for path in result.files_changed:
            lines.append(f"- `{path}`")
    if result.questions_asked:
        lines.append("")
        lines.append("**Questions asked while building:**")
        for question in result.questions_asked:
            lines.append(f"- {question}")
    return "\n".join(lines)


def _latest_ready_pair(
    messages: list[dict],
) -> tuple[FeatureRequirementsResult, ArchitectureDirectionResult] | None:
    """If the conversation's last two messages are a product_manager +
    software_architect pair that both came back ready_for_development,
    reconstructs and returns their structured results (for the "Push to
    Software Engineer" button) - otherwise None. Requiring them to be
    the literal last two messages means the button naturally disappears
    once a newer message is appended after them, whether that's a fresh
    user follow-up (superseding this verdict) or the engineer's own
    build result (so the same requirements can't be pushed twice)."""
    if len(messages) < 2:
        return None
    pm_message, architect_message = messages[-2], messages[-1]
    if pm_message.get("role") != "product_manager" or architect_message.get("role") != "software_architect":
        return None
    pm_data, architect_data = pm_message.get("data"), architect_message.get("data")
    if pm_data is None or architect_data is None:
        return None
    pm_result = FeatureRequirementsResult(**pm_data)
    architect_result = ArchitectureDirectionResult(**architect_data)
    if not (pm_result.ready_for_development and architect_result.ready_for_development):
        return None
    return pm_result, architect_result


def _format_conversation_for_agents(messages: list[dict]) -> str:
    """Turns the saved chat history into one text block for the
    requirements-challenge crew's feature_request input - each turn is
    labeled by speaker, so the agents see exactly what was already said
    and can treat a later user message as a follow-up/answer rather
    than a brand new unrelated request."""
    speaker_labels = {
        "user": "Marco",
        "product_manager": "Product Manager",
        "software_architect": "Software Architect",
    }
    parts = [
        f"{speaker_labels.get(message['role'], message['role'])}: {message['content']}"
        for message in messages
    ]
    return "\n\n".join(parts)


def _render_requirements_challenge_page() -> None:
    """Admin-only sub-page where Marco types a raw feature idea and gets
    it challenged by the product_manager and software_architect agents
    (sdlc/SDLC.py's challenge_requirement) - chat layout modeled on
    Claude Code's own UI: message history on top, input pinned to the
    bottom, sessions managed from the sidebar. Each session is a JSON
    file under data/requirements_sessions/ (sdlc/requirements_sessions.py)."""
    with st.sidebar:
        st.markdown("### 💬 Request a New Feature")
        if st.button("+ New session", key="rc_new_session", use_container_width=True):
            st.session_state["rc_session_id"] = new_session_id()
            st.session_state["rc_messages"] = []
            st.rerun()
        st.markdown("---")
        st.caption("Recent sessions")
        sessions = list_sessions()
        if not sessions:
            st.caption("No sessions yet.")
        for session in sessions:
            is_active = session["id"] == st.session_state.get("rc_session_id")
            label = ("▶ " if is_active else "") + session["title"]
            session_col, delete_col = st.columns([5, 1])
            if session_col.button(label, key=f"rc_session_{session['id']}", use_container_width=True):
                st.session_state["rc_session_id"] = session["id"]
                loaded = load_session(session["id"])
                st.session_state["rc_messages"] = loaded["messages"] if loaded else []
                st.rerun()
            if delete_col.button("🗑️", key=f"rc_delete_{session['id']}", help="Delete this session"):
                delete_session(session["id"])
                if is_active:
                    st.session_state["rc_session_id"] = new_session_id()
                    st.session_state["rc_messages"] = []
                st.rerun()

    st.markdown(
        '<div class="hero-badge">✨ Powered by AI agents</div>'
        '<div class="hero-title" style="font-size:1.8rem;">Request a New Feature</div>'
        '<div class="hero-tagline">Describe a feature idea - the Senior Product Manager and '
        "Senior Software Architect agents will challenge it before a line of code gets written."
        "</div>",
        unsafe_allow_html=True,
    )

    current_deploy_mode = get_auto_deploy_mode()
    if current_deploy_mode is None:
        st.caption(
            "⚠️ Can't read the current deploy workflow mode - GITHUB_VARIABLES_TOKEN is "
            "either missing or doesn't have permission to read repo Actions variables."
        )
    else:
        # st.toggle's own cached state (via its key) persists across
        # reruns/page loads within a session, independent of value= -
        # value= only seeds it on first render. If AUTO_DEPLOY_ON_MERGE
        # changes through anything other than this exact toggle (the
        # desktop "Toggle Demo Mode" icon, editing the GitHub variable
        # directly), a stale cached state can silently disagree with
        # the freshly-fetched current_deploy_mode - and the mismatch
        # check below would then treat that staleness as if it were a
        # fresh user click, silently pushing the stale cached value
        # back to GitHub and reverting someone else's change. Observed
        # live (2026-07-12): the caption and the toggle disagreed after
        # an external change, and GitHub's real value had already been
        # overwritten back to the toggle's stale position. Resyncing
        # here, before the widget renders, whenever the external value
        # has moved since we last saw it, closes that gap.
        if st.session_state.get("_rc_deploy_mode_last_seen") != current_deploy_mode:
            st.session_state["rc_auto_deploy_toggle"] = current_deploy_mode
        st.session_state["_rc_deploy_mode_last_seen"] = current_deploy_mode

        st.caption(
            f"Current value: **{'ON' if current_deploy_mode else 'OFF'}** "
            f"(`AUTO_DEPLOY_ON_MERGE={'true' if current_deploy_mode else 'false'}` on GitHub)"
        )
        toggled_deploy_mode = st.toggle(
            "🚀 Automated deploy on merge",
            key="rc_auto_deploy_toggle",
            help="Same switch as the 'Toggle Demo Mode' desktop icon. Off (default): "
            "deploying to production after a merge still needs a manual 'Run workflow' "
            "click. On: merging a PR into main auto-deploys straight to production.",
        )
        if toggled_deploy_mode != current_deploy_mode:
            # Streamlit already reran once to deliver this toggle
            # interaction, and the widget's own state already reflects
            # the user's choice - no need to force a second rerun (and
            # a second get_auto_deploy_mode() API call) on success. Only
            # force one on failure, to snap the toggle back to the real
            # state instead of leaving it showing a value that was never
            # actually applied.
            if not set_auto_deploy_mode(toggled_deploy_mode):
                st.error(
                    "Couldn't update the deploy mode - check GITHUB_VARIABLES_TOKEN's "
                    "permissions."
                )
                st.session_state["rc_auto_deploy_toggle"] = current_deploy_mode
                st.session_state["_rc_deploy_mode_last_seen"] = current_deploy_mode
                st.rerun()
            else:
                st.session_state["_rc_deploy_mode_last_seen"] = toggled_deploy_mode

    messages = st.session_state.get("rc_messages", [])

    avatars = {
        "user": "🧑‍💼",
        "product_manager": "📋",
        "software_architect": "🏗️",
        "software_engineer": "👷",
    }
    for message in messages:
        role = "user" if message["role"] == "user" else "assistant"
        with st.chat_message(role, avatar=avatars.get(message["role"])):
            st.markdown(message["content"])

    ready_pair = _latest_ready_pair(messages)
    if ready_pair is not None:
        st.success("Both agents have confirmed this is ready for development.")
        if st.button("🚀 Push to Software Engineer", key="rc_push_to_engineer", type="primary"):
            pm_result, architect_result = ready_pair
            session_id = st.session_state["rc_session_id"]
            build_result = None
            error = None
            # TEMPORARY diagnostic logging (see [DIAG] markers below),
            # to be removed once the actual gap is found - PR #22 moved
            # save_session() inside this `with` block on the theory
            # that st.spinner()'s __exit__ was the only point a
            # disconnected client's BaseException could abort the
            # script before the result was saved. That fix is deployed
            # but a session was still observed on production where the
            # crew genuinely completed (per journalctl) yet
            # save_session() never ran - meaning that theory was
            # incomplete. These prints pin down how far execution gets
            # next time, logging only presence/shape, never the actual
            # build content (which can include user-submitted feature
            # requests and generated code) - journalctl is a broader-
            # access surface than this app's per-user file storage.
            print(f"[DIAG] rc_push_to_engineer clicked, session={session_id}", flush=True)
            with st.spinner("👷 Software Engineer is building this feature..."):
                try:
                    build_result = _run_with_retry(build_feature, pm_result, architect_result)
                    print(f"[DIAG] build_feature returned, got_result={build_result is not None}", flush=True)
                except Exception as e:
                    error = f"The build failed: {e}"
                    print(f"[DIAG] build_feature raised: {type(e).__name__}", flush=True)

                if error is not None or build_result is None:
                    messages.append(
                        {
                            "role": "software_engineer",
                            "content": f"⚠️ {error or 'Something went wrong and no build result was produced.'}",
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "software_engineer",
                            "content": _format_engineer_result(build_result),
                            "data": build_result.model_dump(),
                        }
                    )

                # save_session() (plain file I/O, no Streamlit API) now
                # runs before the st.session_state write, not after -
                # observed on production: even with the append/save code
                # already moved inside this `with` block (see the long
                # comment above), a run died between the "build_feature
                # returned" print and "save_session done" ever logging,
                # meaning st.session_state.__setitem__ itself - not just
                # st.spinner()'s __exit__ - can be where a disconnected
                # client's BaseException fires. save_session() has no
                # such risk since it never touches Streamlit at all.
                save_session(session_id, messages)
                print(f"[DIAG] save_session done, session={session_id}", flush=True)
                st.session_state["rc_messages"] = messages

            print("[DIAG] spinner block exited cleanly, about to st.rerun()", flush=True)
            st.rerun()

    prompt = st.chat_input("Describe a feature, or answer the open questions above...")
    if prompt:
        if not st.session_state.get("rc_session_id"):
            st.session_state["rc_session_id"] = new_session_id()
        session_id = st.session_state["rc_session_id"]

        messages = st.session_state.get("rc_messages", [])
        messages.append({"role": "user", "content": prompt})
        st.session_state["rc_messages"] = messages
        save_session(session_id, messages)

        result = None
        error = None
        # See the "Push to Software Engineer" handler above for why
        # save_session() runs inside this `with` block rather than after
        # it - st.spinner()'s __exit__ is where a disconnected client
        # would abort the script via a BaseException our `except
        # Exception` below can't catch, silently losing the result.
        # TEMPORARY [DIAG] prints, see that handler for why they log
        # only presence/shape and never actual user/agent content.
        print(f"[DIAG] challenge_requirement starting, session={session_id}", flush=True)
        with st.spinner("✨ Magic is happening, please wait..."):
            try:
                result = _run_with_retry(
                    challenge_requirement, _format_conversation_for_agents(messages)
                )
                print(f"[DIAG] challenge_requirement returned, got_result={result is not None}", flush=True)
            except Exception as e:
                error = f"The requirements challenge failed: {e}"
                print(f"[DIAG] challenge_requirement raised: {type(e).__name__}", flush=True)

            if error is not None or result is None:
                messages.append(
                    {
                        "role": "product_manager",
                        "content": f"⚠️ {error or 'Something went wrong and no response was produced.'} "
                        "Try rephrasing or resubmitting.",
                    }
                )
            else:
                pm_result, architect_result = result
                messages.append(
                    {
                        "role": "product_manager",
                        "content": _format_pm_result(pm_result),
                        "data": pm_result.model_dump(),
                    }
                )
                messages.append(
                    {
                        "role": "software_architect",
                        "content": _format_architect_result(architect_result),
                        "data": architect_result.model_dump(),
                    }
                )

            # See the "Push to Software Engineer" handler above for why
            # save_session() now runs before the st.session_state write.
            save_session(session_id, messages)
            print(f"[DIAG] save_session done, session={session_id}", flush=True)
            st.session_state["rc_messages"] = messages

        print("[DIAG] spinner block exited cleanly, about to st.rerun()", flush=True)
        st.rerun()


def _render_agent_model_table(agent_keys: list[str], widget_key_prefix: str) -> None:
    """Renders one editable Agent/Current/Recommended/Why/New-model table
    for the given agent_keys (see AGENT_DISPLAY_NAMES in
    sdlc/model_registry.py) on the admin "AI Models" tab, with its own
    save button. widget_key_prefix keeps this group's Streamlit widget
    keys distinct from any other group rendered on the same page."""
    current_models = load_agent_models()
    display_to_model_id = {label: model_id for model_id, label in MODEL_DISPLAY_NAMES.items()}

    rows = []
    for agent_key in agent_keys:
        recommended_id, rationale = RECOMMENDATIONS[agent_key]
        current_label = MODEL_DISPLAY_NAMES[current_models[agent_key]]
        rows.append(
            {
                "Agent": AGENT_DISPLAY_NAMES[agent_key],
                "Current model": current_label,
                "Recommended": MODEL_DISPLAY_NAMES[recommended_id],
                "New model": current_label,
                "Why": rationale,
            }
        )

    edited_rows = st.data_editor(
        rows,
        column_config={
            "Why": st.column_config.TextColumn(width="large"),
            "New model": st.column_config.SelectboxColumn(
                options=list(MODEL_DISPLAY_NAMES.values()), required=True
            ),
        },
        disabled=["Agent", "Current model", "Recommended", "Why"],
        use_container_width=True,
        hide_index=True,
        key=f"{widget_key_prefix}_editor",
    )

    if st.button("Save model changes", key=f"{widget_key_prefix}_save"):
        changed = 0
        for agent_key, edited in zip(agent_keys, edited_rows):
            new_model_id = display_to_model_id[edited["New model"]]
            if new_model_id != current_models[agent_key]:
                set_agent_model(agent_key, new_model_id)
                changed += 1
        if changed:
            st.success(f"Updated {changed} agent(s) - takes effect immediately, no restart needed.")
            st.rerun()
        else:
            st.info("No changes to save.")


st.set_page_config(
    page_title="Job Finder — AI-Powered Job Search",
    page_icon="✨",
    layout="centered",
)

if st.query_params.get("admin") is not None:
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
        tab_overview, tab_sdlc, tab_requirements, tab_models = st.tabs(
            ["Overview", "SDLC Pipeline", "Request a New Feature", "AI Models"]
        )

        with tab_requirements:
            _render_requirements_challenge_page()

        with tab_overview:
            report = get_report()
            st.metric("Registered users", report["registered_users"])
            st.metric("CVs generated", report["cvs_total"])
            st.caption(
                f"{report['cvs_tailored']} tailored for a specific job, "
                f"{report['cvs_format']} format rebuilds."
            )

            serper_balance = get_serper_balance()
            st.metric(
                "Serper credits remaining",
                serper_balance if serper_balance is not None else "unavailable",
            )

            st.markdown("#### Per user")
            st.caption(
                "Tier controls which Claude model each user's agents use - see "
                "job_search.py's TIER_HIGH_MODEL_AGENTS. Test accounts (@example.com) "
                "always run on free regardless of what's set here."
            )
            edited_rows = st.data_editor(
                [
                    {
                        "Email": row["email"],
                        "Tailored for a job": row["tailored"],
                        "Format rebuilds": row["format"],
                        "Total": row["total"],
                        "Tier": row["tier"],
                    }
                    for row in report["per_user"]
                ],
                column_config={
                    "Tier": st.column_config.SelectboxColumn(
                        options=list(VALID_TIERS), required=True
                    ),
                },
                disabled=["Email", "Tailored for a job", "Format rebuilds", "Total"],
                use_container_width=True,
                hide_index=True,
                key="tier_editor",
            )
            if st.button("Save tier changes"):
                for row in edited_rows:
                    set_user_tier(row["Email"], row["Tier"])
                st.success("Tiers saved.")
                st.rerun()

            user_emails = [row["email"] for row in report["per_user"]]

            st.markdown("#### Reset a user's password")
            with st.form("admin_reset_password_form"):
                reset_email = st.selectbox("User", user_emails, key="reset_pw_user")
                new_password = st.text_input(
                    "New password", type="password", key="reset_pw_value"
                )
                if st.form_submit_button("Reset password"):
                    if len(new_password) < 4:
                        st.error("Password must be at least 4 characters.")
                    else:
                        set_user_password(reset_email, new_password)
                        st.success(f"Password reset for {reset_email}.")

            st.markdown("#### Delete a user")
            st.caption(
                "Removes the account and all their data (resume, tailored resumes, "
                "search history) - this can't be undone."
            )
            with st.form("admin_delete_user_form"):
                delete_email = st.selectbox("User", user_emails, key="delete_user_select")
                confirm_email = st.text_input(
                    "Type the user's email to confirm deletion", key="delete_confirm"
                )
                if st.form_submit_button("Delete user"):
                    if delete_email == UNLIMITED_USER:
                        st.error("Can't delete the admin account.")
                    elif confirm_email != delete_email:
                        st.error("Confirmation email doesn't match - user not deleted.")
                    else:
                        delete_user(delete_email)
                        delete_user_data(delete_email)
                        st.success(f"Deleted {delete_email} and all their data.")
                        st.rerun()

        with tab_sdlc:
            st.caption(
                "Live view of the most recently active pull request's real "
                "journey through the review pipeline - Merge Request, each "
                "actual Code Review round, looping back on rework, and Push "
                "to Master once merged. Stops there: the deploy pipeline "
                "isn't tied to a specific PR yet, so it isn't stitched on. "
                "Read-only: nothing here can trigger, cancel, or re-run "
                "anything."
            )
            # Not auto-polling on a timer: every st.tabs() panel's code runs
            # on every script rerun regardless of which tab is visually
            # active (Streamlit doesn't skip hidden tabs), so a timer armed
            # here would keep ticking client-side even while a different
            # tab (e.g. Request a New Feature) is mid-crew-call, and firing
            # it there forces Streamlit to cancel that run before it can
            # save its result - exactly what happened in production. A
            # manual button avoids that risk entirely.
            if st.button("🔄 Refresh", key="sdlc_flow_refresh"):
                st.rerun()

            try:
                pr_info, stages, flow_error = get_latest_pr_flow()
            except Exception:
                pr_info, stages, flow_error = None, [], "error"

            if flow_error == "no_token":
                st.info("GITHUB_ACTIONS_TOKEN isn't configured - set it in .env to enable this tab.")
            elif flow_error in ("unreachable", "error"):
                st.warning("Couldn't reach the GitHub API just now - it'll retry automatically.")
            elif not stages:
                st.info("No pull requests found yet.")
            else:
                st.markdown(f"**#{pr_info['number']}** — {pr_info['title']} ([view PR]({pr_info['url']}))")
                st.markdown(render_pr_flow_svg(stages), unsafe_allow_html=True)

            st.markdown("#### Live SDLC agent activity")
            st.caption(
                "Real-time step status for prod_tester/rollback_agent (inside "
                "the deploy job) and devops_agent's own auto-fix runs. GitHub "
                "can't stream a job's log while it's still running, so the "
                "step list below is genuinely live, but each job's full agent "
                "trace (what it actually reasoned/did) only appears once that "
                "job finishes."
            )
            try:
                agent_runs, agent_error = get_agent_activity()
            except Exception:
                agent_runs, agent_error = [], "error"

            if agent_error == "no_token":
                st.info("GITHUB_ACTIONS_TOKEN isn't configured - set it in .env to enable this.")
            elif agent_error in ("unreachable", "error"):
                st.warning("Couldn't reach the GitHub API just now - it'll retry automatically.")
            elif not agent_runs:
                st.info("No agent runs found yet.")
            else:
                step_icons = {"completed": "✅", "in_progress": "🔄", "queued": "⏳"}
                for run in agent_runs:
                    conclusion_suffix = f", {run['run_conclusion']}" if run["run_conclusion"] else ""
                    st.markdown(
                        f"**{run['workflow']}** — [run #{run['run_id']}]({run['run_url']}) "
                        f"({run['run_status']}{conclusion_suffix})"
                    )
                    for step in run["steps"]:
                        icon = step_icons.get(step["status"], "•")
                        if step["conclusion"] == "failure":
                            icon = "❌"
                        st.caption(f"{icon} {step['name']}")
                    if run.get("trace"):
                        with st.expander("Agent trace"):
                            st.code(run["trace"], language=None)
                    st.markdown("---")

        with tab_models:
            st.caption(
                "Every SDLC agent's currently assigned Claude model, a "
                "recommendation for best performance based on that "
                "agent's actual stakes and judgment load (see "
                "sdlc/model_registry.py), and a control to change it. "
                "Changes apply immediately to this running app and are "
                "saved so they survive the next restart."
            )

            app_agent_keys = [
                key for key in AGENT_DISPLAY_NAMES if key not in TECH_EXCELLENCE_AGENT_KEYS
            ]

            st.markdown("#### SDLC pipeline agents")
            st.caption("Called by this app itself, as part of its own SDLC pipeline.")
            _render_agent_model_table(app_agent_keys, "app_agents")

            st.divider()

            st.markdown("#### Technology Excellence panel")
            st.caption(
                "Only ever invoked from a Claude Code session running the "
                "pre-publish readiness review (sdlc/SDLC.py's "
                "technology_excellence_crew) - never called by this "
                "deployed app itself."
            )
            _render_agent_model_table(TECH_EXCELLENCE_AGENT_KEYS, "tech_excellence_agents")
    st.stop()

st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&display=swap');

    html, body, [class*="css"] {
        font-family: 'Inter', sans-serif;
    }

    .hero-badge {
        display: inline-block;
        padding: 0.3rem 0.9rem;
        border-radius: 999px;
        background: linear-gradient(90deg, rgba(139,92,246,0.15), rgba(34,211,238,0.15));
        border: 1px solid rgba(139,92,246,0.4);
        color: #c4b5fd;
        font-size: 0.8rem;
        font-weight: 600;
        letter-spacing: 0.02em;
        margin-bottom: 0.75rem;
    }

    .hero-title {
        font-size: 2.6rem;
        font-weight: 800;
        line-height: 1.1;
        background: linear-gradient(90deg, #a78bfa, #22d3ee);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
        margin-bottom: 0.4rem;
    }

    .hero-tagline {
        color: #94a3b8;
        font-size: 1.05rem;
        margin-bottom: 1.5rem;
    }

    div.stButton > button, div.stDownloadButton > button {
        background: linear-gradient(90deg, #8b5cf6, #22d3ee);
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 600;
        padding: 0.6rem 1.4rem;
        box-shadow: 0 0 20px rgba(139, 92, 246, 0.35);
        transition: box-shadow 0.2s ease, transform 0.2s ease;
    }
    div.stButton > button:hover, div.stDownloadButton > button:hover {
        box-shadow: 0 0 28px rgba(34, 211, 238, 0.55);
        transform: translateY(-1px);
    }

    div[data-testid="stRadio"] label p {
        color: #94a3b8 !important;
    }

    div[data-testid="stAlert"]:has([data-testid$="Info"]) {
        background-color: rgba(139, 92, 246, 0.12) !important;
        border: 1px solid rgba(139, 92, 246, 0.35) !important;
    }
    div[data-testid="stAlert"]:has([data-testid$="Info"]) * {
        color: #c4b5fd !important;
        fill: #a78bfa !important;
    }

    /* Keep the "Prepare download" buttons aligned across the resume
       format columns, regardless of how many lines each description
       wraps to. Scoped to .st-key-format_picker_columns only - this
       used to be a bare div[data-testid="stHorizontalBlock"] selector,
       which also caught every other use of st.columns() in the app
       (e.g. pushed the AI viewer's "Clear log" button to the bottom
       of its column against the much taller main column). */
    .st-key-format_picker_columns div[data-testid="stHorizontalBlock"] {
        align-items: stretch;
    }
    .st-key-format_picker_columns div[data-testid="stHorizontalBlock"] > div {
        display: flex;
        flex-direction: column;
    }
    .st-key-format_picker_columns div[data-testid="stHorizontalBlock"] > div > div {
        display: flex;
        flex-direction: column;
        flex: 1;
    }
    .st-key-format_picker_columns div[data-testid="stHorizontalBlock"] > div > div > div:has(.stButton) {
        margin-top: auto;
    }

    /* Streamlit's chat_input textarea defaults to overflow-x: auto,
       which some browsers (observed in Safari) render as a persistent
       thin horizontal scrollbar even though the box only ever needs to
       grow vertically - there's nothing to scroll sideways. */
    textarea[data-testid="stChatInputTextArea"] {
        overflow-x: hidden;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

HERO_BADGE_HTML = '<div class="hero-badge">✨ Powered by AI agents</div>'

# Shown full-width above everything on the login screen, and above
# everything once logged in - EXCEPT in AI viewer mode, where "Job
# Finder" instead becomes the left window's own title (see
# setup_layout()) so it sits next to "AI viewer mode" as two separate
# panels, rather than one shared header floating above a divide.
if not (st.session_state.get("authentication_status") and st.session_state.get("ai_viewer_mode")):
    st.markdown(
        f"""
        {HERO_BADGE_HTML}
        <div class="hero-title">Job Finder</div>
        <div class="hero-tagline">AI-powered job search, company research, and resume tailoring — built to land you the interview.</div>
        """,
        unsafe_allow_html=True,
    )

auth = AuthManager()
if not auth.render_login_or_register():
    st.stop()

username = auth.username
user_tier = get_user_tier(username)
user_dir = USERS_DIR / username
user_dir.mkdir(parents=True, exist_ok=True)
resume_path = user_dir / "resume.docx"
resume_name_path = user_dir / "resume_original_name.txt"
history_dir = user_dir / "history"


render_sidebar_toggle()
main_col = setup_layout()

with main_col:
    if st.session_state.get("ai_viewer_mode"):
        st.markdown(
            f'{HERO_BADGE_HTML}<div class="hero-title" style="font-size:1.8rem;">Job Finder</div>',
            unsafe_allow_html=True,
        )

    if not resume_path.exists():
        st.subheader("Upload your resume")
        st.write("Upload a .docx or .pdf resume - this is what will be tailored for each job you search.")
        uploaded = st.file_uploader("Resume (.docx or .pdf)", type=["docx", "pdf"])
        if uploaded is not None:
            save_resume_upload(uploaded.name, uploaded.getvalue(), resume_path)
            resume_name_path.write_text(uploaded.name)
            st.success("Resume uploaded.")
            st.rerun()
        st.stop()

    resume_display_name = (
        resume_name_path.read_text().strip() if resume_name_path.exists() else resume_path.name
    )

    with st.expander(f"Resume on file: {resume_display_name} (click to replace)"):
        replacement = st.file_uploader(
            "Upload a new resume (.docx or .pdf)", type=["docx", "pdf"], key="replace_resume"
        )
        if replacement is not None:
            save_resume_upload(replacement.name, replacement.getvalue(), resume_path)
            resume_name_path.write_text(replacement.name)
            # The old review/extracted content no longer matches this file,
            # so drop anything cached from the previous resume.
            st.session_state.pop("resume_review", None)
            st.session_state.pop("resume_content", None)
            for key in FORMAT_TEMPLATES:
                st.session_state.pop(f"fmt_bytes_{key}", None)
            st.success("Resume replaced.")
            st.rerun()

    st.session_state.setdefault("view", "main")

    if st.session_state["view"] == "format":
        if st.button("← Back"):
            st.session_state["view"] = "main"
            st.rerun()

        st.markdown("### Change resume format")
        st.write("Pick a layout to rebuild and download your resume in.")

        with st.container(key="format_picker_columns"):
            format_cols = st.columns(len(FORMAT_TEMPLATES))
            for col, (template_key, meta) in zip(format_cols, FORMAT_TEMPLATES.items()):
                with col:
                    preview_path = FORMAT_PREVIEWS_DIR / f"{template_key}.jpg"
                    if preview_path.exists():
                        st.image(str(preview_path), use_container_width=True)
                    st.markdown(f"**{meta['label']}**")
                    st.caption(meta["description"])

                    if st.button("Prepare download", key=f"fmt_prep_{template_key}"):
                        with st.spinner("✨ Magic is happening, please wait..."):
                            content = st.session_state.get("resume_content")
                            if content is None:
                                content = extract_resume_content(resume_path)
                                st.session_state["resume_content"] = content
                            if content is not None:
                                st.session_state[f"fmt_bytes_{template_key}"] = render_resume_in_format(
                                    content, template_key
                                )
                                record_cv_generated(username, "format")
                            else:
                                st.error("Couldn't extract your resume's content. Try again.")

                    format_bytes = st.session_state.get(f"fmt_bytes_{template_key}")
                    if format_bytes:
                        st.download_button(
                            "Download",
                            data=format_bytes,
                            file_name=f"{_slugify(username)}_{template_key}.docx",
                            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key=f"fmt_dl_{template_key}",
                            use_container_width=True,
                        )
                        if st.button(
                            "Upload",
                            key=f"fmt_use_{template_key}",
                            use_container_width=True,
                        ):
                            resume_path.write_bytes(format_bytes)
                            resume_name_path.write_text(f"{meta['label']}.docx")
                            st.session_state.pop("resume_review", None)
                            st.session_state.pop("resume_content", None)
                            for key in FORMAT_TEMPLATES:
                                st.session_state.pop(f"fmt_bytes_{key}", None)
                            st.session_state["view"] = "main"
                            st.success(f"Saved - {meta['label']} is now your resume on file.")
                            st.rerun()

    else:
        st.markdown("### Review your resume (Optional)")
        st.write("Get honest, general feedback on your resume - not tied to any specific job.")

        review_col, format_col = st.columns(2)
        with review_col:
            if st.button("Review my resume", use_container_width=True):
                with st.spinner("✨ Magic is happening, please wait..."):
                    st.session_state["resume_review"] = review_resume(resume_path)
        with format_col:
            if st.button("Change resume format", use_container_width=True):
                st.session_state["view"] = "format"
                st.rerun()

        review = st.session_state.get("resume_review")
        if review is not None:
            with st.expander("Resume review", expanded=True):
                if review.strengths:
                    st.markdown("**Strengths**")
                    for item in review.strengths:
                        st.markdown(f"- {item}")
                if review.weaknesses:
                    st.markdown("**Weaknesses**")
                    for item in review.weaknesses:
                        st.markdown(f"- {item}")
                if review.suggestions:
                    st.markdown("**Suggestions**")
                    for item in review.suggestions:
                        st.markdown(f"- {item}")

        st.markdown("### Search for jobs")
        last_search = _load_last_search(user_dir)
        role = st.text_input("Role", value=last_search["role"], placeholder="e.g. CTO")
        location = st.text_input(
            "Location", value=last_search["location"], placeholder="e.g. Amsterdam, Netherlands"
        )
        remote = st.checkbox("Open to fully remote roles", value=last_search["remote"])

        if st.button("Search for jobs"):
            if not role.strip() or not location.strip():
                st.error("Please fill in both Role and Location before searching.")
            else:
                allowed, _ = _check_daily_quota(user_dir / "usage.json", DAILY_SEARCH_LIMIT)
                if not allowed:
                    st.error(
                        f"Free tier is limited to {DAILY_SEARCH_LIMIT} searches a day. "
                        "Contact support for the paid version: marco.hauff@gmail.com"
                    )
                else:
                    missing = [
                        key for key in ("ANTHROPIC_API_KEY", "SERPER_API_KEY")
                        if not os.getenv(key)
                    ]
                    if missing:
                        st.error(
                            f"Missing environment variable(s): {', '.join(missing)}. "
                            "Add them to your .env file."
                        )
                    else:
                        with st.spinner("✨ Magic is happening, please wait..."):
                            postings = find_jobs(role, location, remote, history_dir, user_tier)
                        _increment_daily_quota(user_dir / "usage.json")
                        _save_last_search(user_dir, role, location, remote)
                        st.session_state["postings"] = postings
                        st.session_state["role"] = role

        tailored_resumes = load_tailored_resumes(user_dir)
        with st.expander(f"Tailored resumes ({len(tailored_resumes)})"):
            if not tailored_resumes:
                st.info("You haven't tailored a resume for a specific job yet.")
            else:
                resumes_dir = user_dir / "tailored_resumes"
                for entry in tailored_resumes:
                    generated_on = entry["generated_at"][:10]
                    st.markdown(
                        f"**{entry['title']}** — {entry['company']} ({entry['location']})  \n"
                        f"Tailored on {generated_on}"
                    )
                    dl_col, changes_col = st.columns(2)
                    with dl_col:
                        docx_path = resumes_dir / entry["docx_filename"]
                        if docx_path.exists():
                            st.download_button(
                                "Download tailored resume (.docx)",
                                data=docx_path.read_bytes(),
                                file_name=entry["docx_filename"],
                                mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                key=f"history_dl_{entry['id']}",
                                use_container_width=True,
                            )
                    with changes_col:
                        changes_path = resumes_dir / entry["changes_filename"]
                        if changes_path.exists():
                            st.download_button(
                                "Download changes summary (.txt)",
                                data=changes_path.read_bytes(),
                                file_name=entry["changes_filename"],
                                mime="text/plain",
                                key=f"history_changes_{entry['id']}",
                                use_container_width=True,
                            )
                    st.markdown("---")

        postings = st.session_state.get("postings", [])

        if "job_results" not in st.session_state:
            st.session_state["job_results"] = {}

        if postings:
            st.markdown(f"### Found {len(postings)} role(s)")

            for i, job in enumerate(postings):
                job_key = job["link"] or f"{job['title']}|{job['company']}"

                badge = "  🆕 **NEW**" if job["is_new"] else ""
                salary_line = f"  \n💰 {job['salary']}" if job["salary"] else ""
                st.markdown(
                    f"**{job['title']}** — {job['company']} ({job['location']}){badge}"
                    f"{salary_line}  \n[View posting]({job['link']})"
                )

                if st.button("Do Market Research and Update Resume", key=f"research_btn_{i}"):
                    research_usage_path = user_dir / "research_usage.json"
                    allowed = username == UNLIMITED_USER or _check_daily_quota(
                        research_usage_path, DAILY_RESEARCH_LIMIT
                    )[0]
                    if not allowed:
                        st.error(
                            f"Free tier is limited to {DAILY_RESEARCH_LIMIT} resume "
                            "tailoring run(s) a day. Contact marco.hauff@gmail.com to "
                            "increase your frequency, or ask about the paid subscription."
                        )
                    else:
                        if username != UNLIMITED_USER:
                            _increment_daily_quota(research_usage_path)

                        research = None
                        tailored = None
                        docx_bytes = None
                        error = None

                        try:
                            with st.spinner(f"Researching {job['company']}..."):
                                research = _run_with_retry(
                                    research_company,
                                    job["company"],
                                    st.session_state.get("role", role),
                                    user_tier,
                                )
                        except Exception as e:
                            error = f"Company research failed: {e}"

                        if research is not None and error is None:
                            try:
                                with st.spinner("✨ Magic is happening, please wait..."):
                                    tailored = _run_with_retry(
                                        tailor_resume_for_job, job, research, resume_path, user_tier
                                    )
                            except FileNotFoundError as e:
                                error = str(e)
                            except Exception as e:
                                error = f"Resume tailoring failed: {e}"

                        if tailored is not None and error is None:
                            try:
                                docx_bytes = build_tailored_docx_bytes(
                                    resume_path, tailored.tailored_paragraphs
                                )
                                record_cv_generated(username, "tailored")
                                save_tailored_resume(
                                    user_dir, job, tailored.changes_summary, docx_bytes
                                )
                            except Exception as e:
                                error = f"Building the tailored resume failed: {e}"

                        st.session_state["job_results"][job_key] = {
                            "research": research,
                            "tailored": tailored,
                            "docx_bytes": docx_bytes,
                            "error": error,
                        }

                result = st.session_state["job_results"].get(job_key)
                if result:
                    with st.expander(f"Market research & tailored resume — {job['company']}", expanded=True):
                        if result["error"]:
                            st.error(result["error"])

                        research = result["research"]
                        if research is None:
                            st.warning("No structured research result returned.")
                        else:
                            st.markdown(f"**Overview:** {research.overview}")
                            if research.size:
                                st.markdown(f"**Size:** {research.size}")
                            if research.funding:
                                st.markdown(f"**Funding:** {research.funding}")
                            if research.reputation:
                                st.markdown(f"**Reputation:** {research.reputation}")
                            if research.other_open_roles:
                                st.markdown(f"**Other open roles:** {research.other_open_roles}")
                            if research.tech_stack:
                                st.markdown(f"**Tech stack:** {research.tech_stack}")
                            if research.recent_news:
                                st.markdown(f"**Recent news:** {research.recent_news}")

                        tailored = result["tailored"]
                        if tailored is not None:
                            st.markdown("---")
                            st.markdown("**What was changed:**")
                            st.markdown(tailored.changes_summary)
                            with st.expander("View tailored resume text"):
                                st.text("\n".join(tailored.tailored_paragraphs))

                            if result["docx_bytes"]:
                                st.download_button(
                                    "Download tailored resume (.docx)",
                                    data=result["docx_bytes"],
                                    file_name=f"{_slugify(job['company'])}_tailored_resume.docx",
                                    mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                                    key=f"download_{i}",
                                )
                                st.download_button(
                                    "Download changes summary (.txt)",
                                    data=tailored.changes_summary,
                                    file_name=f"{_slugify(job['company'])}_changes.txt",
                                    mime="text/plain",
                                    key=f"download_changes_{i}",
                                )

                st.markdown("---")

        elif "postings" in st.session_state:
            st.info("No postings found for this search.")

        st.markdown(
            f'<p style="color:#64748b; font-size:0.8rem; text-align:center; margin-top:2rem;">'
            f"v{APP_VERSION}</p>",
            unsafe_allow_html=True,
        )
