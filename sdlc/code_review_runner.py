"""
Driver for the "PR code review" GitHub Actions workflow - calls
review_code() from sdlc/SDLC.py and submits the result as an actual
PR review (approve or request changes).

This runs as a distinct reviewer identity (REVIEWER_BOT_TOKEN, the
"MarcoAIagent" account), not the workflow's default GITHUB_TOKEN -
GitHub does not let a pull request's author satisfy its own required
review, so the review has to come from a genuinely different account.

Review/fix loop is capped at MAX_CHANGE_REQUESTS rounds: once
code_reviewer has already requested changes that many times on this
PR, the next run approves regardless of remaining findings (listing
them for the record) rather than requesting changes again - this keeps
a slow-converging review from blocking the PR forever. Any approval,
whether earned outright or granted under the cap, is followed by an
automatic squash-merge to the base branch.

Reads its parameters from environment variables:
    DIFF_FILE          - path to a file containing the PR's diff
    PR_NUMBER          - the pull request number to review
    REPO               - "owner/repo"
    REVIEWER_BOT_TOKEN - the reviewer identity's own token

Exit code is 0 if a review was successfully submitted and, on
approval, successfully merged - a request-changes review is what
actually blocks the merge under branch protection, so failing this
script's exit code for that case would be redundant and would also
make the Actions run itself look broken rather than "working as
intended." Exits nonzero if the crew produced no result, if
review_code timed out, or if submitting the review or the merge to
GitHub itself failed (e.g. auth or network error) - those are genuine
failures, not verdicts.
"""

import json
import os
import signal
import subprocess
import sys

from sdlc.SDLC import review_code

REVIEW_TIMEOUT_SECONDS = 600
REVIEWER_BOT_USERNAME = "MarcoAIagent"
MAX_CHANGE_REQUESTS = 2


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _sanitize_markdown_line(text: str) -> str:
    """Collapse to a single line and drop backticks so a finding can't break
    out of its `code span` or inject extra markdown list items/headers."""
    return " ".join(text.replace("`", "'").split())


def _format_findings(findings) -> str:
    lines = []
    for finding in findings:
        location = f"{finding.file}:{finding.line}" if finding.line else finding.file
        location = _sanitize_markdown_line(location)
        risk = _sanitize_markdown_line(finding.risk)
        lines.append(f"- `{location}` - {risk}")
    return "\n".join(lines)


class _ReviewTimeout(Exception):
    pass


def _raise_timeout(signum, frame):
    raise _ReviewTimeout(f"code_reviewer did not finish within {REVIEW_TIMEOUT_SECONDS}s")


def _bot_env(bot_token: str) -> dict:
    """Minimal, explicitly allowlisted environment for gh calls made as the
    reviewer bot - only what gh itself needs, so no other secret in
    os.environ (present or added later) can reach the subprocess."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GH_TOKEN": bot_token,
    }


def _count_bot_change_requests(pr_number: str, repo: str, bot_token: str) -> int:
    """How many times REVIEWER_BOT_USERNAME has already requested changes on
    this PR - used to cap the review/fix loop so it can't stall forever."""
    proc = subprocess.run(
        ["gh", "api", f"repos/{repo}/pulls/{pr_number}/reviews"],
        env=_bot_env(bot_token),
        capture_output=True,
        text=True,
        check=True,
    )
    reviews = json.loads(proc.stdout)
    return sum(
        1
        for r in reviews
        if r.get("user", {}).get("login") == REVIEWER_BOT_USERNAME
        and r.get("state") == "CHANGES_REQUESTED"
    )


def _submit_review(pr_number: str, repo: str, bot_token: str, *, approve: bool, body: str) -> bool:
    """Submit the review via gh. Returns True if gh successfully posted it."""
    flag = "--approve" if approve else "--request-changes"
    proc = subprocess.run(
        ["gh", "pr", "review", pr_number, "--repo", repo, flag, "--body", body],
        env=_bot_env(bot_token),
    )
    if proc.returncode != 0:
        print(f"::error::gh pr review exited {proc.returncode} - review was not submitted")
        return False
    return True


def _merge_pr(pr_number: str, repo: str, bot_token: str) -> bool:
    """Squash-merge the PR into its base branch. Returns True on success."""
    proc = subprocess.run(
        ["gh", "pr", "merge", pr_number, "--repo", repo, "--squash"],
        env=_bot_env(bot_token),
    )
    if proc.returncode != 0:
        print(f"::error::gh pr merge exited {proc.returncode} - PR was approved but not merged")
        return False
    return True


def _approve_and_merge(pr_number: str, repo: str, bot_token: str, body: str) -> int:
    if not _submit_review(pr_number, repo, bot_token, approve=True, body=body):
        return 1
    print("Approved.")
    if not _merge_pr(pr_number, repo, bot_token):
        return 1
    print("Merged.")
    return 0


def main() -> int:
    diff_file = _require("DIFF_FILE")
    pr_number = _require("PR_NUMBER")
    repo = _require("REPO")
    bot_token = _require("REVIEWER_BOT_TOKEN")

    with open(diff_file) as f:
        diff = f.read()
    if not diff.strip():
        print("Empty diff - nothing to review.")
        return 0

    print("=== Running code_reviewer ===")
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(REVIEW_TIMEOUT_SECONDS)
    try:
        result = review_code(diff)
    except _ReviewTimeout as exc:
        print(f"::error::{exc}")
        return 1
    finally:
        signal.alarm(0)
    print(result)

    if result is None:
        print("::error::code_reviewer crew produced no result")
        return 1

    if result.passed:
        body = "**Automated review by code_reviewer:** no issues found."
        return _approve_and_merge(pr_number, repo, bot_token, body)

    prior_change_requests = _count_bot_change_requests(pr_number, repo, bot_token)
    if prior_change_requests >= MAX_CHANGE_REQUESTS:
        body = (
            f"**Automated review by code_reviewer:** findings remain after "
            f"{prior_change_requests} review round(s), which reaches the "
            f"{MAX_CHANGE_REQUESTS}-round cap - approving so this PR isn't blocked "
            "indefinitely. Remaining findings for the record:\n\n"
            + _format_findings(result.findings)
        )
        return _approve_and_merge(pr_number, repo, bot_token, body)

    body = (
        "**Automated review by code_reviewer** found issues that need addressing:\n\n"
        + _format_findings(result.findings)
    )
    if not _submit_review(pr_number, repo, bot_token, approve=False, body=body):
        return 1
    print("Requested changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
