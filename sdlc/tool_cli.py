"""
Lets AGENT_BACKEND=subscription's `claude -p` calls invoke the exact same
CrewAI BaseTool implementations the API path already uses, instead of
asking the model to re-derive equivalent behavior as raw shell commands
from a text description - correctness parity for tools too stateful or
complex to safely reinvent (SSH to production, headless-browser
automation, git/PR operations), not just a prompt-writing exercise.

Usage: python -m sdlc.tool_cli [--workspace-dir PATH] <tool_name> '<json kwargs>'
Prints the tool's own string result to stdout and exits 0, or prints an
error to stderr and exits 1. <tool_name> must be one of TOOLS_BY_NAME
below - there is no way to reach an arbitrary import path from here, so
what an agent can actually do in subscription mode is exactly the
allowlist its own --allowedTools grants it (see backend.py's per-agent
scoping), never broader than what it already has in API mode.

The workspace-scoped tools (software_engineer's file tools, plus
create_feature_branch_and_open_pr) resolve paths against
sdlc.tools.build_workspace's workspace_dir() ContextVar, which only
exists inside a live `with build_workspace():` block. That block wraps
one crewai kickoff() call in API mode; here it instead wraps one
subprocess invocation of this script, so --workspace-dir sets the same
ContextVar directly using the path build_feature() already resolved and
passed down - the tool code underneath doesn't need to know the
difference between the two.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from sdlc.tools.aws_audit import AWSLiveSetupTool
from sdlc.tools.build_workspace import (
    WorkspaceEditTool,
    WorkspaceFileReadTool,
    WorkspaceFileWriterTool,
    _workspace_dir,
)
from sdlc.tools.dependency_check import AnthropicModelCheckTool, PackageVersionCheckTool
from sdlc.tools.devops_ops import CommitAndPushFixTool, FetchFailedRunLogsTool, RetriggerWorkflowTool
from sdlc.tools.feature_build_ops import CreateFeatureBranchAndOpenPRTool
from sdlc.tools.github_audit import GitHubLiveRepoCheckTool
from sdlc.tools.prod_ops import ProdHealthCheckTool, ProdRollbackTool
from sdlc.tools.repo_audit import GitFileHistoryTool, GitRepoStatusTool, RepoFileReadTool
from sdlc.tools.ux_inspector import UXPageInspectorTool

REPO_ROOT = Path(__file__).resolve().parent.parent

_WORKSPACE_SCOPED_TOOL_NAMES = {
    "Read a file's content",
    "File Writer Tool",
    "Edit a file (find and replace)",
    "create_feature_branch_and_open_pr",
}

TOOLS_BY_NAME = {
    tool.name: tool
    for tool in [
        PackageVersionCheckTool(),
        AnthropicModelCheckTool(),
        ProdHealthCheckTool(),
        ProdRollbackTool(),
        UXPageInspectorTool(),
        FetchFailedRunLogsTool(),
        CommitAndPushFixTool(),
        RetriggerWorkflowTool(),
        CreateFeatureBranchAndOpenPRTool(),
        WorkspaceFileReadTool(),
        WorkspaceFileWriterTool(),
        WorkspaceEditTool(),
        GitRepoStatusTool(),
        GitFileHistoryTool(),
        # Same allowed_roots as SDLC.py's _readiness_tools - the Technology
        # Excellence panel reads across both Job Finder repos (this one and
        # its sibling crewai-infra), not just this checkout.
        RepoFileReadTool(allowed_roots=[str(REPO_ROOT.parent)]),
        AWSLiveSetupTool(),
        GitHubLiveRepoCheckTool(),
    ]
}


def main(argv: list[str]) -> int:
    args = list(argv)
    workspace_dir_arg = None
    if "--workspace-dir" in args:
        idx = args.index("--workspace-dir")
        workspace_dir_arg = args[idx + 1]
        del args[idx : idx + 2]

    if len(args) != 2:
        print(
            "usage: python -m sdlc.tool_cli [--workspace-dir PATH] <tool_name> '<json kwargs>'",
            file=sys.stderr,
        )
        return 1
    tool_name, raw_kwargs = args

    tool = TOOLS_BY_NAME.get(tool_name)
    if tool is None:
        print(f"unknown tool: {tool_name!r} - available: {sorted(TOOLS_BY_NAME)}", file=sys.stderr)
        return 1

    if tool_name in _WORKSPACE_SCOPED_TOOL_NAMES:
        if not workspace_dir_arg:
            print(f"{tool_name!r} requires --workspace-dir", file=sys.stderr)
            return 1
        _workspace_dir.set(Path(workspace_dir_arg))

    try:
        kwargs = json.loads(raw_kwargs)
    except json.JSONDecodeError as e:
        print(f"kwargs must be valid JSON: {e}", file=sys.stderr)
        return 1

    try:
        result = tool._run(**kwargs)
    except Exception as e:
        print(f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
