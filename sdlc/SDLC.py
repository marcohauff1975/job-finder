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
- No tools are attached to local_tester yet, so it can only reason
  over whatever text you pass it (a description of what to test) - it
  can't run git or pytest itself. ux_reviewer (tools/ux_inspector.py),
  prod_tester, rollback_agent (both tools/prod_ops.py), and
  code_reviewer (tools/dependency_check.py) already have real tools.
- No orchestration between stages (e.g. only calling rollback() if
  test_production() fails) - that control flow will live wherever
  this is eventually driven from (a script, or another agent).

Run directly for a quick manual test of the wiring (uses the DEMO_*
constants below):
    python SDLC.py
"""

import os
import re
import sys
from datetime import date
from pathlib import Path

import yaml
from crewai import Agent, Task, Crew, Process, LLM
from crewai_tools import FileReadTool, FileWriterTool
from dotenv import load_dotenv
from pydantic import BaseModel

from sdlc.model_registry import load_agent_models

# Makes `sdlc.tools...` importable whether this file is run directly
# (python SDLC.py, per the docstring above) or imported as sdlc.SDLC.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sdlc.tools.dependency_check import AnthropicModelCheckTool, PackageVersionCheckTool
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
from sdlc.tools.feature_build_ops import CreateFeatureBranchAndOpenPRTool
from sdlc.tools.build_workspace import (
    build_workspace,
    WorkspaceEditTool,
    WorkspaceFileReadTool,
    WorkspaceFileWriterTool,
)

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


class PRFixResult(BaseModel):
    files_changed: list[str] = []
    fix_summary: str


class ArbiterVerdict(BaseModel):
    safe_to_merge: bool
    reasoning: str
    blocking_reasons: list[str] = []


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


class FeatureRequirementsResult(BaseModel):
    user_story: str
    acceptance_criteria: list[str] = []
    open_questions: list[str] = []
    ready_for_development: bool


class ArchitectureDirectionResult(BaseModel):
    builds_on_existing_app: bool
    new_infrastructure_needed: list[str] = []
    non_functional_requirements: list[str] = []
    technical_notes: str
    clarifications_needed: list[str] = []
    ready_for_development: bool


# Matches build_feature()'s validation of FeatureBuildResult.pr_url -
# a real PR URL from create_feature_branch_and_open_pr's own REST API
# response, not just any non-empty string. Needed after observing
# (2026-07-10) that when the real tool call failed, the engineer
# fabricated a plausible-looking fake URL for a different org/repo
# entirely (github.com/mhauff/crewai-starter/pull/[PR_NUMBER], with an
# unfilled placeholder) rather than reporting the failure - an empty-
# string check alone doesn't catch that.
_PR_URL_PATTERN = re.compile(r"^https://github\.com/marcohauff1975/job-finder/pull/\d+$")


class FeatureBuildResult(BaseModel):
    branch_name: str
    files_changed: list[str] = []
    summary: str
    pr_url: str = ""
    questions_asked: list[str] = []


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

# --- LLM ------------------------------------------------------------------
# Which Claude model each agent below runs on is data, not code: it lives
# in config/agent_models.json and is editable live from the admin "AI
# Models" tab in streamlit_app.py (see sdlc/model_registry.py). _llm()
# just looks up that agent's current assignment at import time.

_agent_models = load_agent_models()


def _llm(agent_key: str) -> LLM:
    return LLM(model=_agent_models[agent_key])


# --- Code Reviewer: agent + task + crew --------------------------------

code_reviewer = Agent(
    config=agents_config["code_reviewer"],
    llm=_llm("code_reviewer"),
    tools=[PackageVersionCheckTool(), AnthropicModelCheckTool()],
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
    llm=_llm("local_tester"),
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
    llm=_llm("ux_reviewer"),
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
    llm=_llm("prod_tester"),
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
    llm=_llm("rollback_agent"),
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
# Deliberately on a stronger model than the other 5 agents (see
# config/agent_models.json) - this one pushes a fix straight to
# origin/main and re-triggers a production deploy with no human review
# step in between, so a wrong diagnosis here is more costly than in the
# other agents' review/report-only roles.

devops_agent = Agent(
    config=agents_config["devops_agent"],
    llm=_llm("devops_agent"),
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

# --- PR Fix Agent + Arbiter: close the review/fix loop without Marco ---
# Marco isn't a developer and can't judge code_reviewer's findings
# himself, so these two exist to make sure a PR never needs him as the
# fallback. pr_fix_agent only edits files - it never commits or pushes
# itself (see sdlc/code_review_runner.py, which owns git end to end and
# pushes once, after every attempt is done, not once per attempt - see
# that file's own docstring for why). pr_arbiter is the real final
# call once the review/fix loop is exhausted: unlike the old behavior
# it replaces (auto-approve after N rounds regardless of remaining
# findings), it actually judges whether what's left is safe to ship,
# and defaults to leaving the PR open and unmerged - not asking Marco -
# when it isn't.

pr_fix_agent = Agent(
    config=agents_config["pr_fix_agent"],
    llm=_llm("pr_fix_agent"),
    tools=[FileReadTool(), FileWriterTool()],
    verbose=True,
)

pr_fix_task = Task(
    config=tasks_config["pr_fix_task"],
    agent=pr_fix_agent,
    output_pydantic=PRFixResult,
)

pr_fix_crew = Crew(
    agents=[pr_fix_agent],
    tasks=[pr_fix_task],
    process=Process.sequential,
    verbose=True,
)

pr_arbiter = Agent(
    config=agents_config["pr_arbiter"],
    llm=_llm("pr_arbiter"),
    tools=[FileReadTool()],
    verbose=True,
)

pr_arbiter_task = Task(
    config=tasks_config["pr_arbiter_task"],
    agent=pr_arbiter,
    output_pydantic=ArbiterVerdict,
)

pr_arbiter_crew = Crew(
    agents=[pr_arbiter],
    tasks=[pr_arbiter_task],
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
# this runs on demand, before (re-)publishing. All six run on the
# stronger tier by default (see config/agent_models.json): a wrong call
# here is either a missed leak (security_engineer) or an
# embarrassing/overstated public claim, so quality matters more than
# cost for an occasional review. Every
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

cto = Agent(config=agents_config["cto"], llm=_llm("cto"), tools=_cto_tools, verbose=True)
aws_lead_engineer = Agent(
    config=agents_config["aws_lead_engineer"], llm=_llm("aws_lead_engineer"), tools=_aws_live_tools, verbose=True
)
python_lead_engineer = Agent(
    config=agents_config["python_lead_engineer"],
    llm=_llm("python_lead_engineer"),
    tools=_readiness_tools,
    verbose=True,
)
data_engineer = Agent(
    config=agents_config["data_engineer"], llm=_llm("data_engineer"), tools=_readiness_tools, verbose=True
)
ai_engineer = Agent(
    config=agents_config["ai_engineer"], llm=_llm("ai_engineer"), tools=_readiness_tools, verbose=True
)
security_engineer = Agent(
    config=agents_config["security_engineer"], llm=_llm("security_engineer"), tools=_security_tools, verbose=True
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


# --- Feature Build pipeline: Product Manager -> Software Architect ------
# -> Software Engineer. Scoped to this repo (crewai-starter) only -
# software_engineer never touches the sibling crewai-infra repo. Model
# tier per agent is set in config/agent_models.json, editable from the
# admin "AI Models" tab - see sdlc/model_registry.py for the current
# recommendation and reasoning per agent.
# software_engineer has allow_delegation=True, which gives it CrewAI's
# built-in "ask question to coworker" tool - so it can genuinely turn
# to product_manager for functional questions or software_architect for
# technical ones mid-task, targeting them by their `role` string, rather
# than guessing. Nothing here merges anything: software_engineer's own
# tool (create_feature_branch_and_open_pr) only ever opens a PR against
# main - the existing "PR code review" workflow and required-review
# branch protection take it from there, same as any human-authored PR.

product_manager = Agent(config=agents_config["product_manager"], llm=_llm("product_manager"), verbose=True)

software_architect = Agent(
    config=agents_config["software_architect"], llm=_llm("software_architect"), verbose=True
)

software_engineer = Agent(
    config=agents_config["software_engineer"],
    llm=_llm("software_engineer"),
    # WorkspaceFileReadTool/WorkspaceFileWriterTool, not the plain
    # crewai_tools versions used elsewhere in this file - those resolve
    # paths against whatever the process's cwd happens to be, which in
    # production is the live app's own directory. These are pinned to
    # the current build's isolated workspace instead (see
    # tools/build_workspace.py's module docstring for why).
    tools=[
        WorkspaceFileReadTool(),
        WorkspaceEditTool(),
        WorkspaceFileWriterTool(),
        CreateFeatureBranchAndOpenPRTool(),
    ],
    allow_delegation=True,
    verbose=True,
    # Default is 25 (crewai.Agent). Observed on production: wrong
    # file-path guesses (app.py, main.py, Home.py, pages/...) and a
    # malformed file_writer_tool call alone burned well over half that
    # budget before the agent ever reached create_feature_branch_and_
    # open_pr, at which point it wrote a prose "I'll do X" answer
    # instead of actually calling the tool - consistent with running
    # low on iterations rather than not understanding the instruction.
    max_iter=40,
)

feature_requirements_task = Task(
    config=tasks_config["feature_requirements_task"],
    agent=product_manager,
    output_pydantic=FeatureRequirementsResult,
)

architecture_direction_task = Task(
    config=tasks_config["architecture_direction_task"],
    agent=software_architect,
    context=[feature_requirements_task],
    output_pydantic=ArchitectureDirectionResult,
)

# feature_build_task deliberately does NOT use context=[...] chaining to
# feature_requirements_task/architecture_direction_task. It's meant to
# run standalone, fed the already-approved PM requirements and Architect
# direction as plain text (see build_feature() below) from whatever
# earlier Requirements Challenge conversation produced them - which may
# have been several turns, resolved over multiple challenge_requirement()
# calls, possibly a while ago. Relying on context=[...] would only work
# if those exact Task objects had just run within the same crew kickoff,
# which isn't the case here and would also mean two concurrent admin
# sessions could clobber each other's shared Task.output.
feature_build_task = Task(
    config=tasks_config["feature_build_task"],
    agent=software_engineer,
    output_pydantic=FeatureBuildResult,
)

# agents includes product_manager/software_architect (not just
# software_engineer) so software_engineer's allow_delegation "ask
# question to coworker" tool has live coworkers to target by role if it
# genuinely needs to ask one mid-build - but tasks only lists
# feature_build_task, so kicking off this crew does not re-run
# feature_requirements_task/architecture_direction_task.
feature_build_crew = Crew(
    agents=[product_manager, software_architect, software_engineer],
    tasks=[feature_build_task],
    process=Process.sequential,
    verbose=True,
)

# Backs the admin-only "Request a New Feature" chat page in
# streamlit_app.py: reuses the same product_manager/software_architect
# agents and feature_requirements_task/architecture_direction_task
# above (same reuse pattern as local_tester across local_test_crew and
# performance_test_crew) - just stops after the Architect's direction
# instead of continuing on to software_engineer, since this page is
# for Marco to be challenged and discuss a feature idea, not to have it
# built and PR'd automatically.
requirements_challenge_crew = Crew(
    agents=[product_manager, software_architect],
    tasks=[feature_requirements_task, architecture_direction_task],
    process=Process.sequential,
    verbose=True,
)

# Looked up by sdlc.model_registry.set_agent_model() so a model change
# made from the admin "AI Models" tab can mutate the already-constructed
# Agent's .llm directly, taking effect immediately in this running
# process instead of only on the next restart.
AGENTS_BY_KEY = {
    "code_reviewer": code_reviewer,
    "local_tester": local_tester,
    "ux_reviewer": ux_reviewer,
    "prod_tester": prod_tester,
    "rollback_agent": rollback_agent,
    "devops_agent": devops_agent,
    "pr_fix_agent": pr_fix_agent,
    "pr_arbiter": pr_arbiter,
    "cto": cto,
    "aws_lead_engineer": aws_lead_engineer,
    "python_lead_engineer": python_lead_engineer,
    "data_engineer": data_engineer,
    "ai_engineer": ai_engineer,
    "security_engineer": security_engineer,
    "product_manager": product_manager,
    "software_architect": software_architect,
    "software_engineer": software_engineer,
}


def review_code(diff: str) -> CodeReviewResult | None:
    """Run the code review crew over a diff and return the structured
    result, or None if it failed."""
    try:
        result = code_review_crew.kickoff(inputs={"diff": diff})
    except Exception:
        # Same defensive catch as fix_review_findings/arbiter_review below -
        # an empty/None LLM response (a known intermittent CrewAI/provider
        # flake, not a verdict on the diff) must not crash the whole review
        # run; the caller (sdlc/code_review_runner.py) retries on None.
        return None
    return result.pydantic if result.pydantic else None


def _format_findings_for_agent(findings: list[CodeReviewFinding]) -> str:
    return "\n".join(
        f"- {f.file}:{f.line} - {f.risk}" if f.line else f"- {f.file} - {f.risk}"
        for f in findings
    )


def fix_review_findings(findings: list[CodeReviewFinding]) -> PRFixResult | None:
    """Run pr_fix_agent against code_reviewer's findings and return what
    it changed, or None if the crew failed to produce a result. Only
    edits files in the current working tree - never commits, pushes, or
    opens anything itself; the calling script (sdlc/
    code_review_runner.py) owns git end to end and pushes once, after
    every attempt is done, not once per attempt."""
    inputs = {"findings": _format_findings_for_agent(findings)}
    try:
        result = pr_fix_crew.kickoff(inputs=inputs)
    except Exception:
        # Same defensive catch as build_feature() below, for the same
        # reason: a final answer that mixes prose with a JSON block can
        # make CrewAI's partial-JSON handling raise a bare pydantic
        # ValidationError instead of falling back gracefully, and that
        # must not escape this function uncaught.
        return None
    return result.pydantic if result.pydantic else None


def arbiter_review(diff: str, findings: list[CodeReviewFinding]) -> ArbiterVerdict | None:
    """Run pr_arbiter - the real, final judgment call once
    code_reviewer/pr_fix_agent have exhausted their review/fix rounds
    without converging - and return its verdict, or None if the crew
    failed to produce a result."""
    inputs = {"diff": diff, "findings": _format_findings_for_agent(findings)}
    try:
        result = pr_arbiter_crew.kickoff(inputs=inputs)
    except Exception:
        return None
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


def _format_requirements_for_engineer(result: FeatureRequirementsResult) -> str:
    lines = [f"User story: {result.user_story}", "Acceptance criteria:"]
    lines += [f"- {criterion}" for criterion in result.acceptance_criteria]
    return "\n".join(lines)


def _format_architecture_for_engineer(result: ArchitectureDirectionResult) -> str:
    lines = [
        "Builds on the existing app as-is."
        if result.builds_on_existing_app
        else "Needs new infrastructure or a separate service."
    ]
    if result.new_infrastructure_needed:
        lines.append("New infrastructure needed:")
        lines += [f"- {item}" for item in result.new_infrastructure_needed]
    if result.non_functional_requirements:
        lines.append("Non-functional requirements:")
        lines += [f"- {item}" for item in result.non_functional_requirements]
    lines.append(f"Technical notes: {result.technical_notes}")
    return "\n".join(lines)


def build_feature(
    pm_result: FeatureRequirementsResult, architect_result: ArchitectureDirectionResult
) -> FeatureBuildResult | None:
    """Run software_engineer against requirements/direction that a
    Requirements Challenge conversation (challenge_requirement()) has
    already produced and marked ready_for_development, and return the
    engineer's build result (branch name, files changed, PR URL), or
    None if the crew failed to produce a result. Raises ValueError if
    either result isn't actually marked ready - the caller (the
    Requirements Challenge admin page) is responsible for only offering
    this action once both agents have confirmed readiness, and this is
    a defense-in-depth check, not the primary gate. software_engineer
    can delegate functional questions to product_manager or technical
    questions to software_architect mid-build instead of guessing
    (allow_delegation) - this does NOT re-run either agent's own task,
    it only makes them available as live coworkers to ask. Only ever
    opens a pull request against main in this repo (crewai-starter) -
    never pushes directly to main and never merges anything itself; the
    existing "PR code review" workflow and required-review branch
    protection pick the PR up automatically from there, exactly like a
    human-authored PR."""
    if not (pm_result.ready_for_development and architect_result.ready_for_development):
        raise ValueError(
            "build_feature() called before both product_manager and "
            "software_architect marked ready_for_development=True."
        )
    inputs = {
        "pm_requirements": _format_requirements_for_engineer(pm_result),
        "architecture_direction": _format_architecture_for_engineer(architect_result),
    }
    try:
        # build_workspace() clones a fresh, throwaway copy of the repo
        # for this one build and deletes it afterward regardless of
        # outcome - software_engineer's file/git tools operate only
        # inside it, never the live app's own checkout (see
        # tools/build_workspace.py's module docstring).
        with build_workspace():
            result = feature_build_crew.kickoff(inputs=inputs)
    except Exception:
        # A final answer that mixes prose with a JSON block (e.g. the
        # engineer explaining a clarifying question instead of cleanly
        # returning FeatureBuildResult JSON) can make CrewAI's own
        # partial-JSON handling raise a bare pydantic ValidationError
        # instead of falling back gracefully (crewai/utilities/
        # converter.py's handle_partial_json re-raises on a
        # validation failure rather than trying the LLM-based
        # converter it falls back to for other failure modes) - that
        # exception must not escape this function uncaught, or the
        # caller never gets to show the user anything at all instead
        # of a clean "something went wrong" message. A failed
        # build_workspace() clone (e.g. GITHUB_PR_PUSH_TOKEN missing)
        # raises RuntimeError, which is also caught here for the same
        # reason.
        return None
    build_result = result.pydantic
    if build_result is None or not _PR_URL_PATTERN.match(build_result.pr_url):
        # A FeatureBuildResult with an empty, malformed, or fabricated
        # pr_url (e.g. the wrong org/repo, or a literal unfilled
        # "[PR_NUMBER]" placeholder - observed live on production) means
        # the engineer never got a real PR back from
        # create_feature_branch_and_open_pr, whether it wrote a plan
        # without calling the tool or the tool itself failed. Treating
        # that the same as a missing result (None) is what makes the
        # caller show "something went wrong" instead of a false "build
        # complete" message pointing at a PR that doesn't exist.
        return None
    return build_result


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


def challenge_requirement(
    conversation_text: str,
) -> tuple[FeatureRequirementsResult, ArchitectureDirectionResult] | None:
    """Runs the requirements-challenge crew over the conversation so far
    (the original feature idea plus any follow-up answers Marco has
    given to earlier open questions/clarifications, already merged into
    one text by the caller) and returns both the Product Manager's and
    the Software Architect's structured verdicts, or None if the crew
    didn't produce both. Each call re-runs both agents fresh against the
    full conversation rather than relying on in-process agent memory, so
    a session picked back up later challenges from the same accumulated
    context either way."""
    result = requirements_challenge_crew.kickoff(inputs={"feature_request": conversation_text})
    if len(result.tasks_output) < 2:
        return None
    pm_result = result.tasks_output[0].pydantic
    architect_result = result.tasks_output[1].pydantic
    if pm_result is None or architect_result is None:
        return None
    return pm_result, architect_result


if __name__ == "__main__":
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "Missing environment variable: ANTHROPIC_API_KEY. "
            "Add it to your .env file."
        )

    review = review_code(DEMO_DIFF)
    print("\n\n=== CODE REVIEW ===\n")
    print(review)
