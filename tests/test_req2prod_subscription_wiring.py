"""
Unit tests for AGENT_BACKEND=subscription's multi-step orchestration in
req2prod/Req2Prod.py: review_project_readiness (6 personas + a synthesis task),
challenge_requirement (product_manager -> software_architect), and
build_feature (an isolated build_workspace() clone). run_via_subscription
and build_workspace are mocked throughout - no test here spawns a real
`claude` process, clones a real repo, or touches the filesystem outside
what a test explicitly sets up via tmp_path.
"""

from contextlib import contextmanager

import pytest

from req2prod import Req2Prod, backend


@pytest.fixture(autouse=True)
def subscription_mode(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "subscription")


class TestReadinessPanel:
    def _persona_result(self, persona: str) -> Req2Prod.PersonaReviewResult:
        return Req2Prod.PersonaReviewResult(persona=persona, passed=True, findings=[], skill_signal="solid tests")

    def test_calls_all_six_personas_then_the_cto_synthesis(self, monkeypatch):
        calls = []

        def fake_run_via_subscription(*, role, task_description, output_model, model, **kwargs):
            calls.append({"role": role, "model": model, "output_model": output_model})
            if output_model is Req2Prod.PersonaReviewResult:
                return self._persona_result(role)
            return Req2Prod.ReadinessReviewResult(ready_to_publish=True, verdict="looks solid")

        monkeypatch.setattr(Req2Prod, "run_via_subscription", fake_run_via_subscription)

        result = Req2Prod.review_project_readiness(app_repo_path="/tmp/app", infra_repo_path="/tmp/infra")

        assert result == Req2Prod.ReadinessReviewResult(ready_to_publish=True, verdict="looks solid")
        assert len(calls) == 7
        assert [c["output_model"] for c in calls[:6]] == [Req2Prod.PersonaReviewResult] * 6
        assert calls[6]["output_model"] is Req2Prod.ReadinessReviewResult
        assert [c["model"] for c in calls] == [
            Req2Prod._agent_models[k]["subscription"] for k, _, _ in Req2Prod._READINESS_PERSONAS
        ] + [Req2Prod._agent_models["cto"]["subscription"]]

    def test_feeds_all_six_persona_results_into_the_synthesis_context(self, monkeypatch):
        synthesis_context = {}

        def fake_run_via_subscription(*, output_model, extra_prompt_context="", **kwargs):
            if output_model is Req2Prod.PersonaReviewResult:
                return self._persona_result(kwargs["role"])
            synthesis_context["text"] = extra_prompt_context
            return Req2Prod.ReadinessReviewResult(ready_to_publish=True, verdict="ok")

        monkeypatch.setattr(Req2Prod, "run_via_subscription", fake_run_via_subscription)

        Req2Prod.review_project_readiness(app_repo_path="/tmp/app", infra_repo_path="/tmp/infra")

        for agent_key, _, _ in Req2Prod._READINESS_PERSONAS:
            assert agent_key in synthesis_context["text"]

    def test_any_persona_returning_none_short_circuits_before_synthesis(self, monkeypatch):
        call_count = {"n": 0}

        def fake_run_via_subscription(*, output_model, **kwargs):
            call_count["n"] += 1
            if output_model is not Req2Prod.PersonaReviewResult:
                pytest.fail("synthesis must not run if a persona failed")
            # Second persona (aws_lead_engineer) fails.
            return None if call_count["n"] == 2 else self._persona_result("x")

        monkeypatch.setattr(Req2Prod, "run_via_subscription", fake_run_via_subscription)

        result = Req2Prod.review_project_readiness(app_repo_path="/tmp/app", infra_repo_path="/tmp/infra")

        assert result is None
        assert call_count["n"] == 2

    def test_does_not_call_the_crewai_crew_in_subscription_mode(self, monkeypatch):
        # Crew is a pydantic model - can't monkeypatch an attribute on the
        # instance directly, only on the class.
        monkeypatch.setattr(
            Req2Prod.Crew, "kickoff", lambda self, **kw: pytest.fail("crew.kickoff() must not run in subscription mode")
        )
        monkeypatch.setattr(
            Req2Prod,
            "run_via_subscription",
            lambda *, output_model, **kw: (
                self._persona_result("x")
                if output_model is Req2Prod.PersonaReviewResult
                else Req2Prod.ReadinessReviewResult(ready_to_publish=True, verdict="ok")
            ),
        )

        Req2Prod.review_project_readiness(app_repo_path="/tmp/app", infra_repo_path="/tmp/infra")


class TestChallengeRequirement:
    def test_chains_pm_result_into_architect_context(self, monkeypatch):
        pm_result = Req2Prod.FeatureRequirementsResult(
            user_story="As a user...", acceptance_criteria=["works"], ready_for_development=True
        )
        seen = {}

        def fake_run_via_subscription(*, output_model, extra_prompt_context="", **kwargs):
            if output_model is Req2Prod.FeatureRequirementsResult:
                return pm_result
            seen["architect_context"] = extra_prompt_context
            return Req2Prod.ArchitectureDirectionResult(
                builds_on_existing_app=True, technical_notes="fine", ready_for_development=True
            )

        monkeypatch.setattr(Req2Prod, "run_via_subscription", fake_run_via_subscription)

        result = Req2Prod.challenge_requirement("Add a dark mode toggle.")

        assert result is not None
        returned_pm, returned_architect = result
        assert returned_pm == pm_result
        assert "As a user..." in seen["architect_context"]

    def test_returns_none_if_pm_result_is_none(self, monkeypatch):
        monkeypatch.setattr(
            Req2Prod,
            "run_via_subscription",
            lambda *, output_model, **kw: pytest.fail("architect must not run without a PM result")
            if output_model is Req2Prod.ArchitectureDirectionResult
            else None,
        )

        assert Req2Prod.challenge_requirement("Add a dark mode toggle.") is None

    def test_returns_none_if_architect_result_is_none(self, monkeypatch):
        pm_result = Req2Prod.FeatureRequirementsResult(user_story="x", ready_for_development=True)

        def fake_run_via_subscription(*, output_model, **kwargs):
            return pm_result if output_model is Req2Prod.FeatureRequirementsResult else None

        monkeypatch.setattr(Req2Prod, "run_via_subscription", fake_run_via_subscription)

        assert Req2Prod.challenge_requirement("Add a dark mode toggle.") is None

    def test_does_not_call_the_crewai_crew_in_subscription_mode(self, monkeypatch):
        monkeypatch.setattr(
            Req2Prod.Crew, "kickoff", lambda self, **kw: pytest.fail("crew.kickoff() must not run in subscription mode")
        )
        monkeypatch.setattr(
            Req2Prod,
            "run_via_subscription",
            lambda *, output_model, **kw: (
                Req2Prod.FeatureRequirementsResult(user_story="x", ready_for_development=True)
                if output_model is Req2Prod.FeatureRequirementsResult
                else Req2Prod.ArchitectureDirectionResult(
                    builds_on_existing_app=True, technical_notes="x", ready_for_development=True
                )
            ),
        )

        Req2Prod.challenge_requirement("Add a dark mode toggle.")


class TestBuildFeature:
    def _ready_inputs(self):
        pm = Req2Prod.FeatureRequirementsResult(user_story="x", ready_for_development=True)
        architect = Req2Prod.ArchitectureDirectionResult(
            builds_on_existing_app=True, technical_notes="x", ready_for_development=True
        )
        return pm, architect

    @contextmanager
    def _fake_workspace(self, path):
        yield path

    def test_runs_engineer_scoped_to_the_workspace_path(self, monkeypatch, tmp_path):
        pm, architect = self._ready_inputs()
        monkeypatch.setattr(Req2Prod, "build_workspace", lambda: self._fake_workspace(tmp_path))
        captured = {}

        def fake_run_via_subscription(*, cwd, allowed_tools, extra_prompt_context, **kwargs):
            captured["cwd"] = cwd
            captured["allowed_tools"] = allowed_tools
            captured["extra_prompt_context"] = extra_prompt_context
            return Req2Prod.FeatureBuildResult(
                branch_name="feature/x",
                summary="done",
                pr_url="https://github.com/marcohauff1975/job-finder/pull/99",
            )

        monkeypatch.setattr(Req2Prod, "run_via_subscription", fake_run_via_subscription)

        result = Req2Prod.build_feature(pm, architect)

        assert result is not None
        assert captured["cwd"] == str(tmp_path)
        assert "Edit" not in captured["allowed_tools"]  # native Edit must never be granted here
        assert "Read" not in captured["allowed_tools"]  # native Read likewise
        assert backend.TOOL_CLI_ALLOWED_TOOLS == captured["allowed_tools"]
        assert str(tmp_path) in captured["extra_prompt_context"]

    def test_rejects_a_fabricated_pr_url_same_as_api_mode(self, monkeypatch, tmp_path):
        pm, architect = self._ready_inputs()
        monkeypatch.setattr(Req2Prod, "build_workspace", lambda: self._fake_workspace(tmp_path))
        monkeypatch.setattr(
            Req2Prod,
            "run_via_subscription",
            lambda **kw: Req2Prod.FeatureBuildResult(
                branch_name="feature/x", summary="done", pr_url="https://github.com/someone-else/other-repo/pull/1"
            ),
        )

        assert Req2Prod.build_feature(pm, architect) is None

    def test_returns_none_when_workspace_setup_fails(self, monkeypatch):
        pm, architect = self._ready_inputs()

        @contextmanager
        def raise_workspace():
            raise RuntimeError("GITHUB_PR_PUSH_TOKEN is not set")
            yield  # pragma: no cover - unreachable, satisfies generator shape

        monkeypatch.setattr(Req2Prod, "build_workspace", raise_workspace)

        assert Req2Prod.build_feature(pm, architect) is None

    def test_still_raises_if_inputs_are_not_ready_for_development(self):
        pm = Req2Prod.FeatureRequirementsResult(user_story="x", ready_for_development=False)
        architect = Req2Prod.ArchitectureDirectionResult(
            builds_on_existing_app=True, technical_notes="x", ready_for_development=True
        )

        with pytest.raises(ValueError):
            Req2Prod.build_feature(pm, architect)


class TestEngineerQuestionIsNotAFailure:
    """The engineer is told to answer with a FeatureBuildResult, so anything
    else parses as nothing. It used to become None, which the UI could only
    render as "Something went wrong and no build result was produced" - so on
    2026-07-16, "the feature you asked for already exists" arrived looking
    like a malfunction and cost an SSH into production to read."""

    _REAL_PROSE = (
        "I should clarify: was this task given to me in error, because the "
        "feature already exists in streamlit_app.py?"
    )

    @contextmanager
    def _fake_workspace(self, path=None):
        yield path

    def _ready_inputs(self):
        return (
            Req2Prod.FeatureRequirementsResult(user_story="s", ready_for_development=True),
            Req2Prod.ArchitectureDirectionResult(
                builds_on_existing_app=True, technical_notes="n", ready_for_development=True
            ),
        )

    def test_api_path_returns_the_prose_out_of_the_validation_error(self, monkeypatch):
        """CrewAI's handle_partial_json re-raises a bare ValidationError when
        the final answer isn't clean JSON. The answer is in the error."""
        pm, architect = self._ready_inputs()
        monkeypatch.delenv("AGENT_BACKEND", raising=False)
        monkeypatch.setattr(Req2Prod, "build_workspace", lambda: self._fake_workspace())

        prose = self._REAL_PROSE

        class CrewThatGetsProseBack:
            def kickoff(self, inputs=None):
                # CrewAI validates the agent's final answer against
                # output_pydantic and re-raises when it isn't clean JSON.
                Req2Prod.FeatureBuildResult.model_validate({"summary": prose})

        # Crew is itself a pydantic model, so its methods can't be patched -
        # replace the whole object.
        monkeypatch.setattr(Req2Prod, "feature_build_crew", CrewThatGetsProseBack())

        result = Req2Prod.build_feature(pm, architect)

        assert isinstance(result, Req2Prod.EngineerQuestion)
        assert result.question == self._REAL_PROSE

    def test_a_real_infrastructure_failure_is_still_None(self, monkeypatch):
        """The ValidationError catch is narrow on purpose - a failed
        build_workspace() clone (no GITHUB_PR_PUSH_TOKEN) is a real failure and
        must not be dressed up as the engineer having a question."""
        pm, architect = self._ready_inputs()
        monkeypatch.delenv("AGENT_BACKEND", raising=False)

        def boom():
            raise RuntimeError("GITHUB_PR_PUSH_TOKEN is not set")

        monkeypatch.setattr(Req2Prod, "build_workspace", boom)

        assert Req2Prod.build_feature(pm, architect) is None

    def test_subscription_path_returns_the_unparsed_reply(self, monkeypatch, tmp_path):
        pm, architect = self._ready_inputs()
        monkeypatch.setenv("AGENT_BACKEND", "subscription")
        monkeypatch.setattr(Req2Prod, "build_workspace", lambda: self._fake_workspace(tmp_path))

        def fake_run_via_subscription(**kwargs):
            # what backend.py does when the reply carries no JSON object
            kwargs["on_unparsed"](self._REAL_PROSE)
            return None

        monkeypatch.setattr(Req2Prod, "run_via_subscription", fake_run_via_subscription)

        result = Req2Prod.build_feature(pm, architect)

        assert isinstance(result, Req2Prod.EngineerQuestion)
        assert result.question == self._REAL_PROSE

    def test_subscription_path_with_no_reply_at_all_is_still_None(self, monkeypatch, tmp_path):
        """Nothing said is a failure; something said isn't."""
        pm, architect = self._ready_inputs()
        monkeypatch.setenv("AGENT_BACKEND", "subscription")
        monkeypatch.setattr(Req2Prod, "build_workspace", lambda: self._fake_workspace(tmp_path))
        monkeypatch.setattr(Req2Prod, "run_via_subscription", lambda **kw: None)

        assert Req2Prod.build_feature(pm, architect) is None

    def test_a_real_build_is_unaffected(self, monkeypatch, tmp_path):
        pm, architect = self._ready_inputs()
        monkeypatch.setenv("AGENT_BACKEND", "subscription")
        monkeypatch.setattr(Req2Prod, "build_workspace", lambda: self._fake_workspace(tmp_path))
        monkeypatch.setattr(
            Req2Prod,
            "run_via_subscription",
            lambda **kw: Req2Prod.FeatureBuildResult(
                branch_name="feature/x",
                summary="done",
                pr_url="https://github.com/marcohauff1975/job-finder/pull/99",
            ),
        )

        result = Req2Prod.build_feature(pm, architect)

        assert isinstance(result, Req2Prod.FeatureBuildResult)
        assert result.pr_url.endswith("/99")
