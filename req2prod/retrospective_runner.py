"""
Driver for the retrospective (continuous-learning) loop - see
req2prod/AGENT_INTELLIGENCE_PHASE3.md. Given a notable outcome, it drafts
at most one lesson (Haiku), has pr_arbiter approve it, and opens a gated
PR appending it to req2prod/lessons/<agent>.md. It never merges anything.

Used by the DevOps circuit-breaker trigger (see
.github/workflows/devops-agent.yml). The rollback trigger calls
propose_lesson() directly from ai_prod_flow_runner.py instead.

Reads its parameters from environment variables:
    RETRO_TRIGGER          short label for what happened
    RETRO_CANDIDATE_AGENT  the agent most likely to need a lesson
    RETRO_CONTEXT          full free-text context of the outcome

Always exits 0 - a retrospective is best-effort and must never fail the
workflow that invoked it.
"""

import os

from req2prod.Req2Prod import propose_lesson


def main() -> int:
    trigger = os.environ.get("RETRO_TRIGGER", "").strip()
    candidate = os.environ.get("RETRO_CANDIDATE_AGENT", "").strip()
    context = os.environ.get("RETRO_CONTEXT", "").strip()

    if not (trigger and candidate and context):
        print(
            "retrospective: missing RETRO_TRIGGER / RETRO_CANDIDATE_AGENT / "
            "RETRO_CONTEXT - nothing to do."
        )
        return 0

    print(f"=== Retrospective for '{trigger}' (candidate agent: {candidate}) ===")
    try:
        print(propose_lesson(trigger, context, candidate))
    except Exception as e:  # best-effort: never fail the calling workflow
        print(f"::warning::retrospective failed (ignored): {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
