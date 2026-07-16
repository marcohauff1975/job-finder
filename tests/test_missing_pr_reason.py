"""
When the engineer finishes but can't open a PR, say why.

Observed 2026-07-16: the Remove-logo demo produced a valid FeatureBuildResult
whose summary read "Unable to complete due to pre-existing branch conflicts
when attempting to create the feature branch and open PR" - exactly right, a
merged demo branch of the same name was still on the remote. pr_url was empty,
_PR_URL_PATTERN rejected it (correctly - it must never claim a PR that doesn't
exist), and build_feature returned None. The UI said "Something went wrong and
no build result was produced", and reading the real reason took an SSH into
production.

Same shape as EngineerQuestion's own case, other arm: there the reply wasn't
valid JSON; here it was valid JSON with no PR in it.
"""

from contextlib import contextmanager

import pytest

from req2prod import Req2Prod

_REAL_SUMMARY = (
    "Removed the st.markdown() call that rendered the logo. Unable to complete "
    "due to pre-existing branch conflicts when attempting to create the feature "
    "branch and open PR"
)


@contextmanager
def _workspace(path=None):
    yield path


def _ready():
    return (
        Req2Prod.FeatureRequirementsResult(user_story="s", ready_for_development=True),
        Req2Prod.ArchitectureDirectionResult(
            builds_on_existing_app=True, technical_notes="n", ready_for_development=True
        ),
    )


def _result(pr_url, summary=_REAL_SUMMARY):
    return Req2Prod.FeatureBuildResult(
        branch_name="feature/remove-req2prod-demo-logo",
        files_changed=["streamlit_app.py"],
        summary=summary,
        pr_url=pr_url,
    )


class TestSubscriptionPath:
    @pytest.fixture(autouse=True)
    def _sub(self, monkeypatch, tmp_path):
        monkeypatch.setenv("AGENT_BACKEND", "subscription")
        monkeypatch.setattr(Req2Prod, "build_workspace", lambda: _workspace(tmp_path))

    def test_an_empty_pr_url_returns_the_engineers_reason(self, monkeypatch):
        monkeypatch.setattr(Req2Prod, "run_via_subscription", lambda **kw: _result(""))

        out = Req2Prod.build_feature(*_ready())

        assert isinstance(out, Req2Prod.EngineerQuestion)
        assert "couldn't open a pull request" in out.question
        assert "pre-existing branch conflicts" in out.question

    def test_a_fabricated_pr_url_is_still_rejected(self, monkeypatch):
        """The guard exists because the engineer once invented a plausible URL
        for a different repo. Explaining the failure must not soften it."""
        monkeypatch.setattr(
            Req2Prod,
            "run_via_subscription",
            lambda **kw: _result("https://github.com/mhauff/crewai-starter/pull/[PR_NUMBER]"),
        )

        out = Req2Prod.build_feature(*_ready())

        assert isinstance(out, Req2Prod.EngineerQuestion)
        assert not isinstance(out, Req2Prod.FeatureBuildResult)

    def test_a_real_pr_is_untouched(self, monkeypatch):
        monkeypatch.setattr(
            Req2Prod,
            "run_via_subscription",
            lambda **kw: _result("https://github.com/marcohauff1975/job-finder/pull/99"),
        )

        out = Req2Prod.build_feature(*_ready())

        assert isinstance(out, Req2Prod.FeatureBuildResult)
        assert out.pr_url.endswith("/99")

    def test_no_result_at_all_is_still_None(self, monkeypatch):
        """Nothing said is a failure; something said isn't."""
        monkeypatch.setattr(Req2Prod, "run_via_subscription", lambda **kw: None)

        assert Req2Prod.build_feature(*_ready()) is None


class TestApiPath:
    @pytest.fixture(autouse=True)
    def _api(self, monkeypatch):
        monkeypatch.delenv("AGENT_BACKEND", raising=False)
        monkeypatch.setattr(Req2Prod, "build_workspace", lambda: _workspace())

    def _crew_returns(self, monkeypatch, build_result):
        class Crew:
            def kickoff(self, inputs=None):
                return type("Out", (), {"pydantic": build_result})()

        monkeypatch.setattr(Req2Prod, "feature_build_crew", Crew())

    def test_an_empty_pr_url_returns_the_engineers_reason(self, monkeypatch):
        self._crew_returns(monkeypatch, _result(""))

        out = Req2Prod.build_feature(*_ready())

        assert isinstance(out, Req2Prod.EngineerQuestion)
        assert "pre-existing branch conflicts" in out.question

    def test_it_copes_with_an_empty_summary(self, monkeypatch):
        """No reason given is still better than the old silence."""
        self._crew_returns(monkeypatch, _result("", summary=""))

        out = Req2Prod.build_feature(*_ready())

        assert isinstance(out, Req2Prod.EngineerQuestion)
        assert "couldn't open a pull request" in out.question
