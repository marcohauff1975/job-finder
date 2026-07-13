"""
Live-ish view of Req2Prod agent activity: the deploy job's prod_tester/
rollback_agent, and devops_agent's own auto-fix runs. Shows the most
recent run of each agent-driving workflow, with per-step status
(queued/in_progress/completed) refreshed live, plus the actual CrewAI
trace (agent/task/tool-call/final-answer output) for that job once
it's available.

GitHub's own API can't stream a job's log while any step in that job
is still running (confirmed directly: `gh run view --log` explicitly
refuses ("still in progress; logs will be available when it is
complete") and the REST log-download endpoint 404s with
BlobNotFound) - so only step-level status is genuinely live; the
detailed trace only becomes available once the *whole job* finishes,
not the moment the specific agent-running step within it ends.

The trace is found by content (CrewAI's own distinctive "Agent
Started" box), not by step name - a step's declared name (e.g. "Run
devops_agent") and its actual shell command in the log (e.g. `python
-m req2prod.devops_agent_runner`) aren't textually related, so name-based
matching isn't reliable.

Read-only, needs GITHUB_ACTIONS_TOKEN (same .env pattern as
req2prod_pr_flow.py); no crewai/Anthropic import, so it stays out of the
production process's dependency footprint.
"""

import os
import re

import requests
import streamlit as st

GITHUB_REPO = "marcohauff1975/job-finder"

# Workflow file -> the job within it whose steps/log we show.
AGENT_WORKFLOWS = {
    "req2prod-pipeline.yml": "deploy",
    "devops-agent.yml": "respond",
}


def _gh_get(path: str, token: str, params: dict | None = None):
    url = f"https://api.github.com/repos/{GITHUB_REPO}{path}"
    try:
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            params=params,
            timeout=10,
        )
        response.raise_for_status()
        return response.json(), True
    except (requests.RequestException, ValueError):
        return None, False


def _fetch_job_log(job_id: int, token: str) -> str | None:
    """Full plain-text log for a job - only ever succeeds once every
    step in that job has finished (GitHub archives logs at the job
    level, not per-step)."""
    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/jobs/{job_id}/logs",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=15,
        )
        response.raise_for_status()
        response.encoding = "utf-8"
        return response.text
    except requests.RequestException:
        return None


_TIMESTAMP_RE = re.compile(r"^\S+Z ?")
_BORDER_CHARS = "─│╭╮╰╯"


def _clean_trace_line(line: str) -> str:
    line = _TIMESTAMP_RE.sub("", line)
    return line.strip(_BORDER_CHARS + " ")


_CREWAI_MARKER = "Agent Started"


def _find_crewai_trace(full_log: str) -> str | None:
    """The cleaned log content of whichever `##[group]Run ...` segment
    actually contains CrewAI's own verbose output - found by content
    (the distinctive "🤖 Agent Started" box), not by step name, since a
    step's declared name and its actual shell command (e.g. "Run
    devops_agent" the step vs. `python -m req2prod.devops_agent_runner` the
    command) aren't textually related. None if no segment has one
    (e.g. the step that runs the crew was skipped)."""
    lines = full_log.splitlines()
    group_starts = [i for i, l in enumerate(lines) if "##[group]Run " in l]
    if not group_starts:
        return None

    for idx, start in enumerate(group_starts):
        end = group_starts[idx + 1] if idx + 1 < len(group_starts) else len(lines)
        segment = lines[start + 1 : end]
        marker_at = next((i for i, l in enumerate(segment) if _CREWAI_MARKER in l), None)
        if marker_at is not None:
            # Drops the shell/env-var preamble before the crew actually
            # started - not useful noise for this view.
            cleaned = [_clean_trace_line(l) for l in segment[marker_at:]]
            cleaned = [l for l in cleaned if l.strip()]
            return "\n".join(cleaned) if cleaned else None
    return None


@st.cache_data(ttl=8)
def get_agent_activity() -> tuple[list[dict], str | None]:
    """Returns (runs, error) - one entry per AGENT_WORKFLOWS file, for
    its single most recent run (whichever is currently in progress, or
    the last one if none is), with its steps (name/status/conclusion)
    for live status, and a job-level "trace" (the actual CrewAI
    agent/task/tool-call output) once the whole job has finished.
    error is None, "no_token", or "unreachable"."""
    token = os.getenv("GITHUB_ACTIONS_TOKEN")
    if not token:
        return [], "no_token"

    results = []
    for workflow, job_name in AGENT_WORKFLOWS.items():
        data, ok = _gh_get(f"/actions/workflows/{workflow}/runs", token, params={"per_page": 3})
        if not ok:
            return [], "unreachable"
        runs = (data or {}).get("workflow_runs", [])
        if not runs:
            continue
        run = next((r for r in runs if r.get("status") != "completed"), runs[0])

        jobs_data, ok2 = _gh_get(f"/actions/runs/{run['id']}/jobs", token)
        if not ok2:
            continue
        job = next(
            (j for j in (jobs_data or {}).get("jobs", []) if j["name"] == job_name),
            None,
        )
        if job is None:
            continue

        steps_out = [
            {"name": s["name"], "status": s["status"], "conclusion": s.get("conclusion")}
            for s in job.get("steps", [])
        ]

        trace = None
        if job.get("status") == "completed":
            log_text = _fetch_job_log(job["id"], token)
            if log_text:
                trace = _find_crewai_trace(log_text)

        results.append(
            {
                "workflow": workflow,
                "run_id": run["id"],
                "run_url": run["html_url"],
                "run_status": run.get("status"),
                "run_conclusion": run.get("conclusion"),
                "job_name": job_name,
                "steps": steps_out,
                "trace": trace,
            }
        )

    return results, None
