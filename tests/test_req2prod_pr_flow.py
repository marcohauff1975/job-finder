"""
Unit tests for req2prod_pr_flow.py's PR-fetching logic - specifically that
it correctly distinguishes pr_fix_agent/pr_arbiter activity from plain
code_reviewer activity using only real GitHub data (a commit-message
prefix and a review-body prefix), since neither of those two agents
has its own GitHub identity to key off - pr_fix_agent never posts a
review at all, and pr_arbiter posts under the same MarcoAIagent bot
account code_reviewer uses. Mocks _gh_get entirely - no real network
calls or GITHUB_ACTIONS_TOKEN needed.
"""

import pytest

import req2prod_pr_flow as flow


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """get_latest_pr_flow is @st.cache_data(ttl=15) - without clearing
    between tests, a later test could silently receive an earlier
    test's cached result instead of hitting its own mocked _gh_get. It
    also checks for a real GITHUB_ACTIONS_TOKEN before calling
    _gh_get at all - without one it short-circuits to error="no_token"
    and _gh_get (mocked or not) is never actually called."""
    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "fake-token")
    flow.get_latest_pr_flow.clear()
    yield
    flow.get_latest_pr_flow.clear()


def _pr(number: int = 7) -> dict:
    return {
        "number": number,
        "title": "Add a new feature",
        "html_url": f"https://github.com/marcohauff1975/job-finder/pull/{number}",
        "user": {"login": "software-engineer"},
        "created_at": "2026-07-10T09:00:00Z",
        "state": "open",
        "merged_at": None,
        "merge_commit_sha": None,
        "head": {"ref": "feature/test-branch"},
    }


def _review(*, commit_id: str, body: str, state: str = "APPROVED") -> dict:
    return {
        "user": {"login": "MarcoAIagent"},
        "state": state,
        "body": body,
        "submitted_at": "2026-07-10T09:20:00Z",
        "commit_id": commit_id,
        "html_url": "https://github.com/marcohauff1975/job-finder/pull/7#review-1",
    }


def _commit(sha: str, message: str) -> dict:
    return {"sha": sha, "commit": {"message": message}}


def _mock_gh_get(pr: dict, reviews: list[dict], commits: list[dict], monkeypatch) -> None:
    number = pr["number"]
    responses = {
        "/pulls": [pr],
        f"/pulls/{number}": pr,
        f"/pulls/{number}/reviews": reviews,
        f"/pulls/{number}/commits": commits,
        f"/actions/workflows/{flow.PIPELINE_WORKFLOW_FILE}/runs": {"workflow_runs": []},
    }

    def fake(path, token, params=None):
        if path not in responses:
            raise AssertionError(f"Unexpected _gh_get call: {path} (params={params})")
        return responses[path], True

    monkeypatch.setattr(flow, "_gh_get", fake)


class TestSummarizeReview:
    def test_strips_code_reviewer_prefix(self):
        body = "**Automated review by code_reviewer:** no issues found."
        assert flow._summarize_review(body) == "no issues found."

    def test_strips_arbiter_prefix(self):
        body = "**Secondary review by pr_arbiter:** remaining issue is cosmetic only."
        assert flow._summarize_review(body) == "remaining issue is cosmetic only."

    def test_prefers_first_bullet_over_prefix_text(self):
        body = (
            "**Automated review by code_reviewer:** no issues found, after fixes:\n\n"
            "- app.py:10 - fixed eval() usage"
        )
        assert flow._summarize_review(body) == "app.py:10 - fixed eval() usage"

    def test_plain_body_with_no_known_prefix(self):
        assert flow._summarize_review("Looks fine to me.") == "Looks fine to me."


class TestPlainCodeReviewerPass:
    def test_no_fix_agent_box_when_no_fix_commit(self, monkeypatch):
        pr = _pr()
        review = _review(
            commit_id="headsha", body="**Automated review by code_reviewer:** no issues found."
        )
        _mock_gh_get(pr, [review], [_commit("headsha", "Add the feature")], monkeypatch)

        pr_info, stages, error = flow.get_latest_pr_flow()

        assert error is None
        assert "pr_fix_agent" not in [s["kind"] for s in stages]
        review_stage = next(s for s in stages if s["kind"] == "code_review")
        assert review_stage["label"] == "Code Review (round 1)"
        assert review_stage["state"] == "ok"


class TestFixAgentDetection:
    def test_inserts_fix_agent_box_before_the_review_it_enabled(self, monkeypatch):
        pr = _pr()
        review = _review(
            commit_id="fixsha",
            body=(
                "**Automated review by code_reviewer:** no issues found, after "
                "pr_fix_agent addressed these findings from an earlier round:\n\n"
                "- app.py:10 - fixed eval() usage"
            ),
        )
        commits = [
            _commit("originalsha", "Add the feature"),
            _commit("fixsha", "[pr-fix-agent] Address code review findings in app.py"),
        ]
        _mock_gh_get(pr, [review], commits, monkeypatch)

        pr_info, stages, error = flow.get_latest_pr_flow()

        assert error is None
        assert [s["kind"] for s in stages] == ["merge_request", "pr_fix_agent", "code_review"]
        fix_stage = stages[1]
        assert fix_stage["agent"] == "pr_fix_agent"
        assert "app.py" in fix_stage["summary"]

    def test_no_fix_agent_box_when_reviewed_commit_isnt_a_fix_commit(self, monkeypatch):
        """A [pr-fix-agent] commit exists on the PR, but this review was
        submitted against a different (later, human) commit - should not
        show a fix-agent box it wasn't actually associated with."""
        pr = _pr()
        review = _review(
            commit_id="latersha", body="**Automated review by code_reviewer:** no issues found."
        )
        commits = [
            _commit("originalsha", "Add the feature"),
            _commit("fixsha", "[pr-fix-agent] Address code review findings in app.py"),
            _commit("latersha", "A further human commit"),
        ]
        _mock_gh_get(pr, [review], commits, monkeypatch)

        pr_info, stages, error = flow.get_latest_pr_flow()

        assert "pr_fix_agent" not in [s["kind"] for s in stages]


class TestArbiterDetection:
    def test_labels_arbiter_approval_distinctly_from_code_reviewer(self, monkeypatch):
        pr = _pr()
        review = _review(
            commit_id="headsha",
            body="**Secondary review by pr_arbiter:** remaining issue is cosmetic, safe to ship.",
            state="APPROVED",
        )
        _mock_gh_get(pr, [review], [_commit("headsha", "Add the feature")], monkeypatch)

        pr_info, stages, error = flow.get_latest_pr_flow()

        review_stage = next(s for s in stages if s["kind"] == "code_review")
        assert review_stage["label"] == "PR Arbiter (round 1)"
        assert "pr_arbiter" in review_stage["agent"]
        assert review_stage["state"] == "ok"

    def test_arbiter_rejection_is_blocked_not_changes_requested(self, monkeypatch):
        pr = _pr()
        review = _review(
            commit_id="headsha",
            body=(
                "**Secondary review by pr_arbiter:** not safe to merge automatically.\n\n"
                "Still calls eval() on user input.\n\n"
                "- Unsanitized eval() in app.py"
            ),
            state="CHANGES_REQUESTED",
        )
        _mock_gh_get(pr, [review], [_commit("headsha", "Add the feature")], monkeypatch)

        pr_info, stages, error = flow.get_latest_pr_flow()

        review_stage = next(s for s in stages if s["kind"] == "code_review")
        assert review_stage["state"] == "blocked"
        assert review_stage["state"] != "changes_requested"
        assert review_stage["label"] == "PR Arbiter (round 1)"

    def test_blocked_state_gets_no_rework_loop_in_svg(self, monkeypatch):
        """A regression guard on the SVG renderer itself: 'blocked' must
        never trigger the dashed loop-back arrow that 'changes_requested'
        does, since nothing auto-retries a pr_arbiter block."""
        stages = [
            {
                "kind": "merge_request",
                "label": "Merge Request #7",
                "agent": "software-engineer",
                "state": "ok",
                "summary": "Add a new feature",
                "url": "http://example.com",
            },
            {
                "kind": "code_review",
                "label": "PR Arbiter (round 1)",
                "agent": "pr_arbiter (MarcoAIagent)",
                "state": "blocked",
                "summary": "Still calls eval() on user input.",
                "url": "http://example.com",
            },
        ]

        svg = flow.render_pr_flow_svg(stages)

        assert "if not ok - rework" not in svg


def _merged_pr(number: int = 7) -> dict:
    pr = _pr(number)
    pr.update(state="closed", merged_at="2026-07-10T09:30:00Z", merge_commit_sha="mergesha")
    return pr


def _job(name: str, steps: list[dict], conclusion: str = "success") -> dict:
    return {"name": name, "status": "completed", "conclusion": conclusion, "steps": steps}


def _deploy_steps(*, restart: str = "success") -> list[dict]:
    """The real deploy job's step names, in order - only the ones
    _deploy_stages actually looks for."""
    return [
        {"name": "Set up job", "conclusion": "success"},
        {"name": "Pull latest main on the server", "conclusion": "success"},
        {"name": "Decide whether a restart is actually needed", "conclusion": "success"},
        {"name": "Restart jobfinder.service", "conclusion": restart},
        {"name": "Run prod_tester (and rollback_agent if needed)", "conclusion": "success"},
    ]


# resolve_backend's real steps: three, and none of them is a deploy step.
_RESOLVE_BACKEND_STEPS = [
    {"name": "Set up job", "conclusion": "success"},
    {"name": "Run if [ \"$REQUESTED_BACKEND\" = \"subscription\" ]", "conclusion": "success"},
    {"name": "Complete job", "conclusion": "success"},
]


def _mock_deploy_run(jobs: list[dict], monkeypatch, *, run_status: str = "completed") -> None:
    """Mocks a merged PR whose merge commit triggered one push run of the
    pipeline, with the given jobs."""
    pr = _merged_pr()
    number = pr["number"]
    run = {
        "id": 123,
        "head_sha": "mergesha",
        "status": run_status,
        "html_url": "https://github.com/marcohauff1975/job-finder/actions/runs/123",
    }
    responses = {
        "/pulls": [pr],
        f"/pulls/{number}": pr,
        f"/pulls/{number}/reviews": [],
        f"/pulls/{number}/commits": [_commit("headsha", "Add the feature")],
        f"/actions/workflows/{flow.PIPELINE_WORKFLOW_FILE}/runs": {"workflow_runs": [run]},
        "/actions/runs/123/jobs": {"jobs": jobs},
        f"/actions/workflows/{flow.DEVOPS_AGENT_WORKFLOW_FILE}/runs": {"workflow_runs": []},
    }

    def fake(path, token, params=None):
        if path not in responses:
            raise AssertionError(f"Unexpected _gh_get call: {path} (params={params})")
        return responses[path], True

    monkeypatch.setattr(flow, "_gh_get", fake)


class TestDeployStagesReadTheDeployJob:
    def test_reads_the_deploy_job_not_whichever_job_is_first(self, monkeypatch):
        """The run has three jobs and the API returns resolve_backend first.
        Reading jobs[0] searched its three steps for "Pull latest main on the
        server", never found it, and reported every completed deploy as
        "Deploy step didn't complete." - amber, regardless of what the deploy
        actually did."""
        _mock_deploy_run(
            [
                _job("resolve_backend", _RESOLVE_BACKEND_STEPS),
                _job("deploy", _deploy_steps()),
                _job("code_review", [], conclusion="skipped"),
            ],
            monkeypatch,
        )

        _pr_info, stages, error = flow.get_latest_pr_flow()

        assert error is None
        push = next(s for s in stages if s["kind"] == "push_to_prod")
        assert push["state"] == "ok"
        assert "restarted jobfinder.service" in push["summary"]

    def test_a_genuinely_failed_restart_is_still_reported(self, monkeypatch):
        """The counterpart to the above: reading the right job must not mean
        reading it optimistically."""
        _mock_deploy_run(
            [
                _job("resolve_backend", _RESOLVE_BACKEND_STEPS),
                _job("deploy", _deploy_steps(restart="failure"), conclusion="failure"),
            ],
            monkeypatch,
        )

        _pr_info, stages, error = flow.get_latest_pr_flow()

        assert error is None
        push = next(s for s in stages if s["kind"] == "push_to_prod")
        assert push["state"] == "failed"

    def test_a_skipped_deploy_shows_no_deploy_stages(self, monkeypatch):
        """AUTO_DEPLOY_ON_MERGE off means merging deliberately doesn't deploy.
        The job is present but skipped with no steps - that's the configured
        behaviour, not a deploy that broke."""
        _mock_deploy_run(
            [
                _job("resolve_backend", _RESOLVE_BACKEND_STEPS),
                _job("deploy", [], conclusion="skipped"),
            ],
            monkeypatch,
        )

        _pr_info, stages, error = flow.get_latest_pr_flow()

        assert error is None
        assert [s for s in stages if s["kind"] == "push_to_prod"] == []

    def test_deploy_job_not_created_yet_reads_as_running(self, monkeypatch):
        """deploy `needs: resolve_backend`, so early in a run it doesn't exist
        yet. That's in-progress, not failed."""
        _mock_deploy_run(
            [_job("resolve_backend", _RESOLVE_BACKEND_STEPS)],
            monkeypatch,
            run_status="in_progress",
        )

        _pr_info, stages, error = flow.get_latest_pr_flow()

        assert error is None
        push = next(s for s in stages if s["kind"] == "push_to_prod")
        assert push["state"] == "running"
