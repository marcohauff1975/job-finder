"""
Unit tests for AGENT_BACKEND=subscription's multi-step orchestration in
sdlc/SDLC.py: review_project_readiness (6 personas + a synthesis task),
challenge_requirement (product_manager -> software_architect), and
build_feature (an isolated build_workspace() clone). run_via_subscription
and build_workspace are mocked throughout - no test here spawns a real
`claude` process, clones a real repo, or touches the filesystem outside
what a test explicitly sets up via tmp_path.
"""

from contextlib import contextmanager

import pytest

from sdlc import SDLC


@pytest.fixture(autouse=True)
def subscription_mode(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND", "subscription")


class TestReadinessPanel:
    def _persona_result(self, persona: str) -> SDLC.PersonaReviewResult:
        return SDLC.PersonaReviewResult(persona=persona, passed=True, findings=[], skill_signal="solid tests")

    def test_calls_all_six_personas_then_the_cto_synthesis(self, monkeypatch):
        calls = []

        def fake_run_via_subscription(*, role, task_description, output_model, model, **kwargs):
            calls.append({"role": role, "model": model, "output_model": output_model})
            if output_model is SDLC.PersonaReviewResult:
                return self._persona_result(role)
            return SDLC.ReadinessReviewResult(ready_to_publish=True, verdict="looks solid")

        monkeypatch.setattr(SDLC, "run_via_subscription", fake_run_via_subscription)

        result = SDLC.review_project_readiness(app_repo_path="/tmp/app", infra_repo_path="/tmp/infra")

        assert result == SDLC.ReadinessReviewResult(ready_to_publish=True, verdict="looks solid")
        assert len(calls) == 7
        assert [c["output_model"] for c in calls[:6]] == [SDLC.PersonaReviewResult] * 6
        assert calls[6]["output_model"] is SDLC.ReadinessReviewResult
        assert [c["model"] for c in calls] == [SDLC._agent_models[k] for k, _, _ in SDLC._READINESS_PERSONAS] + [
            SDLC._agent_models["cto"]
        ]

    def test_feeds_all_six_persona_results_into_the_synthesis_context(self, monkeypatch):
        synthesis_context = {}

        def fake_run_via_subscription(*, output_model, extra_prompt_context="", **kwargs):
            if output_model is SDLC.PersonaReviewResult:
                return self._persona_result(kwargs["role"])
            synthesis_context["text"] = extra_prompt_context
            return SDLC.ReadinessReviewResult(ready_to_publish=True, verdict="ok")

        monkeypatch.setattr(SDLC, "run_via_subscription", fake_run_via_subscription)

        SDLC.review_project_readiness(app_repo_path="/tmp/app", infra_repo_path="/tmp/infra")

        for agent_key, _, _ in SDLC._READINESS_PERSONAS:
            assert agent_key in synthesis_context["text"]

    def test_any_persona_returning_none_short_circuits_before_synthesis(self, monkeypatch):
        call_count = {"n": 0}

        def fake_run_via_subscription(*, output_model, **kwargs):
            call_count["n"] += 1
            if output_model is not SDLC.PersonaReviewResult:
                pytest.fail("synthesis must not run if a persona failed")
            # Second persona (aws_lead_engineer) fails.
            return None if call_count["n"] == 2 else self._persona_result("x")

        monkeypatch.setattr(SDLC, "run_via_subscription", fake_run_via_subscription)

        result = SDLC.review_project_readiness(app_repo_path="/tmp/app", infra_repo_path="/tmp/infra")

        assert result is None
        assert call_count["n"] == 2

    def test_does_not_call_the_crewai_crew_in_subscription_mode(self, monkeypatch):
        # Crew is a pydantic model - can't monkeypatch an attribute on the
        # instance directly, only on the class.
        monkeypatch.setattr(
            SDLC.Crew, "kickoff", lambda self, **kw: pytest.fail("crew.kickoff() must not run in subscription mode")
        )
        monkeypatch.setattr(
            SDLC,
            "run_via_subscription",
            lambda *, output_model, **kw: (
                self._persona_result("x")
                if output_model is SDLC.PersonaReviewResult
                else SDLC.ReadinessReviewResult(ready_to_publish=True, verdict="ok")
            ),
        )

        SDLC.review_project_readiness(app_repo_path="/tmp/app", infra_repo_path="/tmp/infra")


class TestChallengeRequirement:
    def test_chains_pm_result_into_architect_context(self, monkeypatch):
        pm_result = SDLC.FeatureRequirementsResult(
            user_story="As a user...", acceptance_criteria=["works"], ready_for_development=True
        )
        seen = {}

        def fake_run_via_subscription(*, output_model, extra_prompt_context="", **kwargs):
            if output_model is SDLC.FeatureRequirementsResult:
                return pm_result
            seen["architect_context"] = extra_prompt_context
            return SDLC.ArchitectureDirectionResult(
                builds_on_existing_app=True, technical_notes="fine", ready_for_development=True
            )

        monkeypatch.setattr(SDLC, "run_via_subscription", fake_run_via_subscription)

        result = SDLC.challenge_requirement("Add a dark mode toggle.")

        assert result is not None
        returned_pm, returned_architect = result
        assert returned_pm == pm_result
        assert "As a user..." in seen["architect_context"]

    def test_returns_none_if_pm_result_is_none(self, monkeypatch):
        monkeypatch.setattr(
            SDLC,
            "run_via_subscription",
            lambda *, output_model, **kw: pytest.fail("architect must not run without a PM result")
            if output_model is SDLC.ArchitectureDirectionResult
            else None,
        )

        assert SDLC.challenge_requirement("Add a dark mode toggle.") is None

    def test_returns_none_if_architect_result_is_none(self, monkeypatch):
        pm_result = SDLC.FeatureRequirementsResult(user_story="x", ready_for_development=True)

        def fake_run_via_subscription(*, output_model, **kwargs):
            return pm_result if output_model is SDLC.FeatureRequirementsResult else None

        monkeypatch.setattr(SDLC, "run_via_subscription", fake_run_via_subscription)

        assert SDLC.challenge_requirement("Add a dark mode toggle.") is None

    def test_does_not_call_the_crewai_crew_in_subscription_mode(self, monkeypatch):
        monkeypatch.setattr(
            SDLC.Crew, "kickoff", lambda self, **kw: pytest.fail("crew.kickoff() must not run in subscription mode")
        )
        monkeypatch.setattr(
            SDLC,
            "run_via_subscription",
            lambda *, output_model, **kw: (
                SDLC.FeatureRequirementsResult(user_story="x", ready_for_development=True)
                if output_model is SDLC.FeatureRequirementsResult
                else SDLC.ArchitectureDirectionResult(
                    builds_on_existing_app=True, technical_notes="x", ready_for_development=True
                )
            ),
        )

        SDLC.challenge_requirement("Add a dark mode toggle.")


class TestBuildFeature:
    def _ready_inputs(self):
        pm = SDLC.FeatureRequirementsResult(user_story="x", ready_for_development=True)
        architect = SDLC.ArchitectureDirectionResult(
            builds_on_existing_app=True, technical_notes="x", ready_for_development=True
        )
        return pm, architect

    @contextmanager
    def _fake_workspace(self, path):
        yield path

    def test_runs_engineer_scoped_to_the_workspace_path(self, monkeypatch, tmp_path):
        pm, architect = self._ready_inputs()
        monkeypatch.setattr(SDLC, "build_workspace", lambda: self._fake_workspace(tmp_path))
        captured = {}

        def fake_run_via_subscription(*, cwd, allowed_tools, extra_prompt_context, **kwargs):
            captured["cwd"] = cwd
            captured["allowed_tools"] = allowed_tools
            captured["extra_prompt_context"] = extra_prompt_context
            return SDLC.FeatureBuildResult(
                branch_name="feature/x",
                summary="done",
                pr_url="https://github.com/marcohauff1975/job-finder/pull/99",
            )

        monkeypatch.setattr(SDLC, "run_via_subscription", fake_run_via_subscription)

        result = SDLC.build_feature(pm, architect)

        assert result is not None
        assert captured["cwd"] == str(tmp_path)
        assert "Edit" not in captured["allowed_tools"]  # native Edit must never be granted here
        assert "Read" not in captured["allowed_tools"]  # native Read likewise
        assert "Bash(python -m sdlc.tool_cli *)" == captured["allowed_tools"]
        assert str(tmp_path) in captured["extra_prompt_context"]

    def test_rejects_a_fabricated_pr_url_same_as_api_mode(self, monkeypatch, tmp_path):
        pm, architect = self._ready_inputs()
        monkeypatch.setattr(SDLC, "build_workspace", lambda: self._fake_workspace(tmp_path))
        monkeypatch.setattr(
            SDLC,
            "run_via_subscription",
            lambda **kw: SDLC.FeatureBuildResult(
                branch_name="feature/x", summary="done", pr_url="https://github.com/someone-else/other-repo/pull/1"
            ),
        )

        assert SDLC.build_feature(pm, architect) is None

    def test_returns_none_when_workspace_setup_fails(self, monkeypatch):
        pm, architect = self._ready_inputs()

        @contextmanager
        def raise_workspace():
            raise RuntimeError("GITHUB_PR_PUSH_TOKEN is not set")
            yield  # pragma: no cover - unreachable, satisfies generator shape

        monkeypatch.setattr(SDLC, "build_workspace", raise_workspace)

        assert SDLC.build_feature(pm, architect) is None

    def test_still_raises_if_inputs_are_not_ready_for_development(self):
        pm = SDLC.FeatureRequirementsResult(user_story="x", ready_for_development=False)
        architect = SDLC.ArchitectureDirectionResult(
            builds_on_existing_app=True, technical_notes="x", ready_for_development=True
        )

        with pytest.raises(ValueError):
            SDLC.build_feature(pm, architect)
