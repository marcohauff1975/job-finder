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
import time
from datetime import date
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from auth import AuthManager
from reporting import get_report, record_cv_generated

ADMIN_PASSWORD = "REDACTED-ROTATED"
from job_search import (
    FORMAT_TEMPLATES,
    build_tailored_docx_bytes,
    extract_resume_content,
    find_jobs,
    render_resume_in_format,
    research_company,
    review_resume,
    tailor_resume_for_job,
)

FORMAT_PREVIEWS_DIR = Path(__file__).parent / "assets" / "format_previews"

load_dotenv()

USERS_DIR = Path(__file__).parent / "users"
DAILY_SEARCH_LIMIT = 5


def _check_search_quota(user_dir: Path) -> tuple[bool, int]:
    """Returns (allowed, searches_used_today) without incrementing anything.
    Usage is tracked per-user in usage.json and resets automatically when
    the date changes, so no separate cleanup job is needed."""
    usage_path = user_dir / "usage.json"
    today = date.today().isoformat()
    usage = {}
    if usage_path.exists():
        try:
            usage = json.loads(usage_path.read_text())
        except (json.JSONDecodeError, OSError):
            usage = {}
    if usage.get("date") != today:
        return True, 0
    return usage.get("count", 0) < DAILY_SEARCH_LIMIT, usage.get("count", 0)


def _increment_search_quota(user_dir: Path) -> None:
    """Records one more search against today's count, resetting the
    counter first if the stored usage is from a previous day."""
    usage_path = user_dir / "usage.json"
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
            if password == ADMIN_PASSWORD:
                st.session_state["admin_authed"] = True
                st.rerun()
            else:
                st.error("Incorrect password.")
    else:
        report = get_report()
        st.metric("Registered users", report["registered_users"])
        st.metric("CVs generated", report["cvs_total"])
        st.caption(
            f"{report['cvs_tailored']} tailored for a specific job, "
            f"{report['cvs_format']} format rebuilds."
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
       wraps to. */
    div[data-testid="stHorizontalBlock"] {
        align-items: stretch;
    }
    div[data-testid="stHorizontalBlock"] > div {
        display: flex;
        flex-direction: column;
    }
    div[data-testid="stHorizontalBlock"] > div > div {
        display: flex;
        flex-direction: column;
        flex: 1;
    }
    div[data-testid="stHorizontalBlock"] > div > div > div:has(.stButton) {
        margin-top: auto;
    }
    </style>

    <div class="hero-badge">✨ Powered by AI agents</div>
    <div class="hero-title">Job Finder</div>
    <div class="hero-tagline">AI-powered job search, company research, and resume tailoring — built to land you the interview.</div>
    """,
    unsafe_allow_html=True,
)

auth = AuthManager()
if not auth.render_login_or_register():
    st.stop()

username = auth.username
user_dir = USERS_DIR / username
user_dir.mkdir(parents=True, exist_ok=True)
resume_path = user_dir / "resume.docx"
resume_name_path = user_dir / "resume_original_name.txt"
history_dir = user_dir / "history"

if not resume_path.exists():
    st.subheader("Upload your resume")
    st.write("Upload a .docx resume - this is what will be tailored for each job you search.")
    uploaded = st.file_uploader("Resume (.docx)", type=["docx"])
    if uploaded is not None:
        resume_path.write_bytes(uploaded.getvalue())
        resume_name_path.write_text(uploaded.name)
        st.success("Resume uploaded.")
        st.rerun()
    st.stop()

resume_display_name = (
    resume_name_path.read_text().strip() if resume_name_path.exists() else resume_path.name
)

with st.expander(f"Resume on file: {resume_display_name} (click to replace)"):
    replacement = st.file_uploader("Upload a new resume (.docx)", type=["docx"], key="replace_resume")
    if replacement is not None:
        resume_path.write_bytes(replacement.getvalue())
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

    format_cols = st.columns(len(FORMAT_TEMPLATES))
    for col, (template_key, meta) in zip(format_cols, FORMAT_TEMPLATES.items()):
        with col:
            preview_path = FORMAT_PREVIEWS_DIR / f"{template_key}.jpg"
            if preview_path.exists():
                st.image(str(preview_path), use_container_width=True)
            st.markdown(f"**{meta['label']}**")
            st.caption(meta["description"])

            if st.button("Prepare download", key=f"fmt_prep_{template_key}"):
                with st.spinner("Rebuilding your resume in this format..."):
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
    role = st.text_input("Role", value="", placeholder="e.g. CTO")
    location = st.text_input("Location", value="", placeholder="e.g. Amsterdam, Netherlands")
    remote = st.checkbox("Open to fully remote roles", value=True)

    if st.button("Search for jobs"):
        if not role.strip() or not location.strip():
            st.error("Please fill in both Role and Location before searching.")
        else:
            allowed, _ = _check_search_quota(user_dir)
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
                        postings = find_jobs(role, location, remote, history_dir)
                    _increment_search_quota(user_dir)
                    st.session_state["postings"] = postings
                    st.session_state["role"] = role

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
                research = None
                tailored = None
                docx_bytes = None
                error = None

                try:
                    with st.spinner(f"Researching {job['company']}..."):
                        research = _run_with_retry(
                            research_company, job["company"], st.session_state.get("role", role)
                        )
                except Exception as e:
                    error = f"Company research failed: {e}"

                if research is not None and error is None:
                    try:
                        with st.spinner("Tailoring your resume for this role..."):
                            tailored = _run_with_retry(
                                tailor_resume_for_job, job, research, resume_path
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
