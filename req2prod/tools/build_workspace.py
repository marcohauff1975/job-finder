"""
Isolated per-build working directory for software_engineer, so
create_feature_branch_and_open_pr and the file read/write tools never
touch the live app's own checkout.

Discovered the hard way (2026-07-10): these tools used to run in-place
in whatever directory the process's cwd happened to be, which in
production is the exact directory jobfinder.service serves live
traffic from. A single malformed FileWriterTool call left
streamlit_app.py containing just the word "test", and the git
checkout -B right before it had already left the live server sitting
on a half-built feature branch - a full production outage from one bad
tool call, with nothing left over to warn anyone until a real user hit
it.

Each build_feature() call now gets its own throwaway clone in a fresh
temp directory (build_workspace()), used for every git operation and
every file read/write, then deleted afterward regardless of outcome -
so a bad run can never leave stale broken state behind, and the live
app directory is never touched at all.

Threading the workspace path into the file tools: software_engineer's
Agent object (and its tools) are built once at import time in Req2Prod.py,
but the workspace only exists for the duration of one build_feature()
call - so the path can't be baked into the tools at construction time.
Same problem ai_viewer.py solves for carrying Streamlit's
ScriptRunContext across CrewAI's worker-thread hop: a ContextVar, set
right before crew.kickoff() and read by the tools when they actually
run.
"""

import os
import shutil
import stat
import subprocess
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from crewai.tools import BaseTool

_GITHUB_REPO = "marcohauff1975/job-finder"

_workspace_dir: ContextVar[Path | None] = ContextVar("build_workspace_dir", default=None)


def run_in_workspace(
    args: list[str], timeout: int = 60, env: dict | None = None
) -> tuple[int, str, str]:
    """Runs a subprocess with cwd pinned to the current build's
    workspace - the one place in this module that actually enforces
    the isolation, since every git operation goes through here."""
    result = subprocess.run(
        args, cwd=workspace_dir(), capture_output=True, text=True, timeout=timeout, env=env
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


@contextmanager
def git_askpass_env(push_token: str):
    """A GIT_ASKPASS script that echoes the token from an environment
    variable, keeping it out of argv entirely - unlike a token embedded
    in a URL passed as a subprocess argument, which stays visible to
    anything that can list process arguments (ps aux, /proc/<pid>/
    cmdline) for as long as that git process runs."""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write('#!/bin/sh\necho "$GITHUB_PR_PUSH_TOKEN"\n')
        askpass_path = f.name
    os.chmod(askpass_path, stat.S_IRWXU)
    try:
        yield {
            **os.environ,
            "GITHUB_PR_PUSH_TOKEN": push_token,
            "GIT_ASKPASS": askpass_path,
            "GIT_TERMINAL_PROMPT": "0",
        }
    finally:
        os.unlink(askpass_path)


@contextmanager
def build_workspace():
    """Clones this repo into a fresh temp directory, makes it the
    current build's workspace (readable via workspace_dir() from
    anywhere, including CrewAI's worker threads), and deletes it
    afterward no matter what happens during the build. Raises
    RuntimeError if GITHUB_PR_PUSH_TOKEN isn't set (the same token used
    to push/open the PR - see feature_build_ops.py - also has the
    "Contents: read" access needed to clone this private repo) or if
    the clone itself fails."""
    push_token = os.getenv("GITHUB_PR_PUSH_TOKEN")
    if not push_token:
        raise RuntimeError("GITHUB_PR_PUSH_TOKEN is not set - can't clone a build workspace.")

    tmpdir = Path(tempfile.mkdtemp(prefix="crewai-build-"))
    reset_token = None
    try:
        clone_url = f"https://x-access-token@github.com/{_GITHUB_REPO}.git"
        with git_askpass_env(push_token) as env:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, str(tmpdir)],
                capture_output=True,
                text=True,
                timeout=120,
                env=env,
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"git clone into build workspace failed: {result.stderr.strip() or result.stdout.strip()}"
            )
        reset_token = _workspace_dir.set(tmpdir)
        yield tmpdir
    finally:
        if reset_token is not None:
            _workspace_dir.reset(reset_token)
        shutil.rmtree(tmpdir, ignore_errors=True)


def workspace_dir() -> Path:
    """The current build's workspace directory - raises RuntimeError if
    called outside a `with build_workspace():` block, so a coding
    mistake fails loudly instead of silently falling back to some
    other directory (like the live app's)."""
    path = _workspace_dir.get()
    if path is None:
        raise RuntimeError(
            "No build workspace is active - this must only be used inside "
            "build_feature()'s `with build_workspace():` block."
        )
    return path


def _safe_join(root: Path, relative_path: str) -> Path:
    """Resolves relative_path against root, refusing to let it escape
    (via '..', an absolute path, or a symlink) regardless of what the
    agent passes - this is what makes the workspace boundary real
    rather than just a convention the agent is supposed to follow."""
    candidate = (root / relative_path).resolve()
    real_root = root.resolve()
    if not candidate.is_relative_to(real_root):
        raise ValueError(f"'{relative_path}' resolves outside the build workspace.")
    return candidate


class WorkspaceFileReadTool(BaseTool):
    name: str = "Read a file's content"
    description: str = (
        "Reads a file's content, given a path relative to the repository "
        "root (e.g. 'streamlit_app.py'). Optionally pass start_line and "
        "line_count to read only part of a large file. Can only read "
        "files inside the current build's isolated workspace, never the "
        "live app."
    )

    def _run(
        self, file_path: str, start_line: int | None = None, line_count: int | None = None
    ) -> str:
        try:
            target = _safe_join(workspace_dir(), file_path)
        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"
        if not target.exists() or not target.is_file():
            return f"Error: File not found at path: {file_path}"
        lines = target.read_text().splitlines(keepends=True)
        if start_line is not None:
            start = max(start_line - 1, 0)
            end = start + line_count if line_count else len(lines)
            lines = lines[start:end]
        return "".join(lines)


class WorkspaceFileWriterTool(BaseTool):
    name: str = "File Writer Tool"
    description: str = (
        "Writes content to a file, given a path relative to the repository "
        "root (e.g. 'streamlit_app.py'). Pass overwrite=true to replace an "
        "existing file. Can only write inside the current build's isolated "
        "workspace, never the live app."
    )

    def _run(self, filename: str, content: str, overwrite: bool = False) -> str:
        try:
            target = _safe_join(workspace_dir(), filename)
        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"
        if target.exists() and not overwrite:
            return f"Error: {filename} already exists and overwrite was not set to true."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
        return f"Content successfully written to {filename}"


class WorkspaceEditTool(BaseTool):
    name: str = "Edit a file (find and replace)"
    description: str = (
        "Replaces one exact occurrence of old_string with new_string in an "
        "existing file, given a path relative to the repository root (e.g. "
        "'streamlit_app.py'). Use this for any small or medium change "
        "instead of rewriting the whole file with the file-writing tool - "
        "asking a model to reproduce an entire large file from memory just "
        "to change one line is exactly what caused a real production "
        "outage (2026-07-10) when that write came out malformed. Several "
        "small edits, each changing only what actually needs to change, "
        "are far more reliable than one giant rewrite - use the "
        "file-writing tool only for a brand-new file or a change so "
        "extensive that a full rewrite is genuinely simpler. old_string "
        "must match the file's existing text exactly (including "
        "whitespace/indentation - read the file first, don't guess) and "
        "must appear exactly once in the file, or the edit is refused; "
        "include more surrounding lines in old_string to make it unique if "
        "needed. Can only edit files inside the current build's isolated "
        "workspace, never the live app."
    )

    def _run(self, filename: str, old_string: str, new_string: str) -> str:
        try:
            target = _safe_join(workspace_dir(), filename)
        except (ValueError, RuntimeError) as e:
            return f"Error: {e}"
        if not target.exists() or not target.is_file():
            return f"Error: File not found at path: {filename}"
        if old_string == new_string:
            return "Error: old_string and new_string are identical - nothing to change."
        content = target.read_text()
        count = content.count(old_string)
        if count == 0:
            return (
                f"Error: old_string was not found in {filename} - it must match "
                "the file's existing text exactly, including whitespace. Read "
                "the file first rather than guessing."
            )
        if count > 1:
            return (
                f"Error: old_string appears {count} times in {filename} - it "
                "must be unique. Include more surrounding lines for context."
            )
        target.write_text(content.replace(old_string, new_string, 1))
        return f"Successfully replaced 1 occurrence in {filename}"
