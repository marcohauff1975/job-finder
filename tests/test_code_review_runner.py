"""
Unit tests for req2prod/code_review_runner.py's orchestration logic - the
review/fix loop and the pr_arbiter escalation - mocked all the way
down to review_code/fix_review_findings/arbiter_review and every git/gh
subprocess call, since a real run costs real API credits and touches a
real PR. These tests exist specifically because this script decides
whether code merges into a production-deployed app with nobody else
reviewing it - the control flow is worth verifying directly, not just
by reading it.
"""

from types import SimpleNamespace

import pytest

from req2prod import code_review_runner as runner
from req2prod.Req2Prod import ArbiterVerdict, CodeReviewFinding, CodeReviewResult, PRFixResult

FAKE_FINDING = CodeReviewFinding(file="app.py", line="10", risk="uses eval() on user input")


def _ok(stdout: str = "") -> SimpleNamespace:
    return SimpleNamespace(returncode=0, stdout=stdout, stderr="")


@pytest.fixture(autouse=True)
def env(monkeypatch, tmp_path):
    """Required env vars + a real (throwaway) diff file every test needs,
    applied automatically so each test only overrides what it cares
    about."""
    diff_file = tmp_path / "pr.diff"
    diff_file.write_text("diff --git a/app.py b/app.py\n+eval(x)\n")
    monkeypatch.setenv("DIFF_FILE", str(diff_file))
    monkeypatch.setenv("PR_NUMBER", "42")
    monkeypatch.setenv("REPO", "marcohauff1975/job-finder")
    monkeypatch.setenv("REVIEWER_BOT_TOKEN", "fake-token")
    return diff_file


@pytest.fixture
def fake_subprocess(monkeypatch):
    """Records every subprocess.run call this script makes and returns a
    canned success result for each - real git/gh commands never run."""
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["git", "rev-parse"]:
            return _ok("fix-branch")
        if args[:2] == ["git", "diff"]:
            return _ok("diff --git a/app.py b/app.py\n+safe(x)\n")
        return _ok()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    return calls


class TestCleanApprovalNoFixNeeded:
    def test_approves_and_merges_without_running_fix_agent(self, fake_subprocess, monkeypatch):
        monkeypatch.setattr(runner, "review_code", lambda diff: CodeReviewResult(passed=True, findings=[]))
        monkeypatch.setattr(
            runner, "fix_review_findings", lambda findings: pytest.fail("should not be called")
        )
        monkeypatch.setattr(
            runner, "arbiter_review", lambda diff, findings: pytest.fail("should not be called")
        )

        exit_code = runner.main()

        assert exit_code == 0
        review_calls = [c for c in fake_subprocess if c[:3] == ["gh", "pr", "review"]]
        assert any("--approve" in c for c in review_calls)
        assert sum(1 for c in fake_subprocess if c[:3] == ["gh", "pr", "merge"]) == 1
        assert not any(c[:2] == ["git", "add"] for c in fake_subprocess)


class TestFixLoopConverges:
    def test_fixes_findings_then_approves_on_second_review(self, fake_subprocess, monkeypatch):
        calls = {"review": 0}

        def fake_review(diff):
            calls["review"] += 1
            if calls["review"] == 1:
                return CodeReviewResult(passed=False, findings=[FAKE_FINDING])
            return CodeReviewResult(passed=True, findings=[])

        monkeypatch.setattr(runner, "review_code", fake_review)
        monkeypatch.setattr(
            runner,
            "fix_review_findings",
            lambda findings: PRFixResult(files_changed=["app.py"], fix_summary="removed eval()"),
        )
        monkeypatch.setattr(
            runner, "arbiter_review", lambda diff, findings: pytest.fail("should not be called")
        )

        exit_code = runner.main()

        assert exit_code == 0
        assert calls["review"] == 2
        assert any(c[:2] == ["git", "add"] and "app.py" in c for c in fake_subprocess)
        assert sum(1 for c in fake_subprocess if c[:2] == ["git", "commit"]) == 1
        assert sum(1 for c in fake_subprocess if c[:2] == ["git", "push"]) == 1
        assert sum(1 for c in fake_subprocess if c[:3] == ["gh", "pr", "merge"]) == 1


class TestFixAgentGivesUpEarly:
    def test_escalates_to_arbiter_immediately_if_fixer_makes_no_changes(
        self, fake_subprocess, monkeypatch
    ):
        monkeypatch.setattr(
            runner, "review_code", lambda diff: CodeReviewResult(passed=False, findings=[FAKE_FINDING])
        )
        monkeypatch.setattr(
            runner,
            "fix_review_findings",
            lambda findings: PRFixResult(files_changed=[], fix_summary="finding didn't reproduce"),
        )
        arbiter_calls = []

        def fake_arbiter(diff, findings):
            arbiter_calls.append((diff, findings))
            return ArbiterVerdict(safe_to_merge=True, reasoning="cosmetic only", blocking_reasons=[])

        monkeypatch.setattr(runner, "arbiter_review", fake_arbiter)

        exit_code = runner.main()

        assert exit_code == 0
        assert len(arbiter_calls) == 1


class TestArbiterApprovesAfterCap:
    def test_merges_when_arbiter_says_safe(self, fake_subprocess, monkeypatch):
        monkeypatch.setattr(
            runner, "review_code", lambda diff: CodeReviewResult(passed=False, findings=[FAKE_FINDING])
        )
        monkeypatch.setattr(
            runner,
            "fix_review_findings",
            lambda findings: PRFixResult(files_changed=["app.py"], fix_summary="attempted a fix"),
        )
        monkeypatch.setattr(
            runner,
            "arbiter_review",
            lambda diff, findings: ArbiterVerdict(
                safe_to_merge=True, reasoning="remaining issue is cosmetic", blocking_reasons=[]
            ),
        )

        exit_code = runner.main()

        assert exit_code == 0
        assert sum(1 for c in fake_subprocess if c[:3] == ["gh", "pr", "merge"]) == 1

    def test_uses_max_fix_attempts_review_rounds(self, fake_subprocess, monkeypatch):
        review_count = {"n": 0}

        def fake_review(diff):
            review_count["n"] += 1
            return CodeReviewResult(passed=False, findings=[FAKE_FINDING])

        monkeypatch.setattr(runner, "review_code", fake_review)
        monkeypatch.setattr(
            runner,
            "fix_review_findings",
            lambda findings: PRFixResult(files_changed=["app.py"], fix_summary="attempted a fix"),
        )
        monkeypatch.setattr(
            runner,
            "arbiter_review",
            lambda diff, findings: ArbiterVerdict(safe_to_merge=True, reasoning="ok", blocking_reasons=[]),
        )

        runner.main()

        # MAX_FIX_ATTEMPTS + 1 review rounds total (initial + one per fix attempt).
        assert review_count["n"] == runner.MAX_FIX_ATTEMPTS + 1


class TestArbiterBlocksAndNotifies:
    def test_leaves_pr_open_and_notifies_when_arbiter_says_unsafe(self, fake_subprocess, monkeypatch):
        monkeypatch.setattr(
            runner, "review_code", lambda diff: CodeReviewResult(passed=False, findings=[FAKE_FINDING])
        )
        monkeypatch.setattr(
            runner,
            "fix_review_findings",
            lambda findings: PRFixResult(files_changed=[], fix_summary="couldn't safely fix eval()"),
        )
        monkeypatch.setattr(
            runner,
            "arbiter_review",
            lambda diff, findings: ArbiterVerdict(
                safe_to_merge=False,
                reasoning="Still calls eval() on user input - a real security risk.",
                blocking_reasons=["Unsanitized eval() on user-controlled input in app.py"],
            ),
        )
        notify_calls = []
        monkeypatch.setattr(
            runner,
            "send_pr_unresolvable_notification",
            lambda pr_number, repo, reasoning, blocking_reasons: notify_calls.append(
                (pr_number, repo, reasoning, blocking_reasons)
            ),
        )

        exit_code = runner.main()

        assert exit_code == 0  # working as intended, not a script failure
        assert not any(c[:3] == ["gh", "pr", "merge"] for c in fake_subprocess)
        review_calls = [c for c in fake_subprocess if c[:3] == ["gh", "pr", "review"]]
        assert any("--request-changes" in c for c in review_calls)
        assert len(notify_calls) == 1
        assert notify_calls[0][0] == "42"


class TestEmptyDiff:
    def test_returns_early_without_calling_anything(self, env, monkeypatch):
        env.write_text("   \n")
        monkeypatch.setattr(runner, "review_code", lambda diff: pytest.fail("should not be called"))

        exit_code = runner.main()

        assert exit_code == 0


class TestCrewFailures:
    def test_escalates_to_arbiter_when_code_reviewer_never_produces_a_result(
        self, fake_subprocess, monkeypatch
    ):
        """The bug that let PR #21's own code_review check fail outright: a
        code_reviewer crew that never produces a result (repeated empty LLM
        responses, not a verdict on the diff) must still reach pr_arbiter,
        the same way an unconverged findings loop does - not just fail."""
        monkeypatch.setattr(runner, "review_code", lambda diff: None)
        monkeypatch.setattr(
            runner, "fix_review_findings", lambda findings: pytest.fail("should not be called")
        )
        arbiter_calls = []

        def fake_arbiter(diff, findings):
            arbiter_calls.append(findings)
            return ArbiterVerdict(safe_to_merge=True, reasoning="diff looks safe on its own merits")

        monkeypatch.setattr(runner, "arbiter_review", fake_arbiter)

        exit_code = runner.main()

        assert exit_code == 0
        assert len(arbiter_calls) == 1
        assert len(arbiter_calls[0]) == 1
        assert "empty response" in arbiter_calls[0][0].risk
        review_calls = [c for c in fake_subprocess if c[:3] == ["gh", "pr", "review"]]
        assert any("--approve" in c for c in review_calls)

    def test_returns_1_when_both_code_reviewer_and_arbiter_produce_no_result(
        self, fake_subprocess, monkeypatch
    ):
        monkeypatch.setattr(runner, "review_code", lambda diff: None)
        monkeypatch.setattr(runner, "arbiter_review", lambda diff, findings: None)

        exit_code = runner.main()

        assert exit_code == 1

    def test_returns_1_when_arbiter_produces_no_result(self, fake_subprocess, monkeypatch):
        monkeypatch.setattr(
            runner, "review_code", lambda diff: CodeReviewResult(passed=False, findings=[FAKE_FINDING])
        )
        monkeypatch.setattr(
            runner, "fix_review_findings", lambda findings: PRFixResult(files_changed=[], fix_summary="no fix")
        )
        monkeypatch.setattr(runner, "arbiter_review", lambda diff, findings: None)

        exit_code = runner.main()

        assert exit_code == 1


class TestReviewCodeTransientRetry:
    """A None from review_code() means an empty LLM response or a caught
    crew exception (see req2prod/Req2Prod.py's review_code) - a known intermittent
    flake unrelated to the diff itself, so it must not crash the whole run
    on the very first hiccup the way this exact bug did against PR #21."""

    def test_recovers_after_transient_failures_within_the_retry_budget(
        self, fake_subprocess, monkeypatch
    ):
        calls = {"review": 0}

        def flaky_review(diff):
            calls["review"] += 1
            if calls["review"] <= runner.MAX_TRANSIENT_RETRIES:
                return None
            return CodeReviewResult(passed=True, findings=[])

        monkeypatch.setattr(runner, "review_code", flaky_review)
        monkeypatch.setattr(
            runner, "fix_review_findings", lambda findings: pytest.fail("should not be called")
        )
        monkeypatch.setattr(
            runner, "arbiter_review", lambda diff, findings: pytest.fail("should not be called")
        )

        exit_code = runner.main()

        assert exit_code == 0
        assert calls["review"] == runner.MAX_TRANSIENT_RETRIES + 1

    def test_escalates_to_arbiter_once_the_retry_budget_is_exhausted(self, fake_subprocess, monkeypatch):
        calls = {"review": 0}

        def always_none(diff):
            calls["review"] += 1
            return None

        monkeypatch.setattr(runner, "review_code", always_none)
        monkeypatch.setattr(
            runner, "arbiter_review", lambda diff, findings: ArbiterVerdict(safe_to_merge=True, reasoning="ok")
        )

        exit_code = runner.main()

        assert exit_code == 0
        assert calls["review"] == runner.MAX_TRANSIENT_RETRIES + 1


def _workflow_diff(*paths):
    """A diff in the shape `gh pr diff` actually emits."""
    out = []
    for p in paths:
        out += [f"diff --git a/{p} b/{p}", f"--- a/{p}", f"+++ b/{p}", "@@ -1 +1 @@", "-old", "+new"]
    return "\n".join(out)


class TestDetectingAWorkflowInTheDiff:
    def test_a_workflow_file_is_detected(self):
        assert runner._touches_a_workflow(_workflow_diff(".github/workflows/pipeline.yml")) is True

    def test_an_ordinary_diff_is_not(self):
        assert runner._touches_a_workflow(_workflow_diff("job_search.py", "auth.py")) is False

    def test_one_workflow_among_many_files_still_counts(self):
        """Git pushes all or nothing - one workflow file poisons the whole
        push, which is exactly what happened on #91."""
        diff = _workflow_diff("req2prod/deploy_targets.py", ".github/workflows/p.yml", "auth.py")

        assert runner._touches_a_workflow(diff) is True

    def test_other_things_under_dot_github_are_fine(self):
        """Only workflows are locked - an issue template pushes like any other
        file, so those must keep the fix loop."""
        assert runner._touches_a_workflow(_workflow_diff(".github/CODEOWNERS")) is False

    def test_a_path_merely_mentioning_workflows_is_not_one(self):
        assert runner._touches_a_workflow(_workflow_diff("docs/workflows.md")) is False

    def test_added_content_naming_the_path_is_not_a_header(self):
        """Only +++ b/ lines name files. A naive substring search fires on
        this - the line is code, not a header."""
        diff = (
            "--- a/req2prod/deploy_targets.py\n"
            "+++ b/req2prod/deploy_targets.py\n"
            '+_WORKFLOW_DIR = ".github/workflows/"\n'
        )

        assert runner._touches_a_workflow(diff) is False


class TestWorkflowDiffsSkipTheFixLoop:
    """GitHub rejects PAT pushes to workflow files even with full repo write.
    On #91 pr_fix_agent fixed two real findings, the re-review passed, and the
    push was rejected - losing every fix. Review still runs; only the fixing
    stops, and pr_arbiter still gets the last word."""

    def test_pr_fix_agent_never_runs_for_a_workflow_diff(
        self, fake_subprocess, monkeypatch, env
    ):
        env.write_text(_workflow_diff(".github/workflows/req2prod-pipeline.yml"))
        fix_calls = []
        monkeypatch.setattr(
            runner,
            "review_code",
            lambda diff: CodeReviewResult(
                passed=False,
                findings=[CodeReviewFinding(file=".github/workflows/p.yml", risk="bad")],
            ),
        )
        monkeypatch.setattr(
            runner, "fix_review_findings", lambda f: fix_calls.append(f) or PRFixResult(fix_summary="x")
        )
        monkeypatch.setattr(
            runner,
            "arbiter_review",
            lambda diff, findings: ArbiterVerdict(safe_to_merge=True, reasoning="fine"),
        )

        assert runner.main() == 0
        assert fix_calls == [], "a fix here could not have been pushed"

    def test_the_findings_still_reach_pr_arbiter(self, fake_subprocess, monkeypatch, env):
        """Skipping the fix loop must not mean skipping the review."""
        env.write_text(_workflow_diff(".github/workflows/req2prod-pipeline.yml"))
        seen = {}
        monkeypatch.setattr(
            runner,
            "review_code",
            lambda diff: CodeReviewResult(
                passed=False,
                findings=[CodeReviewFinding(file=".github/workflows/p.yml", risk="the real finding")],
            ),
        )
        monkeypatch.setattr(runner, "fix_review_findings", lambda f: None)

        def fake_arbiter(diff, findings):
            seen["findings"] = findings
            return ArbiterVerdict(safe_to_merge=False, reasoning="no", blocking_reasons=["r"])

        monkeypatch.setattr(runner, "arbiter_review", fake_arbiter)
        monkeypatch.setattr(runner, "send_pr_unresolvable_notification", lambda *a: None)

        runner.main()

        assert [f.risk for f in seen["findings"]] == ["the real finding"]

    def test_an_ordinary_diff_still_gets_fixed(self, fake_subprocess, monkeypatch):
        """The guard must not disable the fix loop generally."""
        fix_calls = []
        results = iter([
            CodeReviewResult(passed=False, findings=[CodeReviewFinding(file="app.py", risk="eval")]),
            CodeReviewResult(passed=True, findings=[]),
        ])
        monkeypatch.setattr(runner, "review_code", lambda diff: next(results))
        monkeypatch.setattr(
            runner,
            "fix_review_findings",
            lambda f: fix_calls.append(f) or PRFixResult(files_changed=["app.py"], fix_summary="fixed"),
        )

        assert runner.main() == 0
        assert len(fix_calls) == 1
