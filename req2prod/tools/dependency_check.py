"""
Ground-truth checks for code_reviewer, so it doesn't have to guess about
the freshness of a pinned package version or a Claude model id from its
own (necessarily stale) training data alone. A real version or model
released after that training data looks identical to a typo or a
hallucinated placeholder from pattern-matching alone (see PR #18, which
this was built in direct response to: code_reviewer requested changes on
requirements.txt pins and an agent_models.json model id that were both
already installed/in production use). Every check here is one real,
read-only network call against the actual source of truth, never a
guess.
"""

import os

import requests
from crewai.tools import BaseTool


class PackageVersionCheckTool(BaseTool):
    name: str = "check_pypi_package_version"
    description: str = (
        "Checks whether an exact version of a Python package actually "
        "exists on PyPI - use this before flagging a pinned "
        "requirements.txt version as implausible or nonexistent, since a "
        "version that looks unfamiliar may simply be newer than your own "
        "training data rather than a typo. Give it the exact package name "
        "and version, e.g. package='streamlit', version='1.58.0'. Returns "
        "whether that exact version is really published, and if not, the "
        "most recent version PyPI actually has."
    )

    def _run(self, package: str, version: str) -> str:
        try:
            response = requests.get(f"https://pypi.org/pypi/{package}/json", timeout=10)
        except requests.RequestException as e:
            return f"Couldn't reach PyPI to check '{package}': {e}"
        if response.status_code == 404:
            return f"'{package}' does not exist on PyPI at all."
        if response.status_code != 200:
            return f"PyPI returned HTTP {response.status_code} for '{package}' - couldn't verify."
        data = response.json()
        releases = data.get("releases", {})
        if version in releases and releases[version]:
            return f"'{package}=={version}' is a real, published release on PyPI."
        latest = data.get("info", {}).get("version", "unknown")
        return (
            f"'{package}=={version}' was NOT found on PyPI. The latest "
            f"published version of '{package}' is '{latest}'."
        )


class AnthropicModelCheckTool(BaseTool):
    name: str = "check_anthropic_model_id"
    description: str = (
        "Checks whether a model id (e.g. 'claude-sonnet-5') is a real, "
        "currently-available Anthropic model - use this before flagging an "
        "LLM(model=...) assignment as a suspected typo or placeholder, "
        "since a real model released after your own training data will "
        "look exactly like one to pattern-matching alone (e.g. having no "
        "dated suffix, unlike older models). Give it the bare model id, "
        "without the 'anthropic/' provider prefix crewai's LLM() wrapper "
        "uses. Returns whether it's in Anthropic's real, live model list, "
        "and if not, what's actually available."
    )

    def _run(self, model_id: str) -> str:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return "ANTHROPIC_API_KEY isn't configured - can't verify against the live model list."
        model_id = model_id.removeprefix("anthropic/")
        try:
            response = requests.get(
                "https://api.anthropic.com/v1/models",
                headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
                timeout=10,
            )
        except requests.RequestException as e:
            return f"Couldn't reach the Anthropic API to check '{model_id}': {e}"
        if response.status_code != 200:
            return f"Anthropic API returned HTTP {response.status_code} - couldn't verify '{model_id}'."
        real_ids = [m["id"] for m in response.json().get("data", [])]
        if model_id in real_ids:
            return f"'{model_id}' is a real, currently-available Anthropic model."
        return (
            f"'{model_id}' was NOT found in Anthropic's live model list. "
            f"Currently available models: {', '.join(real_ids)}"
        )
