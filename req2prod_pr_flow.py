"""
Live flowchart of the most recently active pull request's real journey
through the Req2Prod pipeline: Merge Request -> Code Review (one box per
real review round, looping back on "changes requested") -> Push to
Master once merged -> Push to Prod -> Test in Prod, branching down to
Invoke DevOps Agent if the deploy/test fails. Read-only, needs
GITHUB_ACTIONS_TOKEN in the environment (same .env pattern as
SERPER_API_KEY); no crewai/Anthropic import, so it stays out of the
production process's dependency footprint.

Code review and deploy are now one combined GitHub Actions workflow
(.github/workflows/req2prod-pipeline.yml) - code_review job on
pull_request, deploy job on push to main (i.e. right after a merge) -
so this diagram can follow a single PR all the way through production
instead of stopping at the merge, the way it used to when the two were
separate, manually-triggered pipelines.
"""

import os
from html import escape

import requests
import streamlit as st

from req2prod_agent_backend_mode import get_agent_backend

GITHUB_REPO = "marcohauff1975/job-finder"
PIPELINE_WORKFLOW_FILE = "req2prod-pipeline.yml"
DEVOPS_AGENT_WORKFLOW_FILE = "devops-agent.yml"
# The job inside PIPELINE_WORKFLOW_FILE whose steps this view reports on. It
# shares a run with resolve_backend and code_review, so it has to be found by
# name - see _deploy_stages.
DEPLOY_JOB_NAME = "deploy"

# Both identify a persona's activity from real GitHub data, since
# pr_fix_agent and pr_arbiter don't have their own GitHub identity -
# pr_fix_agent only ever leaves a commit (never a review), and
# pr_arbiter posts under the same MarcoAIagent bot account
# code_reviewer uses (see req2prod/code_review_runner.py, which writes
# both of these prefixes verbatim).
PR_FIX_AGENT_COMMIT_PREFIX = "[pr-fix-agent]"
PR_ARBITER_BODY_PREFIX = "**Secondary review by pr_arbiter:**"

STAGE_COLORS = {
    "ok": "#22d3ee",  # cyan - succeeded
    "changes_requested": "#f59e0b",  # amber - code review needs rework (loops back to Merge Request)
    "blocked": "#f59e0b",  # amber - pr_arbiter decided not to merge (Marco notified, no auto-retry loop)
    "failed": "#f59e0b",  # amber - deploy/test failed (branches down to DevOps Agent, no loop back)
    "running": "#8b5cf6",  # violet - in progress right now
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


def _truncate(text: str, limit: int = 130) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit].rstrip() + "…"


_KNOWN_BODY_PREFIXES = (
    "**Automated review by code_reviewer:**",
    PR_ARBITER_BODY_PREFIX,
)


def _summarize_review(body: str) -> str:
    """Review bodies are a markdown bullet list of findings - the first
    bullet is the most representative one-line summary. Falls back to
    the first line of plain text if there are no bullets (e.g. a plain
    approval body). Strips this script's own known bold-text lead-ins
    first (e.g. "**Secondary review by pr_arbiter:**") - the box already
    shows who's speaking via its agent label, and raw "**" markers don't
    render as bold in an SVG <text> element, just literal asterisks."""
    for prefix in _KNOWN_BODY_PREFIXES:
        if body.startswith(prefix):
            body = body[len(prefix):].strip()
            break
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("- "):
            return _truncate(line[2:])
    return _truncate(body) if body.strip() else "No comments."


def _step(steps: list[dict], name_substr: str) -> dict | None:
    for s in steps:
        if name_substr.lower() in (s.get("name") or "").lower():
            return s
    return None


def _deploy_stages(pr: dict, token: str) -> list[dict]:
    """Returns the Push to Prod / Test in Prod / Invoke DevOps Agent
    stages for the deploy triggered by this PR's actual merge commit -
    empty if the PR isn't merged yet, or that merge hasn't triggered a
    deploy run yet."""
    merge_sha = pr.get("merge_commit_sha")
    if not merge_sha:
        return []

    runs, ok = _gh_get(
        f"/actions/workflows/{PIPELINE_WORKFLOW_FILE}/runs", token, params={"per_page": 20, "event": "push"}
    )
    if not ok:
        return []
    deploy_run = next(
        (r for r in (runs or {}).get("workflow_runs", []) if r.get("head_sha") == merge_sha), None
    )
    if deploy_run is None:
        return []

    jobs_data, ok2 = _gh_get(f"/actions/runs/{deploy_run['id']}/jobs", token)
    if not ok2:
        return []
    jobs = (jobs_data or {}).get("jobs", [])
    # By name, not jobs[0]: this run has three jobs and the API returns
    # resolve_backend first (then deploy, then a skipped code_review). Reading
    # jobs[0] meant every step lookup below searched resolve_backend's three
    # steps, none of which is "Pull latest main on the server" - so pull_step
    # was always None and every completed deploy rendered amber "Deploy step
    # didn't complete.", regardless of what the deploy actually did. It was
    # never reading the deploy at all.
    deploy_job = next((j for j in jobs if j.get("name") == DEPLOY_JOB_NAME), None)
    run_completed = deploy_run.get("status") == "completed"
    if deploy_job is not None and deploy_job.get("conclusion") == "skipped":
        # AUTO_DEPLOY_ON_MERGE is off, so merging deliberately doesn't deploy.
        # That's the configured behaviour, not a failure - show no deploy
        # stages at all rather than inventing one that then reads as broken.
        return []
    if deploy_job is None and run_completed:
        return []
    # deploy `needs: resolve_backend`, so on a run that's only just started the
    # deploy job may not exist yet. No steps means the lookups below fall
    # through to "Deploying now…", which is exactly right at that point.
    steps = deploy_job.get("steps", []) if deploy_job else []
    run_url = deploy_run.get("html_url", pr["html_url"])

    stages: list[dict] = []

    restart_step = _step(steps, "Restart jobfinder.service")
    pull_step = _step(steps, "Pull latest main on the server")
    if pull_step is None:
        push_state = "running" if not run_completed else "failed"
        push_summary = "Deploy running now…" if not run_completed else "Deploy step didn't complete."
    elif restart_step is not None and restart_step.get("conclusion") == "success":
        push_state = "ok"
        push_summary = "Pulled latest main and restarted jobfinder.service."
    elif restart_step is not None and restart_step.get("conclusion") == "skipped":
        push_state = "ok"
        push_summary = "Pulled latest main - no restart needed (no code changes)."
    elif not run_completed:
        push_state = "running"
        push_summary = "Deploying now…"
    else:
        push_state = "failed"
        push_summary = "Restart step failed - see run logs."
    stages.append(
        {
            "kind": "push_to_prod",
            "label": "Push to Prod",
            "agent": "deploy pipeline",
            "state": push_state,
            "summary": push_summary,
            "url": run_url,
        }
    )

    test_step = _step(steps, "Run prod_tester")
    if test_step is not None:
        test_conclusion = test_step.get("conclusion")
        if test_step.get("status") != "completed":
            test_state, test_summary = "running", "prod_tester is checking production now…"
        elif test_conclusion == "success":
            test_state, test_summary = "ok", "Production verified healthy after deploy."
        else:
            test_state, test_summary = "failed", "Smoke test failed - see run logs."
        stages.append(
            {
                "kind": "test_in_prod",
                "label": "Test in Prod",
                "agent": "prod_tester",
                "state": test_state,
                "summary": test_summary,
                "url": run_url,
            }
        )

        if test_state == "failed":
            devops_runs, ok3 = _gh_get(
                f"/actions/workflows/{DEVOPS_AGENT_WORKFLOW_FILE}/runs", token, params={"per_page": 10}
            )
            devops_run = None
            if ok3:
                devops_run = next(
                    (r for r in (devops_runs or {}).get("workflow_runs", []) if r.get("head_sha") == merge_sha),
                    None,
                )
            if devops_run is not None:
                if devops_run.get("status") != "completed":
                    devops_state, devops_summary = "running", "devops_agent is diagnosing the failure now…"
                elif devops_run.get("conclusion") == "success":
                    devops_state, devops_summary = "ok", "Applied a fix, pushed it, and re-triggered the deploy."
                else:
                    devops_state, devops_summary = "failed", "Auto-fix attempt failed - needs a human look."
                stages.append(
                    {
                        "kind": "devops_agent",
                        "label": "Invoke DevOps Agent",
                        "agent": "devops_agent",
                        "state": devops_state,
                        "summary": devops_summary,
                        "url": devops_run.get("html_url", run_url),
                    }
                )

    return stages


@st.cache_data(ttl=15)
def get_subscription_stall() -> dict | None:
    """Is the pipeline currently unable to run, because the machine it depends
    on isn't there? Returns {"runner": name|None, "queued": int} when so, else
    None.

    AGENT_BACKEND=subscription routes code_review and deploy onto the
    self-hosted runner, which is Marco's laptop. GitHub cannot push work to it:
    the runner holds an outbound connection and *asks* for jobs, so a closed
    laptop means nothing is asking and the job simply sits in the queue. It
    resumes by itself when the Mac comes back and polls again - GitHub only
    gives up after 24h.

    Nothing surfaced that. The flow on the Pipeline tab is built from posted PR
    reviews, so a queued job produces no Code Review box at all: the page shows
    the pull request and then stops, which reads as finished, or broken, rather
    than as waiting. That is this function's whole reason to exist - the state
    is benign and self-healing, and looked like neither.

    Returns None whenever it cannot tell (no token, API unreachable, variable
    unreadable). A banner claiming the Mac is down because a token is missing
    would be worse than no banner.
    """
    if get_agent_backend() != "subscription":
        return None

    token = os.getenv("GITHUB_ACTIONS_TOKEN")
    if not token:
        return None

    runners, ok = _gh_get("/actions/runners", token)
    if not ok or not isinstance(runners, dict):
        return None

    listed = runners.get("runners", [])
    if any(runner.get("status") == "online" for runner in listed):
        return None

    # Named from the registration even while offline, so the banner can say
    # *which* machine rather than "a runner".
    name = listed[0].get("name") if listed else None

    queued, ok = _gh_get("/actions/runs", token, params={"status": "queued", "per_page": 20})
    waiting = queued.get("total_count", 0) if ok and isinstance(queued, dict) else 0
    return {"runner": name, "queued": waiting}


@st.cache_data(ttl=15)
def get_latest_pr_flow() -> tuple[dict | None, list[dict], str | None]:
    """Returns (pr_info, stages, error). pr_info is {number, title, url,
    author, created_at} for the most recently active PR (open or
    merged); stages is the ordered, real sequence of boxes to draw:
    Merge Request, one Code Review box per actual review round (or a
    "running" box if a round is mid-flight), Push to Master once
    merged, then Push to Prod / Test in Prod / Invoke DevOps Agent
    (only once each has actually run) if that merge has triggered a
    deploy. error is None, "no_token", or "unreachable". Cached 15s so
    a 10s auto-refresh mostly reuses one fetch instead of hitting
    GitHub on every poll."""
    token = os.getenv("GITHUB_ACTIONS_TOKEN")
    if not token:
        return None, [], "no_token"

    pr_list, ok = _gh_get(
        "/pulls", token, params={"state": "all", "sort": "updated", "direction": "desc", "per_page": 10}
    )
    if not ok:
        return None, [], "unreachable"
    if not pr_list:
        return None, [], None

    # Skip a PR that was closed without merging (e.g. an abandoned
    # duplicate) - it never went through the real pipeline, so showing
    # it would look like a review that mysteriously never finished.
    candidates = [p for p in pr_list if p["state"] == "open" or p.get("merged_at")]
    if not candidates:
        return None, [], None
    number = candidates[0]["number"]
    pr, ok = _gh_get(f"/pulls/{number}", token)
    reviews, ok2 = _gh_get(f"/pulls/{number}/reviews", token)
    runs, ok3 = _gh_get(
        f"/actions/workflows/{PIPELINE_WORKFLOW_FILE}/runs",
        token,
        params={"per_page": 20, "event": "pull_request"},
    )
    # pr_fix_agent never posts a review of its own (see req2prod/
    # code_review_runner.py) - a commit is the only real trace it left
    # anything, so the actual PR commit list is what tells this diagram
    # a fix round happened at all.
    commits, ok4 = _gh_get(f"/pulls/{number}/commits", token)
    if not (ok and ok2 and ok3 and ok4):
        return None, [], "unreachable"

    reviews = reviews or []
    commits = commits or []
    fix_agent_commits = {
        c["sha"]: (c.get("commit", {}).get("message") or "")
        for c in commits
        if (c.get("commit", {}).get("message") or "").startswith(PR_FIX_AGENT_COMMIT_PREFIX)
    }
    workflow_runs = [r for r in (runs or {}).get("workflow_runs", []) if r.get("head_branch") == pr["head"]["ref"]]
    workflow_runs.sort(key=lambda r: r.get("created_at", ""))
    reviews_sorted = sorted(reviews, key=lambda r: r.get("submitted_at", "") or "")

    pr_info = {
        "number": number,
        "title": pr["title"],
        "url": pr["html_url"],
        "author": pr["user"]["login"],
        "created_at": pr["created_at"],
    }

    stages = [
        {
            "kind": "merge_request",
            "label": f"Merge Request #{number}",
            "agent": pr_info["author"],
            "state": "ok",
            "summary": _truncate(pr_info["title"]),
            "url": pr_info["url"],
        }
    ]

    for i, review in enumerate(reviews_sorted):
        body = review.get("body", "") or ""
        bot_login = review.get("user", {}).get("login", "bot")

        # A review posted against a [pr-fix-agent] commit means that
        # commit is what got this round to a postable state - show it
        # as its own box immediately before the review it enabled, in
        # real chronological order.
        fix_message = fix_agent_commits.get(review.get("commit_id"))
        if fix_message is not None:
            stages.append(
                {
                    "kind": "pr_fix_agent",
                    "label": "PR Fix Agent",
                    "agent": "pr_fix_agent",
                    "state": "ok",
                    "summary": _truncate(
                        fix_message.removeprefix(PR_FIX_AGENT_COMMIT_PREFIX).strip()
                        or "Applied fixes for code_reviewer's findings."
                    ),
                    "url": review.get("html_url", pr_info["url"]),
                }
            )

        if body.startswith(PR_ARBITER_BODY_PREFIX):
            # pr_arbiter posts under the same bot identity code_reviewer
            # does (see req2prod/code_review_runner.py's docstring for why -
            # GitHub reviews have to come from one consistent account),
            # so only the body text distinguishes the two. A block here
            # is "blocked", never "changes_requested" - the PR is left
            # open with Marco notified, nothing will auto-retry it the
            # way a real rework round would.
            label = f"PR Arbiter (round {i + 1})"
            agent = f"pr_arbiter ({bot_login})"
            state = "blocked" if review.get("state") == "CHANGES_REQUESTED" else "ok"
        else:
            label = f"Code Review (round {i + 1})"
            agent = f"code_reviewer ({bot_login})"
            state = "changes_requested" if review.get("state") == "CHANGES_REQUESTED" else "ok"

        stages.append(
            {
                "kind": "code_review",
                "label": label,
                "agent": agent,
                "state": state,
                "summary": _summarize_review(body),
                "url": review.get("html_url", pr_info["url"]),
            }
        )

    if workflow_runs:
        latest_run = workflow_runs[-1]
        latest_reviewed_commit = reviews_sorted[-1]["commit_id"] if reviews_sorted else None
        if latest_run.get("status") != "completed" and latest_run.get("head_sha") != latest_reviewed_commit:
            stages.append(
                {
                    "kind": "code_review",
                    "label": f"Code Review (round {len(reviews_sorted) + 1})",
                    "agent": "code_reviewer",
                    "state": "running",
                    "summary": "Reviewing the latest changes now…",
                    "url": latest_run.get("html_url", pr_info["url"]),
                }
            )

    if pr.get("merged_at"):
        merged_by = (pr.get("merged_by") or {}).get("login", "auto-merge")
        commit_sha = (pr.get("merge_commit_sha") or "")[:7]
        stages.append(
            {
                "kind": "push_to_master",
                "label": "Push to Master",
                "agent": merged_by,
                "state": "ok",
                "summary": f"Merged into main at {commit_sha}" if commit_sha else "Merged into main.",
                "url": pr_info["url"],
            }
        )
        stages.extend(_deploy_stages(pr, token))

    return pr_info, stages, None


def _wrap(text: str, width: int = 24) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines[:4]


def render_pr_flow_svg(stages: list[dict]) -> str:
    """Builds the flowchart as a self-contained SVG string (inline
    attributes only, no <style> block - keeps streamlit_app.py's rule
    that all page-level CSS lives in exactly one place). Boxes run left
    to right in real chronological order. A "changes_requested" stage
    (code review rework) gets a dashed arrow looping back to the Merge
    Request box - each round in its own lane so multiple rounds don't
    overlap. A "devops_agent" stage is drawn separately, below the last
    box, connected by a plain down arrow - it's a branch to a new
    activity, not a loop back to the start, matching how a failed
    production test actually gets handled."""
    box_w, box_h, gap = 210, 80, 70
    top = 30
    desc_top = top + box_h + 26
    desc_line_h = 15
    max_desc_lines = 4
    desc_bottom = desc_top + (max_desc_lines - 1) * desc_line_h
    loop_lane_gap = 28  # vertical spacing between stacked rework loops, so multiple rounds don't overlap
    loop_y_base = desc_bottom + 30  # clear of the description text below the boxes

    main_stages = [s for s in stages if s["kind"] != "devops_agent"]
    devops_stage = next((s for s in stages if s["kind"] == "devops_agent"), None)

    n = len(main_stages)
    rework_count = sum(1 for s in main_stages if s["state"] == "changes_requested")
    width = max(n * box_w + (n - 1) * gap + 40, 400)
    content_bottom = loop_y_base + max(rework_count, 1) * loop_lane_gap

    devops_top = None
    if devops_stage is not None:
        devops_top = max(desc_bottom, content_bottom) + 50
        content_bottom = devops_top + box_h + 26 + (max_desc_lines - 1) * desc_line_h

    height = content_bottom + 24

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Inter, sans-serif">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#0a0a0f"/>',
        "<defs>"
        '<marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">'
        '<path d="M0,0 L8,4 L0,8 Z" fill="#8b5cf6"/></marker>'
        '<marker id="arrow-amber" markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto">'
        '<path d="M0,0 L8,4 L0,8 Z" fill="#f59e0b"/></marker>'
        "</defs>",
    ]

    def draw_box(x: float, y: float, stage: dict) -> None:
        color = STAGE_COLORS.get(stage["state"], "#8b5cf6")
        parts.append(
            f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="12" '
            f'fill="{color}" fill-opacity="0.14" stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x + box_w / 2}" y="{y + 30}" fill="#f1f5f9" font-size="14" '
            f'font-weight="700" text-anchor="middle">{escape(stage["label"])}</text>'
        )
        parts.append(
            f'<text x="{x + box_w / 2}" y="{y + 52}" fill="{color}" font-size="12" '
            f'text-anchor="middle">{escape(stage["agent"])}</text>'
        )
        if stage["state"] == "running":
            parts.append(
                f'<text x="{x + box_w / 2}" y="{y + 70}" fill="{color}" font-size="11" '
                f'text-anchor="middle">● running now</text>'
            )
        lines = _wrap(stage["summary"])
        desc_y = y + box_h + 26
        for j, line in enumerate(lines):
            parts.append(
                f'<text x="{x}" y="{desc_y + j * desc_line_h}" fill="#94a3b8" font-size="11">{escape(line)}</text>'
            )

    positions = []
    x = 20
    for stage in main_stages:
        positions.append((x, top))
        draw_box(x, top, stage)
        x += box_w + gap

    for i in range(n - 1):
        x1 = positions[i][0] + box_w
        x2 = positions[i + 1][0]
        yc = top + box_h / 2
        parts.append(
            f'<line x1="{x1}" y1="{yc}" x2="{x2 - 8}" y2="{yc}" stroke="#8b5cf6" '
            f'stroke-width="2" marker-end="url(#arrow)"/>'
        )

    mr_x = positions[0][0] + box_w / 2
    lane_index = 0
    for i, stage in enumerate(main_stages):
        if stage["state"] != "changes_requested":
            continue
        loop_y = loop_y_base + lane_index * loop_lane_gap
        lane_index += 1
        cr_x = positions[i][0] + box_w / 2
        cr_bottom = top + box_h
        path = (
            f"M {cr_x} {cr_bottom} "
            f"L {cr_x} {loop_y} "
            f"L {mr_x} {loop_y} "
            f"L {mr_x} {cr_bottom - 8}"
        )
        parts.append(
            f'<path d="{path}" fill="none" stroke="#f59e0b" stroke-width="2" '
            f'stroke-dasharray="5,4" marker-end="url(#arrow-amber)"/>'
        )
        parts.append(
            f'<text x="{(cr_x + mr_x) / 2}" y="{loop_y + 14}" fill="#f59e0b" font-size="11" '
            f'text-anchor="middle">if not ok - rework</text>'
        )

    if devops_stage is not None and devops_top is not None:
        last_x = positions[-1][0]
        devops_x = last_x
        draw_box(devops_x, devops_top, devops_stage)
        arrow_x = last_x + box_w / 2
        parts.append(
            f'<line x1="{arrow_x}" y1="{top + box_h}" x2="{arrow_x}" y2="{devops_top - 8}" '
            f'stroke="#f59e0b" stroke-width="2" stroke-dasharray="5,4" marker-end="url(#arrow-amber)"/>'
        )
        parts.append(
            f'<text x="{arrow_x + 10}" y="{(top + box_h + devops_top) / 2}" fill="#f59e0b" font-size="11">'
            f"if not ok</text>"
        )

    parts.append("</svg>")
    return "\n".join(parts)
