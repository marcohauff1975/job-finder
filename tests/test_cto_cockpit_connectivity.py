"""
Unit tests for cto_cockpit_connectivity.py - the non-Streamlit save/test
logic behind the CTO Cockpit "Connectivity" tab. No test here makes a
real AWS/GitHub/Anthropic network call or touches the real repo .env -
boto3.client and requests.get are mocked, matching test_tool_cli.py's
isolation style, and save_env_values is tested against a tmp_path .env
file via a monkeypatched find_dotenv, matching test_model_registry.py's
config_path fixture pattern.
"""

import requests
from botocore.exceptions import ClientError

import cto_cockpit_connectivity as m


class _FakeResponse:
    def __init__(self, status_code, json_data=None):
        self.status_code = status_code
        self._json_data = json_data or {}

    def json(self):
        return self._json_data


class TestSaveEnvValues:
    def test_writes_values_to_the_located_dotenv_file(self, monkeypatch, tmp_path):
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text("EXISTING=1\n")
        monkeypatch.setattr(m, "find_dotenv", lambda usecwd=True: str(dotenv_path))

        result = m.save_env_values({"ANTHROPIC_API_KEY": "sk-test-123"})

        assert result is True
        assert "ANTHROPIC_API_KEY='sk-test-123'" in dotenv_path.read_text()

    def test_mirrors_into_this_process_os_environ(self, monkeypatch, tmp_path):
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text("")
        monkeypatch.setattr(m, "find_dotenv", lambda usecwd=True: str(dotenv_path))

        m.save_env_values({"SERPER_API_KEY": "serper-key-abc"})

        assert m.os.environ["SERPER_API_KEY"] == "serper-key-abc"

    def test_skips_empty_values(self, monkeypatch, tmp_path):
        dotenv_path = tmp_path / ".env"
        dotenv_path.write_text("")
        monkeypatch.setattr(m, "find_dotenv", lambda usecwd=True: str(dotenv_path))

        m.save_env_values({"ANTHROPIC_API_KEY": "", "SERPER_API_KEY": "keep-me"})

        content = dotenv_path.read_text()
        assert "ANTHROPIC_API_KEY" not in content
        assert "keep-me" in content

    def test_returns_false_when_no_dotenv_file_found(self, monkeypatch):
        monkeypatch.setattr(m, "find_dotenv", lambda usecwd=True: "")

        assert m.save_env_values({"ANTHROPIC_API_KEY": "x"}) is False


class TestAwsConnection:
    def test_success_reports_the_account(self, monkeypatch):
        class FakeStsClient:
            def get_caller_identity(self):
                return {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/x"}

        monkeypatch.setattr(m.boto3, "client", lambda *a, **kw: FakeStsClient())

        ok, message = m.test_aws_connection("AKIA...", "secret", "eu-north-1")

        assert ok is True
        assert "123456789012" in message

    def test_bad_credentials_reports_failure_not_an_exception(self, monkeypatch):
        class FakeStsClient:
            def get_caller_identity(self):
                raise ClientError({"Error": {"Code": "InvalidClientTokenId", "Message": "bad"}}, "GetCallerIdentity")

        monkeypatch.setattr(m.boto3, "client", lambda *a, **kw: FakeStsClient())

        ok, message = m.test_aws_connection("bad", "bad", "eu-north-1")

        assert ok is False
        assert message


class TestGithubConnection:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get", lambda *a, **kw: _FakeResponse(200))

        ok, message = m.test_github_connection("ghp_x", "marcohauff1975/job-finder")

        assert ok is True

    def test_non_200_reports_failure(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get", lambda *a, **kw: _FakeResponse(404))

        ok, message = m.test_github_connection("ghp_x", "nope/nope")

        assert ok is False
        assert "404" in message

    def test_network_error_is_caught_not_raised(self, monkeypatch):
        def raise_exc(*a, **kw):
            raise requests.RequestException("timed out")

        monkeypatch.setattr(m.requests, "get", raise_exc)

        ok, message = m.test_github_connection("ghp_x", "marcohauff1975/job-finder")

        assert ok is False
        assert "timed out" in message


class TestAnthropicConnection:
    def test_success(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get", lambda *a, **kw: _FakeResponse(200))

        ok, _ = m.test_anthropic_connection("sk-ant-x")

        assert ok is True

    def test_invalid_key_reports_failure(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get", lambda *a, **kw: _FakeResponse(401))

        ok, message = m.test_anthropic_connection("bad-key")

        assert ok is False
        assert "401" in message


class TestGithubActionsBilling:
    def test_no_token_short_circuits(self):
        usage, reason = m.get_github_actions_billing("", "marcohauff1975")

        assert usage is None
        assert reason == "no_token"

    def test_success_returns_the_usage_payload(self, monkeypatch):
        payload = {"total_minutes_used": 42}
        monkeypatch.setattr(m.requests, "get", lambda *a, **kw: _FakeResponse(200, payload))

        usage, reason = m.get_github_actions_billing("ghp_x", "marcohauff1975")

        assert usage == payload
        assert reason is None

    def test_forbidden_is_a_distinct_reason(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get", lambda *a, **kw: _FakeResponse(403))

        usage, reason = m.get_github_actions_billing("ghp_x", "marcohauff1975")

        assert usage is None
        assert reason == "forbidden"

    def test_not_found_is_a_distinct_reason(self, monkeypatch):
        monkeypatch.setattr(m.requests, "get", lambda *a, **kw: _FakeResponse(404))

        usage, reason = m.get_github_actions_billing("ghp_x", "marcohauff1975")

        assert usage is None
        assert reason == "not_found"

    def test_network_error_is_caught_not_raised(self, monkeypatch):
        def raise_exc(*a, **kw):
            raise requests.RequestException("boom")

        monkeypatch.setattr(m.requests, "get", raise_exc)

        usage, reason = m.get_github_actions_billing("ghp_x", "marcohauff1975")

        assert usage is None
        assert reason == "error"
