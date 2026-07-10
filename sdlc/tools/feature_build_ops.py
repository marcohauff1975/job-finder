"""
Tool that lets software_engineer actually ship a feature: create a
branch, commit exactly the files it wrote, push, and open a pull
request - never push directly to main. The existing "PR code review"
GitHub Actions workflow (triggered on pull_request: opened) then picks
the PR up automatically, exactly as it would for a human-authored PR.

All git operations run inside the current build's isolated workspace
(see build_workspace.py) - never in the live app's own checkout. That
isolation is the fix for a real production outage (2026-07-10): this
tool used to run git commands in whatever directory the process's cwd
happened to be, which in production is the exact directory
jobfinder.service serves live traffic from.

Needs its own env var, GITHUB_PR_PUSH_TOKEN - a fine-grained token
scoped to "Contents: Read and write" + "Pull requests: Read and write"
on just this repo (also used by build_workspace.py to clone). This
can't reuse whatever git/gh auth happens to already be configured in
the environment (unlike devops_ops.py, which genuinely can assume
that): the production server's own git checkout uses a read-only
deploy key by design (~/.ssh/id_ed25519_jobfinder, can pull to deploy,
was never meant to push) - discovered when this tool's git push kept
failing there with "The key you are authenticating with has been
marked as read only." Pushing the branch goes through this token via
GIT_ASKPASS, and opening the PR goes through GitHub's REST API
directly with this same token, rather than shelling out to the `gh`
CLI - discovered (2026-07-10) that `gh` isn't even installed on the
production server, which made every otherwise-successful push end in
a confusing "[Errno 2] No such file or directory: 'gh'" failure. Using
the REST API directly means this doesn't depend on `gh` being present
at all, the same reasoning as sdlc_deploy_mode.py's
GITHUB_VARIABLES_TOKEN.
"""

import os

import requests
from crewai.tools import BaseTool

from sdlc.tools.build_workspace import _GITHUB_REPO, git_askpass_env, run_in_workspace, workspace_dir

_PROTECTED_BRANCHES = {"main", "master"}


class CreateFeatureBranchAndOpenPRTool(BaseTool):
    name: str = "create_feature_branch_and_open_pr"
    description: str = (
        "Creates a new branch off the current build workspace's main, "
        "stages exactly the given file path(s) (never everything in the "
        "working tree - other unrelated local changes are left alone), "
        "commits them with the given message (automatically prefixed with "
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

        try:
            workspace_dir()  # raises if called outside build_workspace()
        except RuntimeError as e:
            return f"Error: {e}"

        # No fetch/checkout-from-origin needed here: build_workspace()
        # already cloned a fresh copy of main for this build, so the
        # workspace's current HEAD already is main.
        code, out, err = run_in_workspace(["git", "checkout", "-B", branch_name])
        if code != 0:
            return f"git checkout -B {branch_name} failed: {err or out}"

        code, out, err = run_in_workspace(["git", "add", *file_paths])
        if code != 0:
            return f"git add failed: {err or out}"

        full_message = f"[software-engineer] {commit_message}"
        code, out, err = run_in_workspace(["git", "commit", "-m", full_message])
        if code != 0:
            return f"git commit failed (maybe nothing actually changed?): {err or out}"

        # Push over HTTPS via GIT_ASKPASS - the token stays in the
        # environment only, never in argv (a URL with the token
        # embedded would be visible for the life of the process via
        # `ps aux`/`/proc/<pid>/cmdline`) and never touches
        # .git/config (no -u/--set-upstream - this workspace gets
        # deleted right after this build finishes anyway).
        push_url = f"https://x-access-token@github.com/{_GITHUB_REPO}.git"
        with git_askpass_env(push_token) as env:
            code, out, err = run_in_workspace(
                ["git", "push", push_url, f"{branch_name}:{branch_name}"], timeout=60, env=env
            )
        if code != 0:
            return f"git push failed: {err or out}"

        try:
            response = requests.post(
                f"https://api.github.com/repos/{_GITHUB_REPO}/pulls",
                headers={
                    "Authorization": f"Bearer {push_token}",
                    "Accept": "application/vnd.github+json",
                },
                json={"title": pr_title, "head": branch_name, "base": "main", "body": pr_body},
                timeout=30,
            )
            response.raise_for_status()
            pr_url = response.json()["html_url"]
        except (requests.RequestException, KeyError, ValueError) as e:
            return (
                f"Pushed branch '{branch_name}' (commit: {full_message}) but "
                f"opening the PR failed: {e}"
            )
        return f"Pushed branch '{branch_name}' and opened PR: {pr_url}"
