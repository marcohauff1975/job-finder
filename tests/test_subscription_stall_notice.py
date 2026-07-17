"""
Telling the user their Mac is asleep, instead of showing them nothing.

AGENT_BACKEND=subscription routes code_review and deploy onto the self-hosted
runner - Marco's laptop. GitHub cannot push work to it: the runner holds an
outbound connection and asks for jobs (verified on the machine: Runner.Listener
holds 192.168.1.123:65170 -> 20.85.130.105:443 ESTABLISHED and listens on no
port at all). A closed laptop means nothing is asking, so the job sits queued
and resumes by itself when the Mac returns.

Benign - but invisible. The Pipeline tab builds its flow from *posted PR
reviews*, so a queued review produces no Code Review box: the page shows the
pull request and then stops. That reads as finished, or broken. Neither is
true.

The notice must be conservative. It claims a specific machine is down, and a
false alarm ("your Mac is offline" when really a token expired) would send
someone to check a laptop that is fine. So every uncertain path returns None
and says nothing.
"""

import pytest

import req2prod_pr_flow as flow


@pytest.fixture
def gh(monkeypatch):
    """Stubs the GitHub layer. calls records what was asked for."""
    state = {
        "backend": "subscription",
        "runners": {"runners": [{"name": "Marcos-MacBook-Pro", "status": "offline"}]},
        "runs": {"total_count": 2},
        "ok": True,
        "calls": [],
    }
    monkeypatch.setenv("GITHUB_ACTIONS_TOKEN", "t")
    monkeypatch.setattr(flow, "get_agent_backend", lambda: state["backend"])

    def fake_get(path, token, params=None):
        state["calls"].append(path)
        if not state["ok"]:
            return None, False
        return (state["runners"] if "runners" in path else state["runs"]), True

    monkeypatch.setattr(flow, "_gh_get", fake_get)
    return state


def _stall():
    return flow.get_subscription_stall.__wrapped__()


class TestItFiresWhenTheMacIsActuallyDown:
    def test_offline_runner_on_subscription_is_reported(self, gh):
        assert _stall() == {"runner": "Marcos-MacBook-Pro", "queued": 2}

    def test_it_names_the_machine_so_the_message_can_too(self, gh):
        assert _stall()["runner"] == "Marcos-MacBook-Pro"

    def test_a_stall_with_nothing_queued_still_reports(self, gh):
        """Worth saying before a requirement is pushed, not only after one is
        stuck behind it."""
        gh["runs"] = {"total_count": 0}
        assert _stall() == {"runner": "Marcos-MacBook-Pro", "queued": 0}


class TestItStaysQuietWhenItShould:
    def test_online_runner_says_nothing(self, gh):
        gh["runners"] = {"runners": [{"name": "Marcos-MacBook-Pro", "status": "online"}]}
        assert _stall() is None

    def test_one_online_runner_among_several_is_enough(self, gh):
        gh["runners"] = {
            "runners": [
                {"name": "old-laptop", "status": "offline"},
                {"name": "Marcos-MacBook-Pro", "status": "online"},
            ]
        }
        assert _stall() is None

    def test_api_backend_says_nothing_even_with_the_mac_off(self, gh):
        """On API the jobs run on ubuntu-latest. The Mac being asleep is then
        completely irrelevant, and mentioning it would be noise."""
        gh["backend"] = "api"
        assert _stall() is None

    def test_it_does_not_even_ask_github_when_on_api(self, gh):
        gh["backend"] = "api"
        _stall()
        assert gh["calls"] == [], "hit the API to answer a question it already knew"


class TestUncertaintyIsNotAnAlarm:
    """Every one of these could otherwise send someone to check a laptop that
    is running perfectly well."""

    def test_unknown_backend_says_nothing(self, gh):
        gh["backend"] = None
        assert _stall() is None

    def test_missing_token_says_nothing(self, gh, monkeypatch):
        monkeypatch.delenv("GITHUB_ACTIONS_TOKEN", raising=False)
        assert _stall() is None

    def test_unreachable_api_says_nothing(self, gh):
        gh["ok"] = False
        assert _stall() is None

    def test_no_runner_registered_at_all_does_not_crash(self, gh):
        gh["runners"] = {"runners": []}
        assert _stall() == {"runner": None, "queued": 2}


class TestTheBannerOnThePipelineTab:
    """The flow is drawn from posted reviews, so a queued job leaves no box to
    annotate - the notice has to sit above the flow, and has to render even
    when there is nothing else to show."""

    @pytest.fixture
    def tab(self, monkeypatch):
        import req2prod.admin_ui as ui

        seen = {"info": [], "warning": [], "markdown": []}
        monkeypatch.setattr(ui.st, "info", lambda msg, **kw: seen["info"].append(msg))
        monkeypatch.setattr(ui.st, "warning", lambda msg, **kw: seen["warning"].append(msg))
        monkeypatch.setattr(ui.st, "markdown", lambda msg, **kw: seen["markdown"].append(msg))
        monkeypatch.setattr(ui, "get_latest_pr_flow", lambda: (None, [], None))
        return ui, seen

    def test_it_says_the_machine_is_offline_and_will_resume_itself(self, tab, monkeypatch):
        ui, seen = tab
        monkeypatch.setattr(
            ui, "get_subscription_stall", lambda: {"runner": "Marcos-MacBook-Pro", "queued": 1}
        )

        ui._draw_pr_flow()

        banner = " ".join(seen["info"])
        assert "Marcos-MacBook-Pro is offline" in banner
        assert "resumes automatically" in banner
        assert "1 job" in banner

    def test_nothing_is_shown_when_the_runner_is_up(self, tab, monkeypatch):
        ui, seen = tab
        monkeypatch.setattr(ui, "get_subscription_stall", lambda: None)

        ui._draw_pr_flow()

        assert not any("offline" in m for m in seen["info"])

    def test_a_failing_stall_check_never_breaks_the_tab(self, tab, monkeypatch):
        """The flow is the point of the page; a banner is a nicety. It must not
        be able to take the tab down."""
        ui, _ = tab

        def boom():
            raise RuntimeError("github down")

        monkeypatch.setattr(ui, "get_subscription_stall", boom)

        ui._draw_pr_flow()
