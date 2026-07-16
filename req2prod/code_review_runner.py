"""
Driver for the "PR code review" GitHub Actions workflow - calls
review_code() from req2prod/Req2Prod.py, and if it requests changes, runs a
review/fix loop (pr_fix_agent) entirely within this one script
execution rather than relying on further GitHub Actions runs to
iterate - so a PR never sits waiting on Marco (who isn't a developer
and has explicitly said he can't judge code himself) to notice it's
stuck and push a fix by hand. If the loop can't converge on a clean
approval within MAX_FIX_ATTEMPTS rounds, pr_arbiter - a genuinely
independent second opinion, not just "try again" - makes the real
final call: approve and merge if what's left is actually safe to
ship, or leave the PR open, unmerged, and email Marco a plain-language
explanation if it isn't. Marco is only ever informed, never asked to
approve or reject code himself.

This runs as a distinct reviewer identity (REVIEWER_BOT_TOKEN, the
"MarcoAIagent" account), not the workflow's default GITHUB_TOKEN -
GitHub does not let a pull request's author satisfy its own required
review, so the review has to come from a genuinely different account.
The same token also authenticates the checkout itself (see
.github/workflows/req2prod-pipeline.yml's code_review job): the repo's
default GITHUB_TOKEN is read-only, and any fix commits this script
pushes need write access - and the checkout must be on the PR's real
head branch (not the default merge-ref, detached-HEAD checkout), or a
push here would have nowhere real to land.

Any fix commits accumulated across the loop are committed and pushed
exactly once, at the very end, regardless of the final outcome - never
once per attempt. Pushing mid-loop would itself trigger a fresh
`pull_request: synchronize` event and a concurrent second run of this
same job for every attempt, redoing (and racing) the very loop this
script already handles internally.

Reads its parameters from environment variables:
    DIFF_FILE          - path to a file containing the PR's diff
    PR_NUMBER          - the pull request number to review
    REPO               - "owner/repo"
    REVIEWER_BOT_TOKEN - the reviewer identity's own token

Exit code is 0 whenever a real, intentional outcome was reached -
merged, or left open with Marco notified - since both are "working as
intended," not failures. Even code_reviewer itself producing no result
(an empty LLM response after retries, a known intermittent CrewAI/
provider issue - not a verdict on the diff) escalates to pr_arbiter
rather than failing outright, the same way an unconverged findings loop
does. Exits nonzero only for genuine infrastructure failures: a step
timed out, pr_arbiter *also* produced no result, or submitting the
review/merge/push to GitHub itself failed (e.g. auth or network error).
"""

import os
import signal
import subprocess
import sys

from notify import send_pr_unresolvable_notification
from req2prod.Req2Prod import (
    ArbiterVerdict,
    CodeReviewFinding,
    CodeReviewResult,
    arbiter_review,
    fix_review_findings,
    review_code,
)

REVIEW_TIMEOUT_SECONDS = 600
MAX_FIX_ATTEMPTS = 2
MAX_TRANSIENT_RETRIES = 2  # retries for a review_code() crash/None result itself
# (an empty LLM response, not a real verdict on the diff) - separate from
# MAX_FIX_ATTEMPTS, which counts genuine "changes requested" rounds


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


def _run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


class _StepTimeout(Exception):
    pass


def _raise_timeout(signum, frame):
    raise _StepTimeout("An automated review/fix step did not finish in time")


def _with_timeout(func, *args, **kwargs):
    signal.signal(signal.SIGALRM, _raise_timeout)
    signal.alarm(REVIEW_TIMEOUT_SECONDS)
    try:
        return func(*args, **kwargs)
    finally:
        signal.alarm(0)


def _review_code_with_retries(diff: str) -> CodeReviewResult | None:
    """review_code() returning None means either a caught crew exception or
    an empty LLM response - both are a known intermittent flake (not a
    verdict on the diff), so retry a couple of times before treating it as
    a genuine infrastructure failure. A timeout is not retried here; it
    already means REVIEW_TIMEOUT_SECONDS was spent once and propagates
    straight up to the caller."""
    result: CodeReviewResult | None = None
    for retry in range(MAX_TRANSIENT_RETRIES + 1):
        result = _with_timeout(review_code, diff)
        if result is not None:
            return result
        if retry < MAX_TRANSIENT_RETRIES:
            print("code_reviewer crew produced no result - retrying " f"({retry + 1}/{MAX_TRANSIENT_RETRIES})...")
    return result


def _bot_env(bot_token: str) -> dict:
    """Minimal, explicitly allowlisted environment for gh calls made as the
    reviewer bot - only what gh itself needs, so no other secret in
    os.environ (present or added later) can reach the subprocess."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "GH_TOKEN": bot_token,
    }


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


def _current_diff_vs_main() -> str:
    """The PR's real current diff against origin/main, including any
    uncommitted working-tree edits pr_fix_agent has made in this round -
    this is what gets re-reviewed next, not the diff the job started
    with. Requires origin/main to already be fetched."""
    proc = subprocess.run(
        ["git", "diff", "origin/main"], capture_output=True, text=True, check=True
    )
    return proc.stdout


def _commit_and_push_accumulated_fixes(changed_files: list[str]) -> bool:
    """Commits and pushes every file pr_fix_agent touched across every
    attempt, in one commit, at the very end - regardless of whether the
    PR ends up merged or left open, so partial progress is never
    silently lost. Returns True if there was nothing to push or the
    push succeeded; False on a genuine git failure."""
    if not changed_files:
        return True

    code, out, err = _run(["git", "config", "user.name", "pr-fix-agent"])
    if code != 0:
        print(f"::error::git config user.name failed: {err or out}")
        return False
    code, out, err = _run(["git", "config", "user.email", "pr-fix-agent@users.noreply.github.com"])
    if code != 0:
        print(f"::error::git config user.email failed: {err or out}")
        return False

    code, out, err = _run(["git", "add", *changed_files])
    if code != 0:
        print(f"::error::git add failed: {err or out}")
        return False

    # File list goes in the commit message (not just the code) so the
    # "Req2Prod Pipeline" admin dashboard (req2prod_pr_flow.py) has something
    # real to show in its PR Fix Agent box - it identifies this commit
    # by the "[pr-fix-agent]" prefix, same as devops-agent's commits do
    # for its own workflow.
    message = f"[pr-fix-agent] Address code review findings in {', '.join(changed_files)}"
    code, out, err = _run(["git", "commit", "-m", message])
    if code != 0:
        print(f"::error::git commit failed (maybe nothing actually changed?): {err or out}")
        return False

    code, out, err = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        print(f"::error::git rev-parse failed: {err or out}")
        return False
    branch = out

    code, out, err = _run(["git", "push", "origin", branch], timeout=60)
    if code != 0:
        print(f"::error::git push failed: {err or out}")
        return False
    print(f"Pushed fix commit to {branch}.")
    return True


# GitHub refuses to let a Personal Access Token create or update anything under
# here, even one with full repo write - a separate lock from ordinary write
# access, and a deliberate one: a token that can rewrite CI can rewrite the
# thing that is checking it. REVIEWER_BOT_TOKEN has write (it merges these PRs)
# and still can't touch this directory.
_WORKFLOW_DIR = ".github/workflows/"


def _touches_a_workflow(diff: str) -> bool:
    """Would pushing a fix for this diff be rejected outright?

    Observed on PR #91: code_reviewer raised two real findings, pr_fix_agent
    fixed both, the re-review passed, and the push was rejected because one of
    the fixed files lived under .github/workflows/. Git pushes all or nothing,
    so that one file took every other fix with it - a whole review's work
    done, then dropped, and reported as a plain failure.

    Rather than grant the workflow scope (which would let an agent edit the
    pipeline that reviews it - GitHub's default is the right one), the fix
    loop is skipped for these and pr_arbiter decides on the findings as they
    stand. The findings still reach Marco; only the automatic fixing stops.
    """
    return any(
        line[len("+++ b/"):].startswith(_WORKFLOW_DIR)
        for line in diff.splitlines()
        if line.startswith("+++ b/")
    )


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

    changed_files: list[str] = []
    last_findings = []  # most recent round's findings that weren't a clean pass, for the audit trail
    result: CodeReviewResult | None = None
    review_infra_failure = False  # code_reviewer never produced a result at all,
    # as opposed to producing one with findings - see below

    # A fix for one of these can't be pushed, so producing one only wastes a
    # crew call and then loses it. Review still happens; pr_arbiter still gets
    # the last word. See _touches_a_workflow.
    fixes_are_pushable = not _touches_a_workflow(diff)
    if not fixes_are_pushable:
        print(
            f"=== {_WORKFLOW_DIR} in the diff - reviewing without the fix loop ===\n"
            "    GitHub rejects PAT pushes to workflow files, so a fix here "
            "could not be saved.\n"
            "    Findings go to pr_arbiter as they stand."
        )

    for attempt in range(MAX_FIX_ATTEMPTS + 1):
        print(f"=== Running code_reviewer (round {attempt + 1}) ===")
        try:
            result = _review_code_with_retries(diff)
        except _StepTimeout as exc:
            print(f"::error::{exc}")
            return 1
        print(result)

        if result is None:
            # A repeated empty LLM response is a known intermittent CrewAI/
            # provider issue (see review_code()'s docstring), not a verdict on
            # this diff - escalate straight to pr_arbiter instead of failing
            # the whole run outright, the same way an unconverged findings
            # loop does below. This is exactly the gap that let PR #21's own
            # code_review check fail outright with nobody notified.
            print(
                "::warning::code_reviewer crew produced no result after retries - "
                "escalating to pr_arbiter instead of failing outright"
            )
            review_infra_failure = True
            break

        if result.passed:
            break

        last_findings = result.findings
        if not fixes_are_pushable:
            break  # nothing pr_fix_agent wrote here could be pushed
        if attempt >= MAX_FIX_ATTEMPTS:
            break  # findings remain, but the fix loop is out of attempts

        print(f"=== Running pr_fix_agent (attempt {attempt + 1}) ===")
        try:
            fix_result = _with_timeout(fix_review_findings, result.findings)
        except _StepTimeout as exc:
            print(f"::error::{exc}")
            return 1

        if fix_result is None:
            print("::error::pr_fix_agent crew produced no result - stopping the fix loop early")
            break
        print(fix_result)

        if not fix_result.files_changed:
            print("pr_fix_agent made no changes - stopping the fix loop early.")
            break

        for path in fix_result.files_changed:
            if path not in changed_files:
                changed_files.append(path)

        diff = _current_diff_vs_main()

    if result is not None and result.passed:
        if not _commit_and_push_accumulated_fixes(changed_files):
            return 1
        body = (
            "**Automated review by code_reviewer:** no issues found."
            if not changed_files
            else "**Automated review by code_reviewer:** no issues found, after "
            "pr_fix_agent addressed these findings from an earlier round:\n\n"
            + _format_findings(last_findings)
        )
        return _approve_and_merge(pr_number, repo, bot_token, body)

    print("=== Review/fix loop did not converge - running pr_arbiter ===")
    findings_for_arbiter = (
        [
            CodeReviewFinding(
                file="(none)",
                risk=(
                    "code_reviewer could not complete an automated review - every "
                    "attempt got an empty response from the LLM, a known "
                    "intermittent CrewAI/provider issue, not a finding about this "
                    "diff. Read the actual diff yourself and decide on its merits "
                    "whether it's safe to merge."
                ),
            )
        ]
        if review_infra_failure
        else result.findings
    )
    try:
        verdict: ArbiterVerdict | None = _with_timeout(arbiter_review, diff, findings_for_arbiter)
    except _StepTimeout as exc:
        print(f"::error::{exc}")
        return 1

    if verdict is None:
        print("::error::pr_arbiter crew produced no result")
        return 1
    print(verdict)

    if not _commit_and_push_accumulated_fixes(changed_files):
        return 1

    if verdict.safe_to_merge:
        body = f"**Secondary review by pr_arbiter:** {verdict.reasoning}"
        return _approve_and_merge(pr_number, repo, bot_token, body)

    body = (
        "**Secondary review by pr_arbiter:** not safe to merge automatically.\n\n"
        f"{_sanitize_markdown_line(verdict.reasoning)}\n\n"
        + "\n".join(f"- {_sanitize_markdown_line(r)}" for r in verdict.blocking_reasons)
    )
    if not _submit_review(pr_number, repo, bot_token, approve=False, body=body):
        return 1
    print("Left open, unmerged - notifying Marco.")
    send_pr_unresolvable_notification(pr_number, repo, verdict.reasoning, verdict.blocking_reasons)
    return 0


if __name__ == "__main__":
    sys.exit(main())
