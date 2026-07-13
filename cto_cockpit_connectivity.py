"""
Non-Streamlit logic for the CTO Cockpit "Connectivity" and "Cost" tabs:
which .env-backed credential fields exist, how to save them, and how to
live-test each one. Narrow single-purpose module, same convention as
req2prod_deploy_mode.py - defensive, never raises, every network call
wrapped in try/except returning a result instead of propagating.

.env is the same single source of truth every other credential in this
app already uses (ANTHROPIC_API_KEY, SERPER_API_KEY,
GITHUB_VARIABLES_TOKEN, the AWS keys) - Connectivity writes to the real
file via python-dotenv rather than inventing a second config store,
since CrewAI's Agent/LLM objects (job_search.py, req2prod/Req2Prod.py)
read ANTHROPIC_API_KEY at import time and can't be live-reloaded
regardless of storage mechanism - a restart is unavoidable for that one
either way.
"""

import os
from dataclasses import dataclass

import boto3
import requests
from botocore.exceptions import BotoCoreError, ClientError
from dotenv import find_dotenv, set_key


@dataclass
class ConnectivityField:
    env_var: str
    label: str
    secret: bool = True
    help: str = ""


AWS_FIELDS: list[ConnectivityField] = [
    ConnectivityField("AWS_ACCESS_KEY_ID", "AWS Access Key ID"),
    ConnectivityField("AWS_SECRET_ACCESS_KEY", "AWS Secret Access Key"),
    ConnectivityField("AWS_REGION", "AWS Region", secret=False, help="e.g. eu-north-1"),
]

GITHUB_FIELDS: list[ConnectivityField] = [
    ConnectivityField("GITHUB_REPO", "GitHub repo (owner/name)", secret=False),
    ConnectivityField(
        "GITHUB_VARIABLES_TOKEN",
        "Actions token",
        help="Fine-grained PAT, 'Actions: Read and write' - toggles deploy mode / agent backend",
    ),
    ConnectivityField(
        "GITHUB_PR_PUSH_TOKEN",
        "PR push token",
        help="Fine-grained PAT, 'Contents' + 'Pull requests: Read and write' - lets software_engineer open PRs",
    ),
]

ANTHROPIC_FIELDS: list[ConnectivityField] = [
    ConnectivityField("ANTHROPIC_API_KEY", "Anthropic API key"),
]

OTHER_TOOL_FIELDS: dict[str, list[ConnectivityField]] = {
    "Serper (job/company search)": [ConnectivityField("SERPER_API_KEY", "Serper API key")],
}


def save_env_values(values: dict[str, str]) -> bool:
    """Writes each non-empty value to the real .env file (located via
    find_dotenv() - not a hardcoded relative path, so this works
    whether Streamlit is launched from the repo root or elsewhere) and
    mirrors each write into this process's own os.environ so the
    Connectivity tab itself reflects the change on its very next
    rerun. Returns False if no .env file can be located."""
    dotenv_path = find_dotenv(usecwd=True)
    if not dotenv_path:
        return False
    for key, value in values.items():
        if not value:
            continue
        set_key(dotenv_path, key, value)
        os.environ[key] = value
    return True


def test_aws_connection(access_key_id: str, secret_access_key: str, region: str) -> tuple[bool, str]:
    """sts.get_caller_identity() using exactly the given credentials
    (never os.environ) - validates what's currently typed in the form,
    not what's already saved."""
    try:
        client = boto3.client(
            "sts",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region or None,
        )
        identity = client.get_caller_identity()
        return True, f"Connected as account {identity['Account']}"
    except (BotoCoreError, ClientError, ValueError) as exc:
        return False, str(exc)


def test_github_connection(token: str, repo: str) -> tuple[bool, str]:
    """GET /repos/{repo} with the given token. Same defensive
    timeout=10/try-except pattern as req2prod_deploy_mode.py."""
    try:
        response = requests.get(
            f"https://api.github.com/repos/{repo}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if response.status_code == 200:
            return True, f"Connected to {repo}"
        return False, f"GitHub returned {response.status_code}"
    except requests.RequestException as exc:
        return False, str(exc)


def test_anthropic_connection(api_key: str) -> tuple[bool, str]:
    """Cheap GET /v1/models - never a real completion, to avoid
    spending money just to check a key is valid."""
    try:
        response = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=10,
        )
        if response.status_code == 200:
            return True, "Connected"
        return False, f"Anthropic returned {response.status_code}"
    except requests.RequestException as exc:
        return False, str(exc)


def get_github_actions_billing(token: str, owner: str) -> tuple[dict | None, str | None]:
    """GET /users/{owner}/settings/billing/actions. Returns
    (usage, None) on success or (None, reason) where reason is
    "no_token" | "forbidden" | "not_found" | "error". Genuinely
    untested against a personal-account owner (vs. an org) - callers
    must degrade to a placeholder on any non-2xx, not assume this
    endpoint works here."""
    if not token or not owner:
        return None, "no_token"
    try:
        response = requests.get(
            f"https://api.github.com/users/{owner}/settings/billing/actions",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if response.status_code == 200:
            return response.json(), None
        if response.status_code == 403:
            return None, "forbidden"
        if response.status_code == 404:
            return None, "not_found"
        return None, "error"
    except requests.RequestException:
        return None, "error"
