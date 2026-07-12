"""
Unit tests for sdlc/tool_cli.py - the CLI shim that lets AGENT_BACKEND=
subscription's `claude -p` calls reach the same BaseTool implementations
API mode uses. No test here makes a real network call, touches a real
file outside tmp_path, or spawns a real `claude` process - tool_cli.py's
own registered tools are exercised directly (or with their underlying
requests.get/subprocess.run mocked), the same isolation approach as
test_backend.py.
"""

import json

from sdlc import tool_cli


class TestMainArgParsing:
    def test_missing_args_errors(self, capsys):
        assert tool_cli.main([]) == 1
        assert "usage:" in capsys.readouterr().err

    def test_unknown_tool_errors(self, capsys):
        exit_code = tool_cli.main(["not_a_real_tool", "{}"])
        assert exit_code == 1
        assert "unknown tool" in capsys.readouterr().err

    def test_invalid_json_kwargs_errors(self, capsys):
        exit_code = tool_cli.main(["check_pypi_package_version", "{not json"])
        assert exit_code == 1
        assert "must be valid JSON" in capsys.readouterr().err

    def test_workspace_scoped_tool_without_workspace_dir_errors(self, capsys):
        exit_code = tool_cli.main(["File Writer Tool", '{"filename": "x", "content": "y"}'])
        assert exit_code == 1
        assert "requires --workspace-dir" in capsys.readouterr().err

    def test_workspace_dir_flag_is_stripped_regardless_of_position(self, monkeypatch, tmp_path):
        """--workspace-dir can appear before or interleaved with the
        positional args - main() must find and remove it either way,
        leaving exactly tool_name + json kwargs."""
        captured = {}

        def fake_run(**kwargs):
            captured["kwargs"] = kwargs
            return "ok"

        monkeypatch.setattr(tool_cli.TOOLS_BY_NAME["check_pypi_package_version"], "_run", fake_run)

        exit_code = tool_cli.main(
            ["--workspace-dir", str(tmp_path), "check_pypi_package_version", '{"package": "x", "version": "1"}']
        )

        assert exit_code == 0
        assert captured["kwargs"] == {"package": "x", "version": "1"}


class TestToolExecution:
    def test_successful_call_prints_result_and_exits_0(self, monkeypatch, capsys):
        tool = tool_cli.TOOLS_BY_NAME["check_pypi_package_version"]
        monkeypatch.setattr(tool, "_run", lambda **kw: "streamlit==1.58.0 is real.")

        exit_code = tool_cli.main(["check_pypi_package_version", '{"package": "streamlit", "version": "1.58.0"}'])

        assert exit_code == 0
        assert capsys.readouterr().out.strip() == "streamlit==1.58.0 is real."

    def test_tool_exception_is_caught_and_reported(self, monkeypatch, capsys):
        tool = tool_cli.TOOLS_BY_NAME["check_anthropic_model_id"]

        def raise_exc(**kw):
            raise RuntimeError("network unreachable")

        monkeypatch.setattr(tool, "_run", raise_exc)

        exit_code = tool_cli.main(["check_anthropic_model_id", '{"model_id": "claude-sonnet-5"}'])

        assert exit_code == 1
        assert "RuntimeError: network unreachable" in capsys.readouterr().err

    def test_workspace_scoped_tool_actually_writes_inside_given_workspace(self, tmp_path):
        """End-to-end against the real WorkspaceFileWriterTool (no mock) -
        confirms --workspace-dir genuinely sets the ContextVar the tool
        reads, the same one build_workspace() sets in-process."""
        exit_code = tool_cli.main(
            [
                "--workspace-dir",
                str(tmp_path),
                "File Writer Tool",
                json.dumps({"filename": "new_file.py", "content": "x = 1\n", "overwrite": True}),
            ]
        )

        assert exit_code == 0
        assert (tmp_path / "new_file.py").read_text() == "x = 1\n"

    def test_workspace_scoped_tool_cannot_escape_the_given_workspace(self, tmp_path, capsys):
        """Regression guard for the exact bug class build_workspace.py's
        _safe_join exists to prevent (see its docstring) - even reached
        through this CLI shim, a path-escape attempt must still fail."""
        exit_code = tool_cli.main(
            [
                "--workspace-dir",
                str(tmp_path),
                "File Writer Tool",
                json.dumps({"filename": "../escaped.py", "content": "evil", "overwrite": True}),
            ]
        )

        assert exit_code == 0  # the tool itself returns an "Error: ..." string, not a raised exception
        assert "Error" in capsys.readouterr().out
        assert not (tmp_path.parent / "escaped.py").exists()


class TestToolRegistry:
    def test_all_registered_tools_have_unique_names(self):
        # A dict comprehension building TOOLS_BY_NAME would silently drop
        # a duplicate name - assert the source list's length matches
        # instead of just trusting the dict came out right.
        assert len(tool_cli.TOOLS_BY_NAME) == len(
            {
                "check_pypi_package_version",
                "check_anthropic_model_id",
                "check_production_health",
                "rollback_production",
                "inspect_job_finder_page",
                "fetch_failed_run_logs",
                "commit_and_push_fix",
                "retrigger_deploy_workflow",
                "create_feature_branch_and_open_pr",
                "Read a file's content",
                "File Writer Tool",
                "Edit a file (find and replace)",
                "git_repo_status",
                "git_file_history",
                "read_repo_file",
                "aws_live_setup_check",
                "github_live_repo_check",
            }
        )

    def test_workspace_scoped_names_are_all_actually_registered(self):
        assert tool_cli._WORKSPACE_SCOPED_TOOL_NAMES.issubset(tool_cli.TOOLS_BY_NAME)
