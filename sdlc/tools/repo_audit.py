"""
Read-only tools that let the Technology Excellence panel (see
technology_excellence_* agents in sdlc/SDLC.py) actually inspect a
repo's real git state - tracked files, commit history, and whether a
given path was ever committed - instead of only reasoning over a
directory listing. Every command here is a fixed, read-only git
subcommand; none of these tools can stage, commit, push, or modify
anything.

Deliberately does NOT use crewai_tools' DirectoryReadTool/FileReadTool:
DirectoryReadTool does an unfiltered os.walk with no .gitignore
awareness, so pointed at a repo with a committed venv/ it can return
tens of thousands of file paths in one tool result - large enough to
blow past the model's context window in a couple of retries. Both
tools are also sandboxed to os.getcwd(), which silently breaks reading
a *second* repo (e.g. the sibling crewai-infra) that isn't the process
cwd. GitRepoStatusTool's tracked_files (git ls-files) is the bounded,
.gitignore-aware substitute for listing, and RepoFileReadTool below is
the not-cwd-sandboxed substitute for reading - it's still bounded (a
line cap) and still refuses paths outside the Job Finder workspace, so
it isn't a blanket "read anything on disk" tool.
"""

import os
import subprocess

from crewai.tools import BaseTool
from pydantic import Field


def _run(args: list[str], cwd: str, timeout: int = 30) -> tuple[int, str, str]:
    result = subprocess.run(args, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


class GitRepoStatusTool(BaseTool):
    name: str = "git_repo_status"
    description: str = (
        "Returns the real, current git state of the repo at repo_path: "
        "every tracked file (`git ls-files`), any uncommitted changes "
        "(`git status --short`), and the last 30 commits (`git log "
        "--oneline -30`). Read-only - never modifies anything. Call this "
        "before judging repo structure, commit hygiene, or whether a "
        "file is actually tracked - don't assume either from a plain "
        "directory listing."
    )

    def _run(self, repo_path: str) -> str:
        sections = {}
        for label, args in [
            ("tracked_files", ["git", "ls-files"]),
            ("uncommitted_changes", ["git", "status", "--short"]),
            ("recent_commits", ["git", "log", "--oneline", "-30"]),
        ]:
            code, out, err = _run(args, cwd=repo_path)
            sections[label] = out if code == 0 else f"error: {err or out}"
        return "\n\n".join(f"=== {k} ===\n{v}" for k, v in sections.items())


class GitFileHistoryTool(BaseTool):
    name: str = "git_file_history"
    description: str = (
        "Checks whether a specific path was EVER committed to the repo "
        "at repo_path, even if it's absent, deleted, or gitignored now - "
        "via `git log --all --full-history -- <file_path>`. Always run "
        "this on anything that looks like a secret, private key, "
        "credential, or state file (.env, *.pem, *.key, *.tfstate, "
        "*.db, credentials.json) - a file gitignored today may still be "
        "sitting in history from before the .gitignore rule existed, "
        "which needs a history rewrite, not just deletion. An empty "
        "result means the path was never tracked."
    )

    def _run(self, repo_path: str, file_path: str) -> str:
        code, out, err = _run(
            ["git", "log", "--all", "--full-history", "--oneline", "--", file_path],
            cwd=repo_path,
        )
        if code != 0:
            return f"error: {err or out}"
        return out or f"'{file_path}' was never committed to this repo's history."


MAX_READ_LINES = 4000


class RepoFileReadTool(BaseTool):
    name: str = "read_repo_file"
    description: str = (
        "Reads a file's real content by its full path (build it from a "
        "repo path plus a name out of git_repo_status's tracked_files). "
        "Not restricted to the current working directory like a generic "
        "file-reading tool would be, so it works across every repo in "
        "this review - but it refuses anything outside the Job Finder "
        "workspace it was configured for. Output is capped at "
        f"{MAX_READ_LINES} lines; pass start_line (and optionally "
        "line_count) to page through anything longer rather than "
        "assuming a truncated read is the whole file."
    )
    allowed_roots: list[str] = Field(default_factory=list)

    def _run(
        self, file_path: str, start_line: int = 1, line_count: int | None = None
    ) -> str:
        resolved = os.path.realpath(file_path)
        if not any(
            resolved == root or resolved.startswith(root + os.sep)
            for root in self.allowed_roots
        ):
            return f"Refused: '{file_path}' resolves outside the repos allowed for this review."

        try:
            with open(resolved, "r", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return f"Error: file not found at {file_path}"
        except IsADirectoryError:
            return f"Error: '{file_path}' is a directory, not a file"
        except OSError as e:
            return f"Error reading {file_path}: {e}"

        start_idx = max((start_line or 1) - 1, 0)
        end_idx = start_idx + line_count if line_count else len(lines)
        selected = lines[start_idx:end_idx]

        truncated = len(selected) > MAX_READ_LINES
        body = "".join(selected[:MAX_READ_LINES])
        if truncated:
            body += (
                f"\n... [truncated at {MAX_READ_LINES} lines - pass "
                f"start_line={start_idx + MAX_READ_LINES + 1} to continue]"
            )
        return body
