"""
SDLC - Code Reviewer + Local Tester + UX Reviewer + Prod Tester +
Rollback agents.

Separate from the product agents in job_search.py: these agents help
review, test, and (if needed) roll back changes to this app itself -
they are dev/ops tooling, not something the deployed Streamlit app
ever imports or runs. Nothing under sdlc/ needs to reach production.

The agents and tasks are defined in plain text in:
    sdlc/config/agents.yaml
    sdlc/config/tasks.yaml
This file just loads those definitions, wires them together, and
exposes one function per stage.

Not yet wired up (deliberately left for a later pass):
- No tools are attached to code_reviewer, local_tester, prod_tester,
  or rollback_agent yet, so those four can only reason over whatever
  text you pass them (a diff, a log, a description of what to test) -
  they can't run git, pytest, ssh, or curl themselves. ux_reviewer is
  the exception: it has tools/ux_inspector.py, a Playwright-backed tool
  that actually drives the running app. Giving the other four
  agents that same kind of real access is the next step.
- No orchestration between stages (e.g. only calling rollback() if
  test_production() fails) - that control flow will live wherever
  this is eventually driven from (a script, or another agent).

Run directly for a quick manual test of the wiring (uses the DEMO_*
constants below):
    python SDLC.py
"""

import os
import sys
from pathlib import Path

import yaml
from crewai import Agent, Task, Crew, Process, LLM
from dotenv import load_dotenv
from pydantic import BaseModel

# Makes `sdlc.tools...` importable whether this file is run directly
# (python SDLC.py, per the docstring above) or imported as sdlc.SDLC.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdlc.tools.ux_inspector import UXPageInspectorTool

load_dotenv()


# --- Structured output shapes -----------------------------------------
# Defining these tells CrewAI exactly what fields each result must
# have, so results are reliable data (not just paragraphs of text).

class CodeReviewFinding(BaseModel):
    file: str
    line: str = ""
    risk: str


class CodeReviewResult(BaseModel):
    passed: bool
    findings: list[CodeReviewFinding] = []


class LocalTestResult(BaseModel):
    passed: bool
    steps_taken: str
    observation: str


class PerformanceTestResult(BaseModel):
    seconds: float
    passed: bool
    diagnosis: str = ""


class UXReviewFinding(BaseModel):
    guideline: str
    location: str
    issue: str


class UXReviewResult(BaseModel):
    passed: bool
    findings: list[UXReviewFinding] = []
    guidelines_stale_note: str = ""


class ProdTestResult(BaseModel):
    active_user_detected: bool
    passed: bool
    observation: str


class RollbackResult(BaseModel):
    current_commit: str
    rollback_test_passed: bool
    summary: str


# --- Demo inputs ----------------------------------------------------------
# Only used when running this file directly (python SDLC.py) for a
# quick wiring test.

DEMO_DIFF = "diff --git a/example.py b/example.py\n+print('hello')\n"
DEMO_CHANGE_SUMMARY = "Example change for testing the SDLC crew wiring."
DEMO_FLOW_TO_TEST = "Load the home page and confirm it renders."
DEMO_SERVICE_URL = "https://yourmagicaljobfinder.online"

# --- Load agent/task definitions from the config/ folder -----------------

CONFIG_DIR = Path(__file__).parent / "config"

with open(CONFIG_DIR / "agents.yaml", "r") as f:
    agents_config = yaml.safe_load(f)

with open(CONFIG_DIR / "tasks.yaml", "r") as f:
    tasks_config = yaml.safe_load(f)

with open(CONFIG_DIR / "ux_guidelines.md", "r") as f:
    UX_GUIDELINES = f.read()

# --- LLM (same Claude setup as job_search.py) --------------------------

claude = LLM(model="anthropic/claude-sonnet-5")

# --- Code Reviewer: agent + task + crew --------------------------------

code_reviewer = Agent(
    config=agents_config["code_reviewer"],
    llm=claude,
    verbose=True,
)

code_review_task = Task(
    config=tasks_config["code_review_task"],
    agent=code_reviewer,
    output_pydantic=CodeReviewResult,
)

code_review_crew = Crew(
    agents=[code_reviewer],
    tasks=[code_review_task],
    process=Process.sequential,
    verbose=True,
)

# --- Local Tester: agent + task + crew ----------------------------------

local_tester = Agent(
    config=agents_config["local_tester"],
    llm=claude,
    verbose=True,
)

local_test_task = Task(
    config=tasks_config["local_test_task"],
    agent=local_tester,
    output_pydantic=LocalTestResult,
)

local_test_crew = Crew(
    agents=[local_tester],
    tasks=[local_test_task],
    process=Process.sequential,
    verbose=True,
)

performance_test_task = Task(
    config=tasks_config["performance_test_task"],
    agent=local_tester,
    output_pydantic=PerformanceTestResult,
)

performance_test_crew = Crew(
    agents=[local_tester],
    tasks=[performance_test_task],
    process=Process.sequential,
    verbose=True,
)

# --- UX Reviewer: agent + task + crew -----------------------------------

ux_reviewer = Agent(
    config=agents_config["ux_reviewer"],
    llm=claude,
    tools=[UXPageInspectorTool()],
    verbose=True,
)

ux_review_task = Task(
    config=tasks_config["ux_review_task"],
    agent=ux_reviewer,
    output_pydantic=UXReviewResult,
)

ux_review_crew = Crew(
    agents=[ux_reviewer],
    tasks=[ux_review_task],
    process=Process.sequential,
    verbose=True,
)

# --- Prod Tester: agent + task + crew -----------------------------------

prod_tester = Agent(
    config=agents_config["prod_tester"],
    llm=claude,
    verbose=True,
)

prod_test_task = Task(
    config=tasks_config["prod_test_task"],
    agent=prod_tester,
    output_pydantic=ProdTestResult,
)

prod_test_crew = Crew(
    agents=[prod_tester],
    tasks=[prod_test_task],
    process=Process.sequential,
    verbose=True,
)

# --- Rollback Agent: agent + task + crew --------------------------------

rollback_agent = Agent(
    config=agents_config["rollback_agent"],
    llm=claude,
    verbose=True,
)

rollback_task = Task(
    config=tasks_config["rollback_task"],
    agent=rollback_agent,
    output_pydantic=RollbackResult,
)

rollback_crew = Crew(
    agents=[rollback_agent],
    tasks=[rollback_task],
    process=Process.sequential,
    verbose=True,
)


def review_code(diff: str) -> CodeReviewResult | None:
    """Run the code review crew over a diff and return the structured
    result, or None if it failed."""
    result = code_review_crew.kickoff(inputs={"diff": diff})
    return result.pydantic if result.pydantic else None


def test_locally(change_summary: str, flow_to_test: str) -> LocalTestResult | None:
    """Run the local test crew and return the structured result, or
    None if it failed."""
    inputs = {"change_summary": change_summary, "flow_to_test": flow_to_test}
    result = local_test_crew.kickoff(inputs=inputs)
    return result.pydantic if result.pydantic else None


def test_performance(
    change_summary: str, flow_to_test: str, baseline_seconds: float
) -> PerformanceTestResult | None:
    """Run the performance test crew (same local_tester agent as
    test_locally) and return the structured result, or None if it
    failed."""
    inputs = {
        "change_summary": change_summary,
        "flow_to_test": flow_to_test,
        "baseline_seconds": str(baseline_seconds),
    }
    result = performance_test_crew.kickoff(inputs=inputs)
    return result.pydantic if result.pydantic else None


def review_ux(
    change_summary: str, flow_to_test: str, base_url: str = "http://localhost:8501"
) -> UXReviewResult | None:
    """Run the UX review crew (against this app's own ux_guidelines.md)
    and return the structured result, or None if it failed."""
    inputs = {
        "change_summary": change_summary,
        "flow_to_test": flow_to_test,
        "ux_guidelines": UX_GUIDELINES,
        "base_url": base_url,
    }
    result = ux_review_crew.kickoff(inputs=inputs)
    return result.pydantic if result.pydantic else None


def test_production(
    service_url: str, change_summary: str, flow_to_test: str
) -> ProdTestResult | None:
    """Run the production smoke test crew and return the structured
    result, or None if it failed."""
    inputs = {
        "service_url": service_url,
        "change_summary": change_summary,
        "flow_to_test": flow_to_test,
    }
    result = prod_test_crew.kickoff(inputs=inputs)
    return result.pydantic if result.pydantic else None


def rollback(
    new_commit: str, failure_reason: str, previous_commit: str
) -> RollbackResult | None:
    """Run the rollback crew and return the structured result, or None
    if it failed. Only meant to be called after a confirmed
    test_production() failure."""
    inputs = {
        "new_commit": new_commit,
        "failure_reason": failure_reason,
        "previous_commit": previous_commit,
    }
    result = rollback_crew.kickoff(inputs=inputs)
    return result.pydantic if result.pydantic else None


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "Missing environment variable: ANTHROPIC_API_KEY. "
            "Add it to your .env file."
        )

    review = review_code(DEMO_DIFF)
    print("\n\n=== CODE REVIEW ===\n")
    print(review)
