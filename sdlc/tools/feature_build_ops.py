"""
Tool that lets software_engineer actually ship a feature: create a
branch, commit exactly the files it wrote, push, and open a pull
request - never push directly to main. The existing "PR code review"
GitHub Actions workflow (triggered on pull_request: opened) then picks
the PR up automatically, exactly as it would for a human-authored PR.

Needs its own env var, GITHUB_PR_PUSH_TOKEN - a fine-grained token
scoped to "Contents: Read and write" + "Pull requests: Read and write"
on just this repo. This can't reuse whatever git/gh auth happens to
already be configured in the environment (unlike devops_ops.py, which
genuinely can assume that): on the production server, origin is a
read-only deploy key (~/.ssh/id_ed25519_jobfinder) by design, so the
server can `git pull` to deploy but was deliberately never given push
access - discovered when this tool's git push kept failing there with
"The key you are authenticating with has been marked as read only."
Pushing the branch and opening the PR both go through this token
explicitly (an HTTPS URL for the push, GH_TOKEN in the environment for
`gh pr create`) rather than through the ambient origin remote or gh
auth, so this works the same way regardless of what environment it
runs in - same reasoning as sdlc_deploy_mode.py's GITHUB_VARIABLES_TOKEN.
"""

import os
import stat
import subprocess
import tempfile
from contextlib import contextmanager

from crewai.tools import BaseTool

_PROTECTED_BRANCHES = {"main", "master"}
_GITHUB_REPO = "marcohauff1975/job-finder"


def _run(args: list[str], timeout: int = 30, env: dict | None = None) -> tuple[int, str, str]:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


@contextmanager
def _git_askpass_env(push_token: str):
    """A GIT_ASKPASS script that echoes the token from an environment
    variable, plus the env dict that points git at it - keeps the
    token out of argv entirely (unlike embedding it in the push URL,
    which puts it in plain sight of `ps aux`/`/proc/<pid>/cmdline`
    for as long as the git process runs), at the cost of a short-lived
    temp file that exists only for this one push."""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write('#!/bin/sh\necho "$GITHUB_PR_PUSH_TOKEN"\n')
        askpass_path = f.name
    os.chmod(askpass_path, stat.S_IRWXU)
    try:
        yield {
            **os.environ,
            "GITHUB_PR_PUSH_TOKEN": push_token,
            "GIT_ASKPASS": askpass_path,
            "GIT_TERMINAL_PROMPT": "0",
        }
    finally:
        os.unlink(askpass_path)


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

        push_token = os.getenv("GITHUB_PR_PUSH_TOKEN")
        if not push_token:
            return (
                "Refusing: GITHUB_PR_PUSH_TOKEN is not set, so there's no "
                "way to push a branch or open a PR from this environment - "
                "ask a human to add it before retrying."
            )

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

        # Push over HTTPS via GIT_ASKPASS (see _git_askpass_env) rather
        # than through the ambient `origin` remote - on the production
        # server `origin` is a read-only deploy key by design (see
        # module docstring), so this must not depend on whatever push
        # access `origin` happens to have in a given environment. The
        # token stays in the environment only, never in argv (a URL
        # with the token embedded would be visible for the life of the
        # process via `ps aux`/`/proc/<pid>/cmdline`) and never touches
        # .git/config (no -u/--set-upstream - this checkout gets reset
        # via `git checkout -B` on the tool's next run anyway).
        push_url = f"https://x-access-token@github.com/{_GITHUB_REPO}.git"
        with _git_askpass_env(push_token) as env:
            code, out, err = _run(
                ["git", "push", push_url, f"{branch_name}:{branch_name}"], timeout=60, env=env
            )
        if code != 0:
            return f"git push failed: {err or out}"

        # gh pr create needs its own auth - GH_TOKEN overrides whatever
        # (if anything) gh is already logged in as in this environment.
        code, out, err = _run(
            [
                "gh", "pr", "create",
                "--repo", _GITHUB_REPO,
                "--base", "main",
                "--head", branch_name,
                "--title", pr_title,
                "--body", pr_body,
            ],
            timeout=60,
            env={**os.environ, "GH_TOKEN": push_token},
        )
        if code != 0:
            return (
                f"Pushed branch '{branch_name}' (commit: {full_message}) but "
                f"opening the PR failed: {err or out}"
            )
        return f"Pushed branch '{branch_name}' and opened PR: {out}"
