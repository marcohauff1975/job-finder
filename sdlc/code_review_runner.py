"""
Driver for the "PR code review" GitHub Actions workflow - calls
review_code() from sdlc/SDLC.py and submits the result as an actual
PR review (approve or request changes).

This runs as a distinct reviewer identity (REVIEWER_BOT_TOKEN, the
"MarcoAIagent" account), not the workflow's default GITHUB_TOKEN -
GitHub does not let a pull request's author satisfy its own required
review, so the review has to come from a genuinely different account.

Reads its parameters from environment variables:
    DIFF_FILE          - path to a file containing the PR's diff
    PR_NUMBER          - the pull request number to review
    REPO               - "owner/repo"
    REVIEWER_BOT_TOKEN - the reviewer identity's own token

Exit code is 0 if a review was successfully submitted (whether it
approved or requested changes) - a request-changes review is what
actually blocks the merge under branch protection, so failing this
script's exit code for that case would be redundant and would also
make the Actions run itself look broken rather than "working as
intended." Exits nonzero if the crew produced no result, if review_code
timed out, or if submitting the review to GitHub itself failed (e.g.
auth or network error) - those are genuine failures, not verdicts.
"""

import os
import signal
import subprocess
import sys

from sdlc.SDLC import review_code

REVIEW_TIMEOUT_SECONDS = 600


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _sanitize_markdown_line(text: str) -> str:
    """Collapse to a single line and drop backticks so a finding can't break
    out of its `code span` or inject extra markdown list items/headers."""
    return " ".join(text.replace("`", "'").split())


class _ReviewTimeout(Exception):
    pass


def _raise_timeout(signum, frame):
    raise _ReviewTimeout(f"code_reviewer did not finish within {REVIEW_TIMEOUT_SECONDS}s")


def _submit_review(pr_number: str, repo: str, bot_token: str, *, approve: bool, body: str) -> bool:
    """Submit the review via gh, running it with a minimal, explicitly
    allowlisted environment rather than the full parent environment, so no
    other secret in os.environ (present or added later) can reach it.
    Returns True if gh successfully posted the review."""
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GH_TOKEN": bot_token,
    }
    flag = "--approve" if approve else "--request-changes"
    proc = subprocess.run(
        ["gh", "pr", "review", pr_number, "--repo", repo, flag, "--body", body],
        env=env,
    )
    if proc.returncode != 0:
        print(f"::error::gh pr review exited {proc.returncode} - review was not submitted")
        return False
    return True


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
        if not _submit_review(pr_number, repo, bot_token, approve=True, body=body):
            return 1
        print("Approved.")
        return 0

    lines = ["**Automated review by code_reviewer** found issues that need addressing:", ""]
    for finding in result.findings:
        location = f"{finding.file}:{finding.line}" if finding.line else finding.file
        location = _sanitize_markdown_line(location)
        risk = _sanitize_markdown_line(finding.risk)
        lines.append(f"- `{location}` - {risk}")
    body = "\n".join(lines)

    if not _submit_review(pr_number, repo, bot_token, approve=False, body=body):
        return 1
    print("Requested changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
