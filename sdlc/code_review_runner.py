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

Exit code is 0 if a review was submitted at all (whether it approved
or requested changes) - a request-changes review is what actually
blocks the merge under branch protection, so failing this script's
exit code for that case would be redundant and would also make the
Actions run itself look broken rather than "working as intended."
Only exits nonzero if something failed before a review could be
posted (e.g. the crew produced no result).
"""

import os
import subprocess
import sys

from sdlc.SDLC import review_code


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def _sanitize_markdown_line(text: str) -> str:
    """Collapse to a single line and drop backticks so a finding can't break
    out of its `code span` or inject extra markdown list items/headers."""
    return " ".join(text.replace("`", "'").split())


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
    result = review_code(diff)
    print(result)

    if result is None:
        print("::error::code_reviewer crew produced no result")
        return 1

    env = {k: v for k, v in os.environ.items() if k not in ("REVIEWER_BOT_TOKEN", "ANTHROPIC_API_KEY")}
    env["GH_TOKEN"] = bot_token

    if result.passed:
        body = "**Automated review by code_reviewer:** no issues found."
        subprocess.run(
            ["gh", "pr", "review", pr_number, "--repo", repo, "--approve", "--body", body],
            env=env,
            check=True,
        )
        print("Approved.")
        return 0

    lines = ["**Automated review by code_reviewer** found issues that need addressing:", ""]
    for finding in result.findings:
        location = f"{finding.file}:{finding.line}" if finding.line else finding.file
        location = _sanitize_markdown_line(location)
        risk = _sanitize_markdown_line(finding.risk)
        lines.append(f"- `{location}` - {risk}")
    body = "\n".join(lines)

    subprocess.run(
        ["gh", "pr", "review", pr_number, "--repo", repo, "--request-changes", "--body", body],
        env=env,
        check=True,
    )
    print("Requested changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
