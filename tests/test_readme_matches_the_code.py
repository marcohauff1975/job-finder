"""
The README is the first thing anyone reads, and this repo is public.

It had drifted into claiming things that were not true:

- "15 named CrewAI agents" when the registry held 18.
- local_tester "boots the app, drives the actual changed flow in a browser" and
  ux_reviewer "checks the rendered UI" - neither runs. Nothing calls
  test_locally(), test_performance() or review_ux(); the only mentions of those
  names in the codebase are comments saying so.
- "streamlit run streamlit_app.py" as *the* way to run it, months after the
  admin console became its own process (req2prod_app.py). Following the README
  gives you the public app and no Req2Prod at all - the half worth looking at.

None of that was written dishonestly. It was true, and then the code moved and
the prose didn't. Same shape as the demo instruction's dead landmark and the
site's model table: a second copy of a fact, with nothing checking it still
agrees.

These tests are that check. They deliberately pin only claims that can be
derived from the code - a count, an entry point, a caller - not prose style.
"""

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_README = _ROOT / "README.md"


@pytest.fixture(scope="module")
def readme() -> str:
    return _README.read_text()


class TestTheAgentCountIsReal:
    def test_the_number_it_claims_matches_the_registry(self, readme):
        registry = json.loads((_ROOT / "req2prod" / "config" / "agent_models.default.json").read_text())
        actual = len([k for k, v in registry.items() if isinstance(v, dict)])

        m = re.search(r"staffed by (\d+) named CrewAI agents", readme)
        assert m, "the README no longer states an agent count in the expected form"
        assert int(m.group(1)) == actual, (
            f"README claims {m.group(1)} agents; the registry has {actual}. "
            "Adding an agent means updating the README too."
        )


class TestItDoesNotClaimUnwiredAgentsRun:
    """local_tester and ux_reviewer are written and configured but nothing
    invokes them. Describing them as if they run on every PR overstates the
    pipeline in the one place people check first."""

    @pytest.mark.parametrize(
        "agent,entry_points",
        [
            ("local_tester", ("test_locally", "test_performance")),
            ("ux_reviewer", ("review_ux",)),
        ],
    )
    def test_an_unwired_agent_is_marked_as_such(self, agent, entry_points, readme):
        wired = _has_a_real_caller(entry_points)
        row = _table_row(readme, agent)
        assert row, f"{agent} is no longer listed in the README's agent table"

        if wired:
            pytest.skip(f"{agent} is wired up now - the README may describe what it does")
        assert "not yet wired up" in row, (
            f"{agent} has no caller in the codebase, but the README describes it as "
            f"working. Either wire it up or say it isn't: {row[:90]}"
        )


class TestTheRunInstructionsGiveYouBothApps:
    """The split (PR #91) made the console its own process. A README that only
    names streamlit_app.py hands the reader half a system and hides Req2Prod."""

    def test_it_names_the_public_app(self, readme):
        assert "streamlit run streamlit_app.py" in readme

    def test_it_names_the_admin_console_too(self, readme):
        assert "req2prod_app.py" in readme, (
            "the README does not mention req2prod_app.py - following it gives you "
            "the Job Finder app and no Req2Prod console at all."
        )

    def test_both_entry_points_actually_exist(self, readme):
        for name in ("streamlit_app.py", "req2prod_app.py"):
            assert (_ROOT / name).is_file(), f"README names {name}, which does not exist"


class TestItPointsAtTheSite:
    def test_it_links_req2prod_nl(self, readme):
        assert "req2prod.nl" in readme

    @pytest.mark.parametrize("page", ["details.html", "demo.html"])
    def test_every_site_page_it_links_is_published(self, page, readme):
        """The site tree is the URL structure (infra/README-site.md: "the path in
        this repo is the path on the web"), so a link to a page that isn't in
        site/ is a 404 the moment someone clicks it."""
        if f"req2prod.nl/{page}" not in readme:
            pytest.skip(f"README does not link {page}")
        assert (_ROOT / "site" / page).is_file(), (
            f"README links req2prod.nl/{page} but site/{page} does not exist"
        )


def _has_a_real_caller(entry_points: tuple[str, ...]) -> bool:
    """Does anything actually call one of these? Comments don't count - the only
    mentions of test_locally() and review_ux() today are notes saying they are
    unwired, and a naive grep reads those as callers."""
    for path in _ROOT.glob("**/*.py"):
        if "venv" in path.parts or "tests" in path.parts:
            continue
        for line in path.read_text().splitlines():
            code = line.split("#", 1)[0]
            if any(f"{name}(" in code for name in entry_points) and "def " not in code:
                return True
    return False


def _table_row(readme: str, agent: str) -> str | None:
    for line in readme.splitlines():
        if line.startswith("|") and f"`{agent}`" in line:
            return line
    return None
