"""
Live flowchart of the most recently active pull request's real journey
through the SDLC "review" pipeline - Merge Request -> Code Review
(one box per real review round, looping back on "changes requested")
-> Push to Master once actually merged. Read-only, needs
GITHUB_ACTIONS_TOKEN in the environment (same .env pattern as
SERPER_API_KEY); no crewai/Anthropic import, so it stays out of the
production process's dependency footprint.

Deliberately stops at "Push to Master": the deploy side (Push to Prod,
Test in Prod, DevOps auto-fix) isn't tied to a specific PR today - it's
a separate, manually-triggered pipeline - so stitching it onto this
PR's diagram would invent a link that doesn't actually exist yet.
Revisit once the two pipelines are actually one.
"""

import os
from html import escape

import requests
import streamlit as st

GITHUB_REPO = "marcohauff1975/job-finder"
CODE_REVIEW_WORKFLOW_FILE = "pr-code-review.yml"

STAGE_COLORS = {
    "ok": "#22d3ee",  # cyan - succeeded
    "changes_requested": "#f59e0b",  # amber - needs rework
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


def _summarize_review(body: str) -> str:
    """Review bodies are a markdown bullet list of findings - the first
    bullet is the most representative one-line summary. Falls back to
    the first line of plain text if there are no bullets (e.g. a plain
    approval body)."""
    for line in body.splitlines():
        line = line.strip()
        if line.startswith("- "):
            return _truncate(line[2:])
    return _truncate(body) if body.strip() else "No comments."


@st.cache_data(ttl=15)
def get_latest_pr_flow() -> tuple[dict | None, list[dict], str | None]:
    """Returns (pr_info, stages, error). pr_info is {number, title, url,
    author, created_at} for the most recently active PR (open or
    merged); stages is the ordered, real sequence of boxes to draw for
    that PR - a Merge Request stage, one Code Review stage per actual
    review round (state "ok" or "changes_requested"), a "running" Code
    Review stage if the workflow is mid-run with no review posted yet,
    and a final Push to Master stage only if the PR is actually merged.
    error is None, "no_token", or "unreachable". Cached 15s so a 10s
    auto-refresh mostly reuses one fetch instead of hitting GitHub on
    every poll."""
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
        f"/actions/workflows/{CODE_REVIEW_WORKFLOW_FILE}/runs", token, params={"per_page": 20}
    )
    if not (ok and ok2 and ok3):
        return None, [], "unreachable"

    reviews = reviews or []
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
        state = "changes_requested" if review.get("state") == "CHANGES_REQUESTED" else "ok"
        stages.append(
            {
                "kind": "code_review",
                "label": f"Code Review (round {i + 1})",
                "agent": f"code_reviewer ({review.get('user', {}).get('login', 'bot')})",
                "state": state,
                "summary": _summarize_review(review.get("body", "")),
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
    that all page-level CSS lives in exactly one place). Boxes run
    left to right in real chronological order; any "changes_requested"
    stage gets a curved arrow looping back down to the Merge Request
    box, labeled "if not ok", mirroring the actual rework cycle."""
    box_w, box_h, gap = 210, 80, 70
    top = 30
    desc_top = top + box_h + 26
    desc_line_h = 15
    max_desc_lines = 4
    desc_bottom = desc_top + (max_desc_lines - 1) * desc_line_h
    loop_lane_gap = 28  # vertical spacing between stacked rework loops, so multiple rounds don't overlap
    loop_y_base = desc_bottom + 30  # clear of the description text below the boxes
    n = len(stages)
    rework_count = sum(1 for s in stages if s["state"] == "changes_requested")
    width = max(n * box_w + (n - 1) * gap + 40, 400)
    height = loop_y_base + max(rework_count, 1) * loop_lane_gap + 24

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

    positions = []
    x = 20
    for stage in stages:
        color = STAGE_COLORS.get(stage["state"], "#8b5cf6")
        positions.append((x, top))
        parts.append(
            f'<rect x="{x}" y="{top}" width="{box_w}" height="{box_h}" rx="12" '
            f'fill="{color}" fill-opacity="0.14" stroke="{color}" stroke-width="2"/>'
        )
        parts.append(
            f'<text x="{x + box_w / 2}" y="{top + 30}" fill="#f1f5f9" font-size="14" '
            f'font-weight="700" text-anchor="middle">{escape(stage["label"])}</text>'
        )
        parts.append(
            f'<text x="{x + box_w / 2}" y="{top + 52}" fill="{color}" font-size="12" '
            f'text-anchor="middle">{escape(stage["agent"])}</text>'
        )
        if stage["state"] == "running":
            parts.append(
                f'<text x="{x + box_w / 2}" y="{top + 70}" fill="{color}" font-size="11" '
                f'text-anchor="middle">● running now</text>'
            )
        x += box_w + gap

    for i in range(n - 1):
        x1 = positions[i][0] + box_w
        x2 = positions[i + 1][0]
        yc = top + box_h / 2
        parts.append(
            f'<line x1="{x1}" y1="{yc}" x2="{x2 - 8}" y2="{yc}" stroke="#8b5cf6" '
            f'stroke-width="2" marker-end="url(#arrow)"/>'
        )

    for i, stage in enumerate(stages):
        bx = positions[i][0]
        lines = _wrap(stage["summary"])
        for j, line in enumerate(lines):
            parts.append(
                f'<text x="{bx}" y="{desc_top + j * desc_line_h}" fill="#94a3b8" font-size="11">{escape(line)}</text>'
            )

    mr_x = positions[0][0] + box_w / 2
    lane_index = 0
    for i, stage in enumerate(stages):
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

    parts.append("</svg>")
    return "\n".join(parts)
