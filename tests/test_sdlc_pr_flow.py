"""
Unit tests for sdlc_pr_flow.py's PR-fetching logic - specifically that
it correctly distinguishes pr_fix_agent/pr_arbiter activity from plain
code_reviewer activity using only real GitHub data (a commit-message
prefix and a review-body prefix), since neither of those two agents
has its own GitHub identity to key off - pr_fix_agent never posts a
review at all, and pr_arbiter posts under the same MarcoAIagent bot
account code_reviewer uses. Mocks _gh_get entirely - no real network
calls or GITHUB_ACTIONS_TOKEN needed.
"""

import pytest

import sdlc_pr_flow as flow


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
