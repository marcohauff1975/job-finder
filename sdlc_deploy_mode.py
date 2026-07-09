"""
Toggle for AUTO_DEPLOY_ON_MERGE, the GitHub repo variable that
.github/workflows/sdlc-pipeline.yml's deploy job checks: "false"
(default, "regular development mode") means deploying to prod after a
merge still requires a manual "Run workflow" click; "true" ("demo
mode") means merging a PR into main auto-deploys straight to
production. Same variable the desktop "Toggle Demo Mode.command"
flips via `gh variable set`, but this uses the GitHub REST API
directly with GITHUB_VARIABLES_TOKEN so it works the same way whether
the admin page is running locally or on the production server -
neither of which can be assumed to have `gh` CLI installed and
authenticated.

Needs its own .env var, GITHUB_VARIABLES_TOKEN - a separate
fine-grained token scoped to just "Actions: Read and write" on this
repo, rather than reusing GITHUB_ACTIONS_TOKEN (sdlc_pr_flow.py,
sdlc_agent_steps.py), which is scoped narrower for its own read-only
PR/workflow-status use and shouldn't be broadened just for this.
"""

import os

import requests

GITHUB_REPO = "marcohauff1975/job-finder"
VARIABLE_NAME = "AUTO_DEPLOY_ON_MERGE"


def get_auto_deploy_mode() -> bool | None:
    """True if demo mode (auto-deploy on merge) is on, False if off, or
    None if the token is missing or lacks permission - callers should
    treat None as "can't tell right now", not "off"."""
    token = os.getenv("GITHUB_VARIABLES_TOKEN")
    if not token:
        return None
    try:
        response = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/variables/{VARIABLE_NAME}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["value"].strip().lower() == "true"
    except (requests.RequestException, ValueError, KeyError):
        return None


def set_auto_deploy_mode(enabled: bool) -> bool:
    """Flips AUTO_DEPLOY_ON_MERGE and returns whether it succeeded."""
    token = os.getenv("GITHUB_VARIABLES_TOKEN")
    if not token:
        return False
    try:
        response = requests.patch(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/variables/{VARIABLE_NAME}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"name": VARIABLE_NAME, "value": "true" if enabled else "false"},
            timeout=10,
        )
        return response.status_code == 204
    except requests.RequestException:
        return False
