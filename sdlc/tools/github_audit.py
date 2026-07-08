"""
Read-only GitHub inspection for the CTO and Security Engineer personas
(see technology_excellence_* agents in sdlc/SDLC.py) - checks what a
real visitor/GitHub itself actually sees for a repo, not just its local
git state. Local git state alone can't tell you the repo is still
private, or that GitHub's own Dependabot/secret-scanning are sitting
disabled - exactly the kind of thing that would sink a "link this from
my resume" plan even with perfect code.

Every call here is a read-only `gh api` GET - never a POST/PATCH/DELETE.
Uses whatever `gh` auth is already configured for the calling
environment; this tool never fetches or handles credentials itself.
"""

import json
import re
import subprocess

from crewai.tools import BaseTool


def _run(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str, str]:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _owner_repo(repo_path: str) -> str | None:
    code, out, _ = _run(["git", "remote", "get-url", "origin"], cwd=repo_path)
    if code != 0:
        return None
    match = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(\.git)?/?$", out.strip())
    return f"{match.group(1)}/{match.group(2)}" if match else None


class GitHubLiveRepoCheckTool(BaseTool):
    name: str = "github_live_repo_check"
    description: str = (
        "Checks what GitHub itself actually shows for the repo at "
        "repo_path - not its local git state. Returns: whether it even "
        "has a GitHub remote at all; if so, its real visibility "
        "(public/private - a repo can look perfect locally and still be "
        "private, which alone would break a 'link this from my resume' "
        "plan), description/homepage/topics/license as a visitor would "
        "see them, open issue and star counts; whether Dependabot "
        "alerts and secret scanning are actually turned on for this "
        "repo (both are free once a repo is public, but neither is on "
        "by default); the count of any currently-open Dependabot or "
        "secret-scanning alerts; and whether branch protection is "
        "enabled on the default branch. Read-only - every call is a "
        "GET, never a POST/PATCH/DELETE. Call this once per repo; it "
        "always checks everything above in one pass."
    )

    def _run(self, repo_path: str) -> str:
        owner_repo = _owner_repo(repo_path)
        if owner_repo is None:
            return (
                f"'{repo_path}' has no GitHub remote (or isn't a git repo at "
                "all) - it is not hosted on GitHub, so none of its public "
                "GitHub presence can be checked. If this repo is meant to be "
                "part of the public portfolio, that alone is worth reporting."
            )

        result: dict = {"github_repo": owner_repo}

        code, out, err = _run(
            [
                "gh", "api", f"repos/{owner_repo}",
                "--jq",
                "{visibility, private, description, homepage, topics, "
                "default_branch, open_issues_count, stargazers_count, "
                "license, has_wiki}",
            ],
            cwd=repo_path,
        )
        try:
            result["repo_metadata"] = json.loads(out) if code == 0 else f"error: {err or out}"
        except json.JSONDecodeError:
            result["repo_metadata"] = out or f"error: {err}"

        default_branch = "main"
        if isinstance(result["repo_metadata"], dict):
            default_branch = result["repo_metadata"].get("default_branch", "main")

        code, _, err = _run(["gh", "api", f"repos/{owner_repo}/vulnerability-alerts"], cwd=repo_path)
        result["dependabot_alerts_enabled"] = code == 0
        if code != 0:
            result["dependabot_alerts_note"] = err

        code, out, err = _run(
            ["gh", "api", f"repos/{owner_repo}/dependabot/alerts", "--jq", "length"], cwd=repo_path
        )
        result["dependabot_open_alert_count"] = out if code == 0 else f"unavailable: {err}"

        code, out, err = _run(
            ["gh", "api", f"repos/{owner_repo}/secret-scanning/alerts", "--jq", "length"], cwd=repo_path
        )
        result["secret_scanning_open_alert_count"] = out if code == 0 else f"unavailable: {err}"

        code, _, err = _run(
            ["gh", "api", f"repos/{owner_repo}/branches/{default_branch}/protection"], cwd=repo_path
        )
        result["branch_protection_enabled"] = code == 0
        if code != 0:
            result["branch_protection_note"] = err

        return json.dumps(result, indent=2, default=str)
