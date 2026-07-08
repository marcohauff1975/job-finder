"""
Tool that lets software_engineer actually ship a feature: create a
branch, commit exactly the files it wrote, push, and open a pull
request - never push directly to main. The existing "PR code review"
GitHub Actions workflow (triggered on pull_request: opened) then picks
the PR up automatically, exactly as it would for a human-authored PR.

Like devops_ops.py, this expects `gh` (GitHub CLI) already authenticated
and a git identity/push credentials already configured in whatever
environment it runs in - it doesn't handle auth itself.
"""

import subprocess

from crewai.tools import BaseTool

_PROTECTED_BRANCHES = {"main", "master"}


def _run(args: list[str], timeout: int = 30) -> tuple[int, str, str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


class CreateFeatureBranchAndOpenPRTool(BaseTool):
    name: str = "create_feature_branch_and_open_pr"
    description: str = (
        "Creates a new branch off the latest main, stages exactly the "
        "given file path(s) (never everything in the working tree - "
        "other unrelated local changes are left alone), commits them "
        "with the given message (automatically prefixed with "
        "'[software-engineer] ' so these commits are identifiable "
        "later), pushes the branch, and opens a pull request against "
        "main with the given title/body. Only call this once you've "
        "actually written the feature's file(s) with the file-writing "
        "tool. Never pass 'main' or 'master' as branch_name - this tool "
        "only ever pushes to a new feature branch and opens a PR, it "
        "never pushes directly to main; code_reviewer and the existing "
        "branch protection rule handle everything from the PR onward, "
        "you do not merge it yourself."
    )

    def _run(
        self,
        branch_name: str,
        file_paths: list[str],
        commit_message: str,
        pr_title: str,
        pr_body: str,
    ) -> str:
        if branch_name.strip().lower() in _PROTECTED_BRANCHES:
            return (
                f"Refusing: '{branch_name}' is a protected branch. Pick a "
                "descriptive feature branch name instead, e.g. "
                "'feature/short-slug'."
            )
        if not file_paths:
            return "Refusing: no file_paths given - nothing to commit."

        code, out, err = _run(["git", "fetch", "origin", "main"], timeout=60)
        if code != 0:
            return f"git fetch origin main failed: {err or out}"

        code, out, err = _run(["git", "checkout", "-B", branch_name, "origin/main"])
        if code != 0:
            return f"git checkout -B {branch_name} failed: {err or out}"

        code, out, err = _run(["git", "add", *file_paths])
        if code != 0:
            return f"git add failed: {err or out}"

        full_message = f"[software-engineer] {commit_message}"
        code, out, err = _run(["git", "commit", "-m", full_message])
        if code != 0:
            return f"git commit failed (maybe nothing actually changed?): {err or out}"

        code, out, err = _run(["git", "push", "-u", "origin", branch_name], timeout=60)
        if code != 0:
            return f"git push failed: {err or out}"

        code, out, err = _run(
            [
                "gh", "pr", "create",
                "--base", "main",
                "--head", branch_name,
                "--title", pr_title,
                "--body", pr_body,
            ],
            timeout=60,
        )
        if code != 0:
            return (
                f"Pushed branch '{branch_name}' (commit: {full_message}) but "
                f"opening the PR failed: {err or out}"
            )
        return f"Pushed branch '{branch_name}' and opened PR: {out}"
