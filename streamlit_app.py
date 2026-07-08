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
    get_estimated_anthropic_cost,
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
from sdlc_status import STATUS_ICONS, get_pipeline_status

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


st.set_page_config(
    page_title="Job Finder — AI-Powered Job Search",
    page_icon="✨",
    layout="centered",
)

if st.query_params.get("admin") is not None:
    if not st.session_state.get("admin_authed"):
        password = st.text_input("Password", type="password", key="admin_password")
        if st.button("Enter"):
            admin_password = get_admin_password()
            if admin_password is None:
                st.error("Admin password unavailable (couldn't reach Secrets Manager).")
            elif password == admin_password:
                st.session_state["admin_authed"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    else:
        tab_overview, tab_sdlc = st.tabs(["Overview", "SDLC Pipeline"])

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

            st.metric("Anthropic spend (estimated)", f"${get_estimated_anthropic_cost():.2f}")
            st.caption(
                "Estimated from actual token usage × published Sonnet pricing - "
                "Anthropic doesn't expose a real balance to a regular API key."
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
                "Recent runs across the SDLC workflows - PR review, both "
                "production-deploy variants, and the DevOps auto-fix - merged "
                "into one timeline (today's two separate pipelines, meant to "
                "converge into one over time). Read-only: nothing here can "
                "trigger, cancel, or re-run a workflow."
            )
            if st.button("Refresh", key="sdlc_refresh"):
                get_pipeline_status.clear()
                st.rerun()

            events = get_pipeline_status()
            if not events:
                st.info(
                    "No pipeline data available - either GITHUB_ACTIONS_TOKEN "
                    "isn't configured, or GitHub couldn't be reached."
                )
            else:
                for event in events:
                    icon = (
                        "🔄"
                        if event["status"] != "completed"
                        else STATUS_ICONS.get(event["conclusion"], "❓")
                    )
                    st.markdown(
                        f"{icon} **{event['stage']}** — {event['title']}  \n"
                        f"<span style='color:gray;font-size:0.85em'>"
                        f"{event['started_at']} · "
                        f"<a href='{event['run_url']}'>view run</a></span>",
                        unsafe_allow_html=True,
                    )
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
