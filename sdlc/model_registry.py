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
    "pr_fix_agent": "PR Fix Agent",
    "pr_arbiter": "PR Arbiter (secondary approver)",
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

# agent_key -> {"api": (model id, reason), "subscription": (model id, reason)}.
# Two separate recommendations per agent, not one: the API recommendation
# is grounded in this pipeline's actual stakes per agent AND cost - cost
# per call is real money, so a mechanical, low-judgment agent stays cheap
# even if a stronger model would help marginally. The subscription
# recommendation drops cost from that tradeoff entirely (a flat-rate
# subscription, not metered per-token) - so it only asks "would a
# stronger model reduce a real judgment risk", and answers yes wherever
# the answer to that is yes, not just where the API recommendation
# already happened to be cheap. The two still land on the same tier for
# agents with no real judgment call to improve (local_tester,
# rollback_agent) and for agents already at the top tier either way
# (pr_arbiter, security_engineer) - the difference only shows up for
# agents the API recommendation capped at Sonnet mainly to keep cost
# proportionate to how often they run.
RECOMMENDATIONS = {
    "code_reviewer": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Gates every merge/deploy - the one thing standing between a "
            "bug or leaked secret and production. Highest-stakes "
            "per-commit call in the pipeline.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same highest-stakes gate, but cost per call isn't a reason to "
            "hold back on a subscription - the strongest tier only helps "
            "catch more.",
        ),
    },
    "local_tester": {
        "api": (
            "anthropic/claude-haiku-4-5-20251001",
            "Mechanical pass/fail: runs the app, reads logs, exercises one "
            "known flow. Little ambiguity to reason through.",
        ),
        "subscription": (
            "anthropic/claude-haiku-4-5-20251001",
            "Still mechanical pass/fail either way - a stronger model has "
            "no real judgment call here to improve, subscription or not.",
        ),
    },
    "ux_reviewer": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Judges rendered output against qualitative written "
            "guidelines - a subjective comparison that benefits from "
            "stronger reasoning.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same subjective judgment call, and nothing here to lose by "
            "going stronger once cost isn't the constraint.",
        ),
    },
    "prod_tester": {
        "api": (
            "anthropic/claude-sonnet-5",
            "This pipeline's one real incident (2026-07-08 false "
            "rollback) came from misreading ambiguous tool output, not "
            "missing data - exactly what a stronger model reduces.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same misread-ambiguous-output risk that caused a real "
            "incident - worth the strongest tier once it's not costing "
            "extra per call.",
        ),
    },
    "rollback_agent": {
        "api": (
            "anthropic/claude-haiku-4-5-20251001",
            "Only acts after a failure is already confirmed, then runs "
            "one fixed recovery action. No diagnosis or judgment call to "
            "get wrong.",
        ),
        "subscription": (
            "anthropic/claude-haiku-4-5-20251001",
            "Still a fixed recovery action either way - no judgment call "
            "for a stronger model to improve.",
        ),
    },
    "devops_agent": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Already deliberately upgraded (pushes an unreviewed fix "
            "straight to main and re-triggers prod). Keep here, or go "
            "Opus if it starts mis-diagnosing root causes.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Pushes an unreviewed fix straight to production - once cost "
            "isn't the reason to hold back, this is worth the strongest "
            "tier outright rather than waiting for it to start "
            "mis-diagnosing.",
        ),
    },
    "pr_fix_agent": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Writes real fixes to code review findings, but every fix "
            "gets re-reviewed by code_reviewer before anything can merge "
            "- a wrong fix is caught downstream, not shipped directly, so "
            "this doesn't need the strongest tier.",
        ),
        "subscription": (
            "anthropic/claude-sonnet-5",
            "Same reasoning as the API recommendation - the safety net is "
            "architectural (code_reviewer re-checks every fix), not cost- "
            "driven, so removing cost as a constraint doesn't change the "
            "case for a stronger tier here.",
        ),
    },
    "pr_arbiter": {
        "api": (
            "anthropic/claude-opus-4-8",
            "The actual final decision on whether an unresolved PR is "
            "safe to merge into production, with nobody else reviewing it "
            "afterward - Marco has explicitly said he can't make this "
            "call himself, so a wrong verdict here ships straight to real "
            "users. Same reasoning as security_engineer's tier.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same as the API recommendation - already the strongest tier "
            "available, so removing cost as a constraint doesn't change "
            "anything here.",
        ),
    },
    "cto": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Chairs an occasional, not per-commit, public-facing "
            "readiness review - current tier already matches the "
            "frequency and stakes.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same public-facing readiness verdict, worth the strongest "
            "tier once occasional-run cost isn't the limiting factor.",
        ),
    },
    "aws_lead_engineer": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Occasional infra review, already on the stronger tier - "
            "appropriate as-is.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same occasional infra review - worth the strongest tier once "
            "cost isn't the limiting factor.",
        ),
    },
    "python_lead_engineer": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Occasional code-quality review, already on the stronger tier "
            "- appropriate as-is.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same occasional code-quality review - worth the strongest "
            "tier once cost isn't the limiting factor.",
        ),
    },
    "data_engineer": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Occasional data/PII review, already on the stronger tier - "
            "appropriate as-is.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same occasional data/PII review - worth the strongest tier "
            "once cost isn't the limiting factor.",
        ),
    },
    "ai_engineer": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Occasional design review, already on the stronger tier - "
            "appropriate as-is.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same occasional design review - worth the strongest tier "
            "once cost isn't the limiting factor.",
        ),
    },
    "security_engineer": {
        "api": (
            "anthropic/claude-opus-4-8",
            "Sole veto power over publishing the repo - a missed secret "
            "here is a blocking, resume-linked leak, worth the strongest "
            "model even at occasional-run cost.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same as the API recommendation - already the strongest tier "
            "available, so removing cost as a constraint doesn't change "
            "anything here.",
        ),
    },
    "product_manager": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Resolves requirement ambiguity itself rather than bouncing "
            "it back - that judgment compounds through everything built "
            "afterward.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same compounding-judgment risk - worth the strongest tier "
            "once cost isn't the limiting factor.",
        ),
    },
    "software_architect": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Technical-direction mistakes (wrong infra call, missed "
            "non-functional requirement) are expensive to unwind once the "
            "engineer has already built against them.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same expensive-to-unwind risk - worth the strongest tier "
            "once cost isn't the limiting factor.",
        ),
    },
    "software_engineer": {
        "api": (
            "anthropic/claude-sonnet-5",
            "Feeds code_reviewer, which is being raised to the same tier "
            "- keeping both ends of the same pipeline balanced avoids one "
            "becoming the weak link.",
        ),
        "subscription": (
            "anthropic/claude-opus-4-8",
            "Same pipeline-balance reasoning - code_reviewer's "
            "subscription recommendation is Opus too, so this stays "
            "matched to it.",
        ),
    },
}


_LOCK = threading.Lock()


def _migrate_flat_entry(value: str | dict) -> dict[str, str]:
    """agent_models.json predates the API/subscription split - an entry
    still in the old flat {agent_key: model_id} form is migrated in
    memory (and rewritten to disk on the next load) to {"api": model_id,
    "subscription": model_id}, so an existing assignment doesn't silently
    change for either backend the moment this ships."""
    if isinstance(value, str):
        return {"api": value, "subscription": value}
    return value


def load_agent_models() -> dict[str, dict[str, str]]:
    with open(CONFIG_PATH, "r") as f:
        raw = json.load(f)

    migrated = {key: _migrate_flat_entry(value) for key, value in raw.items()}
    if migrated != raw:
        with open(CONFIG_PATH, "w") as f:
            json.dump(migrated, f, indent=2)
            f.write("\n")
    return migrated


def set_agent_model(agent_key: str, model_id: str, backend: str) -> None:
    """Persists the change for the given backend ("api" or
    "subscription") and, for "api" only, swaps the already-constructed
    Agent's .llm in place (see module docstring) - there's no equivalent
    constructed object for "subscription" to mutate, since sdlc/backend.py
    reads config/agent_models.json fresh on every call rather than
    caching a model at construction time, so persisting is already
    enough for that backend to pick the change up on its next call. Two
    admin sessions changing models around the same time would otherwise
    race on the read-modify-write against CONFIG_PATH (one session's
    change getting silently lost) - _LOCK serializes that. It does NOT
    protect a crew that's already mid-run elsewhere in the process from
    picking up the new model on its next step: agents are process-wide
    singletons (see sdlc/SDLC.py), by the same design as local_tester
    being reused across local_test_crew/performance_test_crew, so a
    change here is inherently visible to any crew using that agent,
    in-flight or not - that's the deliberate tradeoff of "takes effect
    immediately, no restart needed" and would need per-run agent
    instances to avoid, which this pipeline doesn't do anywhere today."""
    with _LOCK:
        current = load_agent_models()
        current.setdefault(agent_key, {})[backend] = model_id
        with open(CONFIG_PATH, "w") as f:
            json.dump(current, f, indent=2)
            f.write("\n")

        if backend != "api":
            return

        import sys

        sdlc_module = sys.modules.get("sdlc.SDLC")
        if sdlc_module is not None:
            agent = sdlc_module.AGENTS_BY_KEY.get(agent_key)
            if agent is not None:
                agent.llm = LLM(model=model_id)
