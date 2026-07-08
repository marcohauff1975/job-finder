"""
Per-agent Claude model assignment for the SDLC agents, editable live from
the admin "AI Models" tab in streamlit_app.py.

The current assignment per agent lives in config/agent_models.json (data,
mutable at runtime) rather than hardcoded in SDLC.py. set_agent_model()
both persists a change there and, if sdlc.SDLC has already been imported
in this process, mutates the already-constructed Agent object directly
(CrewAI's Agent.llm isn't frozen) - so a change made in the admin UI takes
effect on that agent's next run immediately, no app restart needed.
"""

import json
import threading
from pathlib import Path

from crewai import LLM

CONFIG_PATH = Path(__file__).parent / "config" / "agent_models.json"

MODEL_DISPLAY_NAMES = {
    "anthropic/claude-haiku-4-5-20251001": "Haiku 4.5 (fast, cheap)",
    "anthropic/claude-sonnet-5": "Sonnet 5 (balanced)",
    "anthropic/claude-opus-4-8": "Opus 4.8 (strongest, slowest/priciest)",
}

# Called only from a Claude Code session running SDLC.py's
# technology_excellence_crew (the pre-publish readiness review) - never
# invoked by the deployed Streamlit app itself, unlike every other agent
# below. Kept separate so the admin "AI Models" tab can show them in
# their own group instead of implying they're part of the live app's
# request path.
TECH_EXCELLENCE_AGENT_KEYS = [
    "cto",
    "aws_lead_engineer",
    "python_lead_engineer",
    "data_engineer",
    "ai_engineer",
    "security_engineer",
]

AGENT_DISPLAY_NAMES = {
    "code_reviewer": "Code Reviewer",
    "local_tester": "Local Tester",
    "ux_reviewer": "UX Reviewer",
    "prod_tester": "Prod Tester",
    "rollback_agent": "Rollback Agent",
    "devops_agent": "DevOps Agent",
    "cto": "CTO (readiness panel chair)",
    "aws_lead_engineer": "AWS Lead Engineer",
    "python_lead_engineer": "Python Lead Engineer",
    "data_engineer": "Data Engineer",
    "ai_engineer": "AI Engineer",
    "security_engineer": "Security Engineer",
    "product_manager": "Product Manager",
    "software_architect": "Software Architect",
    "software_engineer": "Software Engineer",
}

# agent_key -> (recommended model id, one-line reason). Grounded in this
# pipeline's own actual stakes per agent: whether it acts unattended on
# main/prod, how subjective its judgment call is, and how often it runs
# (occasional review panels can afford a stronger model than a per-commit
# gate has to run cheaply at volume) - not a blanket "bigger is better".
RECOMMENDATIONS = {
    "code_reviewer": (
        "anthropic/claude-sonnet-5",
        "Gates every merge/deploy - the one thing standing between a bug "
        "or leaked secret and production. Highest-stakes per-commit call "
        "in the pipeline.",
    ),
    "local_tester": (
        "anthropic/claude-haiku-4-5-20251001",
        "Mechanical pass/fail: runs the app, reads logs, exercises one "
        "known flow. Little ambiguity to reason through.",
    ),
    "ux_reviewer": (
        "anthropic/claude-sonnet-5",
        "Judges rendered output against qualitative written guidelines - "
        "a subjective comparison that benefits from stronger reasoning.",
    ),
    "prod_tester": (
        "anthropic/claude-sonnet-5",
        "This pipeline's one real incident (2026-07-08 false rollback) "
        "came from misreading ambiguous tool output, not missing data - "
        "exactly what a stronger model reduces.",
    ),
    "rollback_agent": (
        "anthropic/claude-haiku-4-5-20251001",
        "Only acts after a failure is already confirmed, then runs one "
        "fixed recovery action. No diagnosis or judgment call to get "
        "wrong.",
    ),
    "devops_agent": (
        "anthropic/claude-sonnet-5",
        "Already deliberately upgraded (pushes an unreviewed fix straight "
        "to main and re-triggers prod). Keep here, or go Opus if it starts "
        "mis-diagnosing root causes.",
    ),
    "cto": (
        "anthropic/claude-sonnet-5",
        "Chairs an occasional, not per-commit, public-facing readiness "
        "review - current tier already matches the frequency and stakes.",
    ),
    "aws_lead_engineer": (
        "anthropic/claude-sonnet-5",
        "Occasional infra review, already on the stronger tier - "
        "appropriate as-is.",
    ),
    "python_lead_engineer": (
        "anthropic/claude-sonnet-5",
        "Occasional code-quality review, already on the stronger tier - "
        "appropriate as-is.",
    ),
    "data_engineer": (
        "anthropic/claude-sonnet-5",
        "Occasional data/PII review, already on the stronger tier - "
        "appropriate as-is.",
    ),
    "ai_engineer": (
        "anthropic/claude-sonnet-5",
        "Occasional design review, already on the stronger tier - "
        "appropriate as-is.",
    ),
    "security_engineer": (
        "anthropic/claude-opus-4-8",
        "Sole veto power over publishing the repo - a missed secret here "
        "is a blocking, resume-linked leak, worth the strongest model even "
        "at occasional-run cost.",
    ),
    "product_manager": (
        "anthropic/claude-sonnet-5",
        "Resolves requirement ambiguity itself rather than bouncing it "
        "back - that judgment compounds through everything built "
        "afterward.",
    ),
    "software_architect": (
        "anthropic/claude-sonnet-5",
        "Technical-direction mistakes (wrong infra call, missed "
        "non-functional requirement) are expensive to unwind once the "
        "engineer has already built against them.",
    ),
    "software_engineer": (
        "anthropic/claude-sonnet-5",
        "Feeds code_reviewer, which is being raised to the same tier - "
        "keeping both ends of the same pipeline balanced avoids one "
        "becoming the weak link.",
    ),
}


_LOCK = threading.Lock()


def load_agent_models() -> dict[str, str]:
    with open(CONFIG_PATH, "r") as f:
        return json.load(f)


def set_agent_model(agent_key: str, model_id: str) -> None:
    """Persists the change and swaps the already-constructed Agent's
    .llm in place (see module docstring). Two admin sessions changing
    models around the same time would otherwise race on the
    read-modify-write against CONFIG_PATH (one session's change getting
    silently lost) - _LOCK serializes that. It does NOT protect a crew
    that's already mid-run elsewhere in the process from picking up the
    new model on its next step: agents are process-wide singletons (see
    sdlc/SDLC.py), by the same design as local_tester being reused
    across local_test_crew/performance_test_crew, so a change here is
    inherently visible to any crew using that agent, in-flight or not -
    that's the deliberate tradeoff of "takes effect immediately, no
    restart needed" and would need per-run agent instances to avoid,
    which this pipeline doesn't do anywhere today."""
    with _LOCK:
        current = load_agent_models()
        current[agent_key] = model_id
        with open(CONFIG_PATH, "w") as f:
            json.dump(current, f, indent=2)
            f.write("\n")

        import sys

        sdlc_module = sys.modules.get("sdlc.SDLC")
        if sdlc_module is not None:
            agent = sdlc_module.AGENTS_BY_KEY.get(agent_key)
            if agent is not None:
                agent.llm = LLM(model=model_id)
