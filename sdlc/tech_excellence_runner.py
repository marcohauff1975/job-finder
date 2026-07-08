"""
Manual driver for the Technology Excellence panel - run this yourself
before (re-)publishing the Job Finder project, it isn't wired to any
GitHub Actions workflow (compare devops_agent_runner.py and
ai_prod_flow_runner.py, which read their inputs from CI env vars).

Always reviews the WHOLE Job Finder project - the app repo
(crewai-starter) and its sibling infra repo (crewai-infra) together in
one run, never just one - since that's the pairing a hiring manager
actually sees. Not for an arbitrary/unrelated repo: the panel's
personas and tools are written against this specific project's stack.

Usage:
    python -m sdlc.tech_excellence_runner [app_repo_path] [infra_repo_path]

Both default to JOB_FINDER_APP_REPO_PATH / JOB_FINDER_INFRA_REPO_PATH
in sdlc/SDLC.py (this repo, and its "../crewai-infra" sibling) - only
pass explicit paths if your checkouts live somewhere else.

Exit code is 0 only if the panel's synthesized verdict is
ready_to_publish; 1 if it found blocking issues or produced no result.
"""

import os
import sys
from datetime import date

from sdlc.SDLC import (
    JOB_FINDER_APP_REPO_PATH,
    JOB_FINDER_INFRA_REPO_PATH,
    LINKEDIN_ACTIVITY_LOG_DIR,
    review_project_readiness,
)


def main() -> int:
    args = sys.argv[1:]
    app_repo_path = os.path.abspath(args[0]) if len(args) > 0 else JOB_FINDER_APP_REPO_PATH
    infra_repo_path = os.path.abspath(args[1]) if len(args) > 1 else JOB_FINDER_INFRA_REPO_PATH

    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "Missing environment variable: ANTHROPIC_API_KEY. "
            "Add it to your .env file."
        )

    print("=== Technology Excellence panel reviewing the Job Finder project ===")
    print(f"  app repo:   {app_repo_path}")
    print(f"  infra repo: {infra_repo_path}\n")

    result = review_project_readiness(app_repo_path, infra_repo_path)

    if result is None:
        print("::error::technology_excellence_crew produced no result")
        return 1

    print(f"READY TO PUBLISH: {result.ready_to_publish}\n")
    print(f"Verdict: {result.verdict}\n")

    if result.blocking_issues:
        print("--- Blocking issues ---")
        for finding in result.blocking_issues:
            print(f"  - [{finding.repo}] {finding.issue}")
            print(f"    Why it matters: {finding.why_it_matters}")
            print(f"    Fix: {finding.fix}")
        print()

    if result.quick_wins:
        print("--- Quick wins ---")
        for win in result.quick_wins:
            print(f"  - {win}")
        print()

    if result.skills_showcase:
        print("--- Skills showcase (the commercial) ---")
        for point in result.skills_showcase:
            print(f"  - {point}")
        print()

    log_path = LINKEDIN_ACTIVITY_LOG_DIR / f"{date.today().isoformat()}-tech-excellence-review.md"
    print(f"LinkedIn activity log written to: {log_path}")

    return 0 if result.ready_to_publish else 1


if __name__ == "__main__":
    sys.exit(main())
