"""
Toggle for AGENT_BACKEND, the GitHub repo variable that
.github/workflows/req2prod-pipeline.yml's resolve_backend job (and
devops-agent.yml's own copy) checks: "api" (default) means every Req2Prod
agent runs through CrewAI against the metered Anthropic API, on whichever
GitHub-hosted runner normally handles the job. "subscription" means those
same agents instead run as headless `claude -p` calls billed against
Marco's own Claude subscription (see req2prod/backend.py) - which only works
on his own machine, so resolve_backend also routes the job onto a
self-hosted runner when this is "subscription" (and only for a trusted
same-repo trigger - see that job's own comment).

Same GITHUB_VARIABLES_TOKEN REST pattern as req2prod_deploy_mode.py, for the
same reason: works the same whether this admin page is running locally or
on the production server, neither of which can be assumed to have `gh`
CLI installed and authenticated.

Deliberately a separate module from req2prod_deploy_mode.py rather than a
generalized "toggle any repo variable" helper - two near-identical single-
purpose modules are easier to read and change independently than one
generic abstraction covering both, given each variable's own meaning is
baked into a lot of the surrounding help text anyway.

Only affects GitHub Actions - it does NOT change this Streamlit process's
own agent calls, which read their own AGENT_BACKEND from THIS process's
environment (see streamlit_app.py's "AI Models" tab, and req2prod/backend.py's
run_agent()). Production Streamlit never has a subscription login
available, so it always runs on the API path regardless of what this
toggle is set to.
"""

import os

import requests

# Overridable via .env for forks/other deployments - defaults to this repo
# so nothing has to change for the normal single-repo case.
GITHUB_REPO = os.getenv("GITHUB_REPO", "marcohauff1975/job-finder")
VARIABLE_NAME = "AGENT_BACKEND"


def get_agent_backend() -> str | None:
    """"api" or "subscription" (whatever the repo variable is actually
    set to, not just those two values, though nothing else is expected),
    or None if the token is missing, lacks permission, or the variable
    doesn't exist yet - callers should treat None as "can't tell right
    now", not "api"."""
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
        return response.json()["value"].strip().lower()
    except (requests.RequestException, ValueError, KeyError):
        return None


def set_agent_backend(value: str) -> bool:
    """Sets AGENT_BACKEND to "api" or "subscription" and returns whether
    it succeeded."""
    token = os.getenv("GITHUB_VARIABLES_TOKEN")
    if not token:
        return False
    try:
        response = requests.patch(
            f"https://api.github.com/repos/{GITHUB_REPO}/actions/variables/{VARIABLE_NAME}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"name": VARIABLE_NAME, "value": value},
            timeout=10,
        )
        return response.status_code == 204
    except requests.RequestException:
        return False
