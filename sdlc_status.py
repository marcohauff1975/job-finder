"""
Read-only status feed for the SDLC pipeline dashboard (the admin
page's "SDLC Pipeline" tab) - pulls recent GitHub Actions run history
straight from GitHub's API so the dashboard reflects what actually
ran, not a separately-tracked copy that could drift. Only ever issues
GET requests, and imports nothing from crewai/sdlc/ - keeps the
production Streamlit process free of the CrewAI/Anthropic stack that
only ever runs inside GitHub Actions, never in this app.

Needs GITHUB_ACTIONS_TOKEN in the environment (same .env-based pattern
as SERPER_API_KEY in reporting.py) - a GitHub fine-grained PAT scoped
to this repo with read-only "Actions" permission, nothing broader.
"""

import os

import requests
import streamlit as st

GITHUB_REPO = "marcohauff1975/job-finder"

# The two pipelines described in streamlit_app.py's admin tab: PR code
# review, and push-to-production (both the plain and AI-driven
# variants, plus the auto-fix that responds to either failing) - shown
# as one merged timeline since they're meant to converge into a single
# pipeline over time.
PIPELINE_WORKFLOWS = [
    ("PR code review", "pr-code-review.yml"),
    ("Deploy to production", "deploy-to-prod.yml"),
    ("AI prod flow", "ai-prod-flow.yml"),
    ("DevOps auto-fix", "devops-agent.yml"),
]

STATUS_ICONS = {"success": "✅", "failure": "❌", "cancelled": "⚠️"}


def _fetch_runs(workflow_file: str, token: str, limit: int) -> tuple[list[dict], bool]:
    """Returns (runs, ok). ok is False only if the request itself
    failed (network error, bad auth, GitHub down) - a workflow that's
    simply never run yet still returns (ok=True, runs=[]), which is a
    different, non-error condition from the dashboard's point of view."""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/{workflow_file}/runs"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params={"per_page": limit},
            timeout=10,
        )
        response.raise_for_status()
        return response.json().get("workflow_runs", []), True
    except (requests.RequestException, ValueError):
        return [], False


@st.cache_data(ttl=60)
def get_pipeline_status(runs_per_workflow: int = 5) -> tuple[list[dict], str | None]:
    """Returns (events, error). events is a flat, time-sorted (newest
    first) list of recent runs across all four SDLC workflows, each as
    {stage, status, conclusion, run_url, started_at, title}. error is
    None on success, "no_token" if GITHUB_ACTIONS_TOKEN isn't set, or
    "unreachable" if every workflow's request failed (a real GitHub/auth
    problem) - kept distinct from a merely-empty result (a workflow that
    just hasn't run yet) so the admin tab can tell a configuration
    problem apart from "nothing to show". Cached 60s so the admin tab
    doesn't hit GitHub's API on every Streamlit rerun."""
    token = os.getenv("GITHUB_ACTIONS_TOKEN")
    if not token:
        return [], "no_token"

    events = []
    any_ok = False
    for stage_name, workflow_file in PIPELINE_WORKFLOWS:
        runs, ok = _fetch_runs(workflow_file, token, runs_per_workflow)
        any_ok = any_ok or ok
        for run in runs:
            events.append(
                {
                    "stage": stage_name,
                    "status": run.get("status"),
                    "conclusion": run.get("conclusion"),
                    "run_url": run.get("html_url"),
                    "started_at": run.get("run_started_at") or run.get("created_at") or "",
                    "title": run.get("display_title") or run.get("name") or "",
                }
            )

    if not any_ok:
        return [], "unreachable"

    events.sort(key=lambda e: e["started_at"], reverse=True)
    return events, None
