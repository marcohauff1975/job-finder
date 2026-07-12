"""
Dual-backend dispatch for review_code/fix_review_findings/arbiter_review:
either CrewAI's normal Anthropic-API-billed path (AGENT_BACKEND=api, the
default and the only path anywhere except this file), or a headless
`claude -p` subprocess call authenticated via whatever Claude subscription
is already logged in on this machine (AGENT_BACKEND=subscription) - for
testing the full SDLC review/fix/arbiter loop without burning API credits
on every PR. See .github/workflows/sdlc-pipeline.yml's resolve_backend job
for how AGENT_BACKEND actually gets set in CI: only ever "subscription" on
Marco's own self-hosted runner (his laptop), and only for pushes/PRs he
authored himself - never for an external fork's PR, regardless of the
toggle, since a self-hosted runner physically executing an untrusted PR's
code is a real risk on a public repo.

Deliberately does NOT pass --bare to `claude -p`: Anthropic's own docs are
explicit that --bare skips the long-lived CLAUDE_CODE_OAUTH_TOKEN, but are
unclear on whether it also skips an ordinary interactive `claude login`
subscription session - getting that wrong would silently fall back to
requiring ANTHROPIC_API_KEY, defeating the entire point of this module.
Runs from the repo's own checkout instead (see SDLC.py's REPO_ROOT) - it
has no CLAUDE.md to pick up unwanted context from anyway, and pr_fix_agent
needs to be there regardless, since it edits real files in place.

Scoped only to code_reviewer/pr_fix_agent/pr_arbiter for now - the highest-
volume, non-time-critical part of the pipeline, and the part just proven
working end to end (see PR #21). devops_agent/rollback_agent stay on the
API path deliberately: they react to a live production deploy failure in
real time, unattended, which only works if they run in GitHub's cloud, not
on a laptop that has to be on and logged in to do anything.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Callable, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

SUBSCRIPTION_TIMEOUT_SECONDS = 600


def _clean_model_id(raw: str) -> str:
    """'anthropic/claude-sonnet-5' -> 'claude-sonnet-5', for --model."""
    return raw.removeprefix("anthropic/")


def bash_tool_instructions(tool_names: list[str], workspace_dir: str | None = None) -> str:
    """A prompt block documenting the one Bash command form an agent may
    use to reach sdlc/tool_cli.py's tools (see that module's docstring for
    why - it reuses the exact same BaseTool implementations API mode
    calls directly, rather than asking the model to reinvent SSH/browser-
    automation/git-push behavior from a description). Descriptions are
    pulled from the tools' own .description - the same text CrewAI shows
    the model in API mode - so there's exactly one place each tool's
    behavior is documented, not two that could drift apart."""
    from sdlc.tool_cli import TOOLS_BY_NAME

    workspace_flag = f" --workspace-dir {workspace_dir}" if workspace_dir else ""
    lines = [
        "You have Bash access, restricted to exactly one command form: "
        f"`python -m sdlc.tool_cli{workspace_flag} <tool_name> '<json kwargs>'`. "
        "Available <tool_name> values and what each does:"
    ]
    for name in tool_names:
        lines.append(f"- {name}: {TOOLS_BY_NAME[name].description}")
    return "\n".join(lines)


def _extract_json_object(text: str) -> str | None:
    """Claude sometimes wraps the requested JSON in prose or a markdown
    fence despite being told not to - pull out the first balanced-looking
    {...} block rather than trusting the whole response to be bare JSON."""
    fence_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence_match:
        return fence_match.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    return text[start : end + 1]


def run_via_subscription(
    *,
    role: str,
    goal: str,
    backstory: str,
    task_description: str,
    expected_output: str,
    output_model: type[T],
    model: str,
    cwd: str,
    allowed_tools: str = "",
    extra_prompt_context: str = "",
) -> T | None:
    """Run one agent turn as a headless `claude -p` call instead of a
    CrewAI/Anthropic-API call, parsing the response into the same
    Pydantic model CrewAI's output_pydantic would otherwise have
    produced - so callers don't need to know which backend ran.
    extra_prompt_context is appended after the task description - used
    for tool-invocation instructions (see bash_tool_instructions above)
    and for threading prior tasks' results into a multi-step chain (see
    e.g. review_project_readiness/challenge_requirement in SDLC.py),
    mirroring what CrewAI's own Task(context=[...]) chaining does in API
    mode."""
    schema = json.dumps(output_model.model_json_schema())
    context_block = f"\n\n{extra_prompt_context}" if extra_prompt_context else ""
    prompt = (
        f"You are acting as: {role}\n"
        f"Your goal: {goal}\n"
        f"{backstory}\n\n"
        f"Task:\n{task_description}{context_block}\n\n"
        f"Expected output: {expected_output}\n\n"
        "Respond with ONLY a single valid JSON object matching this exact "
        f"JSON schema - no prose before or after it, no markdown fence:\n{schema}"
    )

    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", _clean_model_id(model)]
    if allowed_tools:
        cmd += ["--allowedTools", allowed_tools]
    else:
        cmd += ["--tools", ""]

    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=SUBSCRIPTION_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        print(f"::error::claude -p timed out after {SUBSCRIPTION_TIMEOUT_SECONDS}s")
        return None
    except OSError as exc:
        # Covers FileNotFoundError (the `claude` binary isn't on this
        # process's PATH - a real, observed failure mode on a freshly
        # set up self-hosted runner) and PermissionError alike. An
        # environment problem here is no different from any other
        # "couldn't get a result" case - it must degrade to None, not
        # crash the whole review run with an unhandled traceback.
        print(f"::error::couldn't run claude -p: {exc}")
        return None

    if proc.returncode != 0:
        print(f"::error::claude -p failed (exit {proc.returncode}): {proc.stderr.strip()[:500]}")
        return None

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError:
        print(f"::error::claude -p returned a non-JSON envelope: {proc.stdout[:500]}")
        return None

    result_text = envelope.get("result", "")
    json_text = _extract_json_object(result_text)
    if json_text is None:
        print(f"::error::no JSON object found in claude -p's response: {result_text[:500]}")
        return None

    try:
        return output_model.model_validate_json(json_text)
    except Exception as exc:
        print(f"::error::claude -p's response didn't match {output_model.__name__}: {exc}")
        return None


def run_agent(
    *,
    agent_key: str,
    task_key: str,
    inputs: dict[str, str],
    output_model: type[T],
    kickoff: Callable[[], object],
    agents_config: dict,
    tasks_config: dict,
    model: str,
    cwd: str,
    allowed_tools: str = "",
    extra_prompt_context: str = "",
) -> T | None:
    """Single entry point every SDLC.py stage function routes through:
    AGENT_BACKEND=subscription runs run_via_subscription above, anything
    else (including unset) runs the given CrewAI kickoff callable exactly
    as before - same try/except-return-None contract either way, so
    callers (retry loops, escalation logic) don't need to know which
    backend produced a None."""
    if os.environ.get("AGENT_BACKEND", "api") == "subscription":
        agent_cfg = agents_config[agent_key]
        task_cfg = tasks_config[task_key]
        return run_via_subscription(
            role=agent_cfg["role"],
            goal=agent_cfg["goal"],
            backstory=agent_cfg["backstory"],
            task_description=task_cfg["description"].format(**inputs),
            expected_output=task_cfg["expected_output"],
            output_model=output_model,
            model=model,
            cwd=cwd,
            allowed_tools=allowed_tools,
            extra_prompt_context=extra_prompt_context,
        )

    try:
        result = kickoff()
    except Exception:
        return None
    return result.pydantic if result.pydantic else None
