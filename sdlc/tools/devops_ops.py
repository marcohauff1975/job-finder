"""
Tools that let devops_agent actually diagnose and fix a failed deploy:
reading the real failure logs from GitHub Actions, committing and
pushing a fix, and re-triggering the workflow that failed - closing
the loop that prod_ops.py leaves to a human today (that file only acts
on a deploy's app-health outcome once the deploy mechanism itself
already succeeded; this handles the deploy mechanism breaking).

Like prod_ops.py, these expect the environment they run in to already
have `gh` (GitHub CLI) authenticated via GH_TOKEN, and a git identity/
push credentials already configured (see .github/workflows/
devops-agent.yml) - they don't handle auth themselves.
"""

import subprocess

from crewai.tools import BaseTool


def _run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


class FetchFailedRunLogsTool(BaseTool):
    name: str = "fetch_failed_run_logs"
    description: str = (
        "Returns the real log output of only the failed step(s) from a "
        "given GitHub Actions run ID, via `gh run view --log-failed`. "
        "Always call this first, before forming any theory about what "
        "went wrong - the actual error text is the only reliable source "
        "of the root cause, not the workflow or step names."
    )

    def _run(self, run_id: str) -> str:
        code, out, err = _run(["gh", "run", "view", run_id, "--log-failed"], timeout=60)
        if code != 0:
            return f"Failed to fetch logs (exit {code}): {err or out}"
        # Keep the tail - the actual failing command and its error are
        # always at the end of a failed step's log, and this keeps the
        # tool's output well within a reasonable prompt size regardless
        # of how verbose the failing step was.
        return out[-12000:]


class CommitAndPushFixTool(BaseTool):
    name: str = "commit_and_push_fix"
    description: str = (
        "Stages the given file path(s), commits them with the given "
        "message (automatically prefixed with '[devops-agent] ' so these "
        "commits are identifiable later - this prefix is also how the "
        "calling workflow detects and refuses to try a second automatic "
        "fix if this one doesn't resolve the failure), and pushes "
        "directly to origin/main. Only call this once you've actually "
        "edited the file(s) with the file-writing tool and are confident "
        "in the fix - this pushes straight to production's source of "
        "truth with no human review step in between."
    )

    def _run(self, file_paths: list[str], commit_message: str) -> str:
        code, out, err = _run(["git", "add", *file_paths])
        if code != 0:
            return f"git add failed: {err or out}"

        full_message = f"[devops-agent] {commit_message}"
        code, out, err = _run(["git", "commit", "-m", full_message])
        if code != 0:
            return f"git commit failed (maybe nothing actually changed?): {err or out}"

        code, out, err = _run(["git", "push", "origin", "main"], timeout=60)
        if code != 0:
            return f"git push failed: {err or out}"

        return f"Pushed commit: {full_message}"


class RetriggerWorkflowTool(BaseTool):
    name: str = "retrigger_deploy_workflow"
    description: str = (
        "Re-dispatches the given GitHub Actions workflow (file name or "
        "path, e.g. 'deploy-to-prod.yml') on the main branch, via "
        "`gh workflow run`, so the fix you just pushed actually gets "
        "deployed instead of just sitting on main. Only call this after "
        "commit_and_push_fix has already succeeded."
    )

    def _run(self, workflow_file: str) -> str:
        code, out, err = _run(["gh", "workflow", "run", workflow_file, "--ref", "main"])
        if code != 0:
            return f"Failed to retrigger workflow (exit {code}): {err or out}"
        return f"Re-triggered {workflow_file}."
