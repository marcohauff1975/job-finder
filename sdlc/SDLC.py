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
- No tools are attached to code_reviewer or local_tester yet, so
  those two can only reason over whatever text you pass them (a diff,
  a description of what to test) - they can't run git or pytest
  themselves. ux_reviewer (tools/ux_inspector.py), prod_tester, and
  rollback_agent (both tools/prod_ops.py) already have real tools.
- No orchestration between stages (e.g. only calling rollback() if
  test_production() fails) - that control flow will live wherever
  this is eventually driven from (a script, or another agent).

Run directly for a quick manual test of the wiring (uses the DEMO_*
constants below):
    python SDLC.py
"""

import os
import sys
from datetime import date
from pathlib import Path

import yaml
from crewai import Agent, Task, Crew, Process, LLM
from crewai_tools import FileReadTool, FileWriterTool
from dotenv import load_dotenv
from pydantic import BaseModel

# Makes `sdlc.tools...` importable whether this file is run directly
# (python SDLC.py, per the docstring above) or imported as sdlc.SDLC.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdlc.tools.devops_ops import (
    CommitAndPushFixTool,
    FetchFailedRunLogsTool,
    RetriggerWorkflowTool,
)
from sdlc.tools.ux_inspector import UXPageInspectorTool
from sdlc.tools.prod_ops import ProdHealthCheckTool, ProdRollbackTool
from sdlc.tools.repo_audit import GitFileHistoryTool, GitRepoStatusTool, RepoFileReadTool
from sdlc.tools.aws_audit import AWSLiveSetupTool
from sdlc.tools.github_audit import GitHubLiveRepoCheckTool

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


class DevOpsFixResult(BaseModel):
    root_cause: str
    files_changed: list[str] = []
    fix_summary: str
    commit_pushed: bool
    workflow_retriggered: bool


class ReadinessFinding(BaseModel):
    repo: str  # e.g. "crewai-starter", "crewai-infra", "crewai-infra (live AWS account)", "crewai-starter (GitHub)"
    issue: str
    why_it_matters: str
    fix: str


class PersonaReviewResult(BaseModel):
    persona: str
    passed: bool
    blocking: bool = False
    findings: list[ReadinessFinding] = []
    skill_signal: str = ""


class ReadinessReviewResult(BaseModel):
    ready_to_publish: bool
    verdict: str
    blocking_issues: list[ReadinessFinding] = []
    quick_wins: list[str] = []
    skills_showcase: list[str] = []


# --- Demo inputs ----------------------------------------------------------
# Only used when running this file directly (python SDLC.py) for a
# quick wiring test.

DEMO_DIFF = "diff --git a/example.py b/example.py\n+print('hello')\n"
DEMO_CHANGE_SUMMARY = "Example change for testing the SDLC crew wiring."
DEMO_FLOW_TO_TEST = "Load the home page and confirm it renders."
DEMO_SERVICE_URL = "https://yourmagicaljobfinder.online"

# The Job Finder project is always these two repos together - the app
# repo (this one) and its sibling infra repo. Not user-configurable
# beyond overriding these paths; the panel's personas/tools are written
# against this specific pairing, not an arbitrary repo.
JOB_FINDER_APP_REPO_PATH = str(REPO_ROOT)
JOB_FINDER_INFRA_REPO_PATH = str(REPO_ROOT.parent / "crewai-infra")

# Same folder/format Marco already exports to by hand after a Claude
# Code session worth remembering - see the HOW-TO and existing examples
# in that folder. The weekly LinkedIn post generator scans this folder
# for new files each run, so writing here is the only integration point
# needed; nothing here ever posts to LinkedIn itself.
LINKEDIN_ACTIVITY_LOG_DIR = (
    REPO_ROOT.parent / "AI linkedin Posts" / "linkedin blogs" / "code-activity"
)

# --- Load agent/task definitions from the config/ folder -----------------

CONFIG_DIR = Path(__file__).parent / "config"

with open(CONFIG_DIR / "agents.yaml", "r") as f:
    agents_config = yaml.safe_load(f)

with open(CONFIG_DIR / "tasks.yaml", "r") as f:
    tasks_config = yaml.safe_load(f)

with open(CONFIG_DIR / "ux_guidelines.md", "r") as f:
    UX_GUIDELINES = f.read()

# --- LLM (same Claude setup as job_search.py) --------------------------
# All 5 agents run on the cheap model for now - Marco plans to move
# specific ones (likely code_reviewer/ux_reviewer, which need more
# judgment) back to claude_high later.

claude_small = LLM(model="anthropic/claude-haiku-4-5-20251001")
claude_high = LLM(model="anthropic/claude-sonnet-5")

# --- Code Reviewer: agent + task + crew --------------------------------

code_reviewer = Agent(
    config=agents_config["code_reviewer"],
    llm=claude_small,
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
    llm=claude_small,
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
    llm=claude_small,
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
    llm=claude_small,
    tools=[ProdHealthCheckTool()],
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
    llm=claude_small,
    tools=[ProdRollbackTool()],
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

# --- DevOps Agent: agent + task + crew -----------------------------------
# Deliberately on claude_high rather than claude_small like the other 5
# agents for now - this one pushes a fix straight to origin/main and
# re-triggers a production deploy with no human review step in
# between, so a wrong diagnosis here is more costly than in the other
# agents' review/report-only roles.

devops_agent = Agent(
    config=agents_config["devops_agent"],
    llm=claude_high,
    tools=[
        FetchFailedRunLogsTool(),
        FileReadTool(),
        FileWriterTool(),
        CommitAndPushFixTool(),
        RetriggerWorkflowTool(),
    ],
    verbose=True,
)

devops_fix_task = Task(
    config=tasks_config["devops_fix_task"],
    agent=devops_agent,
    output_pydantic=DevOpsFixResult,
)

devops_fix_crew = Crew(
    agents=[devops_agent],
    tasks=[devops_fix_task],
    process=Process.sequential,
    verbose=True,
)


# --- Technology Excellence panel: 6 agents + 7 tasks + crew ------------
# Scoped specifically to the Job Finder project as a whole - always
# BOTH the app repo (crewai-starter) and its sibling infra repo
# (crewai-infra) together, never just one - not a general-purpose
# "review any repo" tool. Job Finder is Marco's portfolio piece, linked
# directly from his resume, so this panel's job is broader than
# catching red flags: it's meant to work out whether the project
# functions as a credible, evidence-backed commercial for his technical
# abilities, and to say plainly what it currently demonstrates (see
# skill_signal/skills_showcase below) as well as what would break the
# pitch - in either repo. Not a per-commit gate like code_reviewer -
# this runs on demand, before (re-)publishing. All six run on
# claude_high: a wrong call here is either a missed leak
# (security_engineer) or an embarrassing/overstated public claim, so
# quality matters more than cost for an occasional review. Every
# persona shares the same read-only toolset (real git status/tracked
# files, whether a given path was ever committed, and file reading) so
# findings are grounded in what's actually in each repo, not
# assumption. Uses RepoFileReadTool (see tools/repo_audit.py), not
# crewai_tools' FileReadTool/DirectoryReadTool - those are sandboxed to
# os.getcwd(), which would silently block reading crewai-infra (a
# sibling directory, not the cwd), and DirectoryReadTool's unfiltered
# os.walk would dump crewai-starter's entire committed-but-gitignored
# venv/ (30k+ files) into a single tool result. git_repo_status's
# tracked_files (git ls-files) is the bounded, .gitignore-aware
# listing instead.

_readiness_tools = [
    GitRepoStatusTool(),
    GitFileHistoryTool(),
    RepoFileReadTool(allowed_roots=[str(REPO_ROOT.parent)]),
]

# aws_lead_engineer and security_engineer also get aws_live_setup_check
# (sdlc/tools/aws_audit.py) - the only two personas who need to compare
# the real, currently-running AWS account against what the Terraform
# code in crewai-infra claims, since drift between the two (e.g. a
# policy attached by hand) is invisible to a code-only review.
_aws_live_tools = _readiness_tools + [AWSLiveSetupTool()]

# cto and security_engineer also get github_live_repo_check (sdlc/tools/
# github_audit.py) - what a real visitor/GitHub itself actually sees
# (is the repo even public, are Dependabot/secret-scanning turned on)
# rather than only local git state, which can't show any of that.
_cto_tools = _readiness_tools + [GitHubLiveRepoCheckTool()]
_security_tools = _aws_live_tools + [GitHubLiveRepoCheckTool()]

cto = Agent(config=agents_config["cto"], llm=claude_high, tools=_cto_tools, verbose=True)
aws_lead_engineer = Agent(
    config=agents_config["aws_lead_engineer"], llm=claude_high, tools=_aws_live_tools, verbose=True
)
python_lead_engineer = Agent(
    config=agents_config["python_lead_engineer"], llm=claude_high, tools=_readiness_tools, verbose=True
)
data_engineer = Agent(
    config=agents_config["data_engineer"], llm=claude_high, tools=_readiness_tools, verbose=True
)
ai_engineer = Agent(
    config=agents_config["ai_engineer"], llm=claude_high, tools=_readiness_tools, verbose=True
)
security_engineer = Agent(
    config=agents_config["security_engineer"], llm=claude_high, tools=_security_tools, verbose=True
)

cto_review_task = Task(
    config=tasks_config["cto_review_task"], agent=cto, output_pydantic=PersonaReviewResult
)
aws_review_task = Task(
    config=tasks_config["aws_review_task"], agent=aws_lead_engineer, output_pydantic=PersonaReviewResult
)
python_review_task = Task(
    config=tasks_config["python_review_task"],
    agent=python_lead_engineer,
    output_pydantic=PersonaReviewResult,
)
data_review_task = Task(
    config=tasks_config["data_review_task"], agent=data_engineer, output_pydantic=PersonaReviewResult
)
ai_review_task = Task(
    config=tasks_config["ai_review_task"], agent=ai_engineer, output_pydantic=PersonaReviewResult
)
security_review_task = Task(
    config=tasks_config["security_review_task"],
    agent=security_engineer,
    output_pydantic=PersonaReviewResult,
)

readiness_synthesis_task = Task(
    config=tasks_config["readiness_synthesis_task"],
    agent=cto,
    context=[
        cto_review_task,
        aws_review_task,
        python_review_task,
        data_review_task,
        ai_review_task,
        security_review_task,
    ],
    output_pydantic=ReadinessReviewResult,
)

technology_excellence_crew = Crew(
    agents=[cto, aws_lead_engineer, python_lead_engineer, data_engineer, ai_engineer, security_engineer],
    tasks=[
        cto_review_task,
        aws_review_task,
        python_review_task,
        data_review_task,
        ai_review_task,
        security_review_task,
        readiness_synthesis_task,
    ],
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
    service_url: str,
    change_summary: str,
    flow_to_test: str,
    instance_ip: str,
    key_path: str,
    remote_user: str,
    app_port: int = 8501,
) -> ProdTestResult | None:
    """Run the production smoke test crew and return the structured
    result, or None if it failed. prod_tester has a real tool
    (check_production_health) and is given the SSH connection details
    it needs to call it - this function doesn't check anything itself,
    the agent does."""
    inputs = {
        "service_url": service_url,
        "change_summary": change_summary,
        "flow_to_test": flow_to_test,
        "instance_ip": instance_ip,
        "key_path": key_path,
        "remote_user": remote_user,
        "app_port": str(app_port),
    }
    result = prod_test_crew.kickoff(inputs=inputs)
    return result.pydantic if result.pydantic else None


def rollback(
    new_commit: str,
    failure_reason: str,
    previous_commit: str,
    instance_ip: str,
    key_path: str,
    remote_user: str,
    remote_app_dir: str,
    service_name: str,
    service_url: str,
) -> RollbackResult | None:
    """Run the rollback crew and return the structured result, or None
    if it failed. Only meant to be called after a confirmed
    test_production() failure. rollback_agent has a real tool
    (rollback_production) and is given the SSH connection details it
    needs to call it - this function doesn't revert anything itself,
    the agent does."""
    inputs = {
        "new_commit": new_commit,
        "failure_reason": failure_reason,
        "previous_commit": previous_commit,
        "instance_ip": instance_ip,
        "key_path": key_path,
        "remote_user": remote_user,
        "remote_app_dir": remote_app_dir,
        "service_name": service_name,
        "service_url": service_url,
    }
    result = rollback_crew.kickoff(inputs=inputs)
    return result.pydantic if result.pydantic else None


def _write_linkedin_activity_log(result: ReadinessReviewResult) -> Path | None:
    """Writes today's panel run into LINKEDIN_ACTIVITY_LOG_DIR, in the
    exact format Marco already exports by hand (see that folder's own
    HOW-TO-export-from-claude-code.md): a dated file, third person,
    under 20 lines, ending in a CONFIDENTIAL yes/no flag - so the weekly
    LinkedIn post generator picks this run up automatically the same
    way it picks up a manual session export, no separate integration
    needed. One file per day: a same-day rerun overwrites it, which is
    correct - each day's file should reflect the project's latest
    state, not accumulate stale duplicate entries. blocking_issues
    force CONFIDENTIAL: yes, since an unresolved leak or overprivileged
    credential isn't safe to summarize publicly yet, even in outline
    form, until it's actually fixed. Returns None (after printing a
    warning) if the log directory can't be created or written to - this
    export is a side effect of a review run, not its point, so a full
    readiness verdict should never be lost just because this optional
    step failed."""
    today = date.today().isoformat()
    path = LINKEDIN_ACTIVITY_LOG_DIR / f"{today}-tech-excellence-review.md"

    decisions = "\n".join(f"- {point}" for point in result.skills_showcase) or (
        "- No standout technical signal yet - see quick wins instead."
    )
    confidential = "yes" if result.blocking_issues else "no"

    content = f"""# {today} — Job Finder: Technology Excellence readiness review

Marco ran his Technology Excellence panel - six AI personas (CTO, AWS \
Lead, Python Lead, Data Engineer, AI Engineer, Security Engineer) - \
against both Job Finder repos (the app and its infra) and the real, \
live AWS account behind them, to check whether the project is ready to \
publish and link from his resume.

## The task

Confirm the project reads as a credible, evidence-backed demonstration \
of Marco's technical abilities to a hiring manager who only has a few \
minutes to look - not just that nothing is broken.

## Key decisions or approaches

{decisions}

## Why it matters

{result.verdict}

CONFIDENTIAL: {confidential}
"""
    try:
        LINKEDIN_ACTIVITY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    except OSError as e:
        print(f"::warning::Couldn't write LinkedIn activity log to {path}: {e}")
        return None
    return path


def review_project_readiness(
    app_repo_path: str = JOB_FINDER_APP_REPO_PATH,
    infra_repo_path: str = JOB_FINDER_INFRA_REPO_PATH,
) -> ReadinessReviewResult | None:
    """Run the Technology Excellence panel (CTO, AWS/Python/Data/AI
    leads, Security) over the WHOLE Job Finder project - always both
    the app repo (crewai-starter, app_repo_path) and its infra repo
    (crewai-infra, infra_repo_path) together, never just one - and
    return the CTO's synthesized verdict on whether the project as a
    whole reads as a credible commercial for Marco's technical
    abilities: is it ready_to_publish, what's blocking (each finding
    tagged with which repo it's in), and what it already demonstrates
    (skills_showcase). Returns None if the crew failed to produce a
    result. Both paths must be real git checkouts - the panel's tools
    read each repo's actual tracked files and history, not just what's
    on disk. On a successful run, also writes today's result into the
    LinkedIn activity-log pipeline (_write_linkedin_activity_log) -
    this always happens, not just on request, since that's the whole
    point of running this panel regularly."""
    inputs = {"app_repo_path": app_repo_path, "infra_repo_path": infra_repo_path}
    result = technology_excellence_crew.kickoff(inputs=inputs)
    readiness_result = result.pydantic if result.pydantic else None
    if readiness_result is not None:
        _write_linkedin_activity_log(readiness_result)
    return readiness_result


def fix_deploy_failure(
    workflow_name: str, workflow_file: str, run_id: str
) -> DevOpsFixResult | None:
    """Run the devops-agent crew against a just-failed deploy workflow
    run and return the structured result, or None if it failed. Meant
    to be called at most once per failure - see devops_agent's own
    backstory and .github/workflows/devops-agent.yml's guard step for
    how a repeat failure right after an auto-fix is handled instead of
    calling this again."""
    inputs = {
        "workflow_name": workflow_name,
        "workflow_file": workflow_file,
        "run_id": run_id,
    }
    result = devops_fix_crew.kickoff(inputs=inputs)
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
