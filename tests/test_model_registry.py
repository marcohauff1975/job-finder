"""
Unit tests for req2prod/model_registry.py's per-backend model storage: the
flat-to-nested migration, and set_agent_model's backend-aware persistence
+ conditional live-mutation. Each test gets its own throwaway JSON file
via the config_path fixture below, with CONFIG_PATH monkeypatched to
point at it - never touches the real config/agent_models.json.
"""

import json

import pytest

from req2prod import model_registry


@pytest.fixture
def config_path(monkeypatch, tmp_path):
    path = tmp_path / "agent_models.json"
    monkeypatch.setattr(model_registry, "CONFIG_PATH", path)
    monkeypatch.setattr(model_registry, "DEFAULT_CONFIG_PATH", tmp_path / "agent_models.default.json")
    return path


class TestLoadAgentModelsMigration:
    def test_migrates_a_flat_entry_in_memory(self, config_path):
        config_path.write_text(json.dumps({"code_reviewer": "anthropic/claude-sonnet-5"}))

        loaded = model_registry.load_agent_models()

        assert loaded == {
            "code_reviewer": {"api": "anthropic/claude-sonnet-5", "subscription": "anthropic/claude-sonnet-5"}
        }

    def test_persists_the_migration_back_to_disk(self, config_path):
        config_path.write_text(json.dumps({"code_reviewer": "anthropic/claude-sonnet-5"}))

        model_registry.load_agent_models()

        on_disk = json.loads(config_path.read_text())
        assert on_disk == {
            "code_reviewer": {"api": "anthropic/claude-sonnet-5", "subscription": "anthropic/claude-sonnet-5"}
        }

    def test_leaves_an_already_nested_entry_untouched(self, config_path):
        nested = {"code_reviewer": {"api": "anthropic/claude-sonnet-5", "subscription": "anthropic/claude-opus-4-8"}}
        config_path.write_text(json.dumps(nested))

        loaded = model_registry.load_agent_models()

        assert loaded == nested

    def test_does_not_rewrite_the_file_when_nothing_needed_migrating(self, config_path):
        nested = {"code_reviewer": {"api": "anthropic/claude-sonnet-5", "subscription": "anthropic/claude-opus-4-8"}}
        config_path.write_text(json.dumps(nested))
        mtime_before = config_path.stat().st_mtime_ns

        model_registry.load_agent_models()

        assert config_path.stat().st_mtime_ns == mtime_before


class TestSetAgentModel:
    def test_persists_independently_per_backend(self, config_path):
        config_path.write_text(
            json.dumps({"code_reviewer": {"api": "anthropic/claude-sonnet-5", "subscription": "anthropic/claude-sonnet-5"}})
        )

        model_registry.set_agent_model("code_reviewer", "anthropic/claude-opus-4-8", "subscription")

        on_disk = json.loads(config_path.read_text())
        assert on_disk["code_reviewer"] == {
            "api": "anthropic/claude-sonnet-5",
            "subscription": "anthropic/claude-opus-4-8",
        }

    def test_creates_the_agent_entry_if_missing(self, config_path):
        config_path.write_text(json.dumps({}))

        model_registry.set_agent_model("code_reviewer", "anthropic/claude-opus-4-8", "api")

        on_disk = json.loads(config_path.read_text())
        assert on_disk["code_reviewer"]["api"] == "anthropic/claude-opus-4-8"

    def test_subscription_change_does_not_touch_a_live_agent_object(self, config_path, monkeypatch):
        config_path.write_text(
            json.dumps({"code_reviewer": {"api": "anthropic/claude-sonnet-5", "subscription": "anthropic/claude-sonnet-5"}})
        )

        class FakeReq2ProdModule:
            AGENTS_BY_KEY = {"code_reviewer": None}

            def __setattr__(self, *a):
                pytest.fail("subscription-mode change must not touch AGENTS_BY_KEY")

        import sys

        monkeypatch.setitem(sys.modules, "req2prod.Req2Prod", FakeReq2ProdModule())

        # Should not raise/fail even though AGENTS_BY_KEY["code_reviewer"]
        # is None (a real Agent's .llm would be mutated only for "api").
        model_registry.set_agent_model("code_reviewer", "anthropic/claude-opus-4-8", "subscription")

    def test_api_change_live_mutates_the_constructed_agent(self, config_path, monkeypatch):
        config_path.write_text(
            json.dumps({"code_reviewer": {"api": "anthropic/claude-sonnet-5", "subscription": "anthropic/claude-sonnet-5"}})
        )

        class FakeAgent:
            llm = "old-llm"

        fake_agent = FakeAgent()

        class FakeReq2ProdModule:
            AGENTS_BY_KEY = {"code_reviewer": fake_agent}

        import sys

        monkeypatch.setitem(sys.modules, "req2prod.Req2Prod", FakeReq2ProdModule())

        model_registry.set_agent_model("code_reviewer", "anthropic/claude-opus-4-8", "api")

        # LLM(model=...) is a factory that returns a provider-specific
        # completion object (AnthropicCompletion here), not a bare LLM
        # instance, and stores .model with the "anthropic/" prefix
        # already stripped - assert on that rather than the exact type.
        assert fake_agent.llm != "old-llm"
        assert fake_agent.llm.model == "claude-opus-4-8"
