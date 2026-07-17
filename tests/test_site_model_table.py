"""
The model table on req2prod.nl has to match the models the code actually uses.

site/index.html hardcodes a per-agent model table. The real assignment lives in
req2prod/config/agent_models.json - which is *runtime state*: gitignored,
edited live from the admin console's "AI Models" tab, and deliberately
untracked so a deploy never clobbers it (see the deploy's
`git rm --cached --ignore-unmatch`). A static page cannot follow a file that
changes at runtime, so the table is a second copy of a fact the registry owns,
and nothing was checking the two still agreed.

They had already stopped. On 2026-07-17 the page claimed `claude-sonnet-5` for
product_manager, software_architect, software_engineer, prod_tester and
devops_agent - five of its nine rows - while the registry assigned
`claude-haiku-4-5` to all five. The page had copied the admin table's
*Recommended* column and presented it as the assignment, under a caption that
read "Real assignments from the registry". On an investor-facing page that
claims a more expensive model than actually runs.

This pins the table to the shipped defaults (agent_models.default.json), which
is the strongest claim a static page can honestly make: it cannot know what a
running box has been edited to, and the caption now says so.

Same shape as tests/test_demo_logo_instructions.py and backend.py's
TOOL_CLI_COMMAND - two literals that agree until they don't, with a test as the
only thing that notices.
"""

import json
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent.parent
_SITE = _ROOT / "site" / "index.html"
_DEFAULTS = _ROOT / "req2prod" / "config" / "agent_models.default.json"

# The page writes friendly ids ("claude-haiku-4-5"); the registry writes real
# ones ("anthropic/claude-haiku-4-5-20251001"). Normalising here rather than
# forcing dated ids into the page keeps the marketing copy readable while still
# pinning it to the same fact.
_DATE_SUFFIX = re.compile(r"-\d{8}$")


def _normalise(model_id: str) -> str:
    return _DATE_SUFFIX.sub("", model_id.split("/", 1)[-1])


# The site's row label -> the registry's agent key. The site labels the first
# three in prose ("Product Manager") and the rest by their code names.
_ROW_TO_AGENT = {
    "Product Manager": "product_manager",
    "Software Architect": "software_architect",
    "Software Engineer": "software_engineer",
    "code_reviewer": "code_reviewer",
    "pr_fix_agent": "pr_fix_agent",
    "pr_arbiter": "pr_arbiter",
    "prod_tester": "prod_tester",
    "devops_agent": "devops_agent",
    "rollback_agent": "rollback_agent",
}

_ROW = re.compile(
    r'<td class="agc">([^<]+)<small>.*?<span class="mbadge[^"]*">([^<]+)</span>',
    re.S,
)


@pytest.fixture(scope="module")
def site_rows() -> dict[str, str]:
    rows = dict(_ROW.findall(_SITE.read_text()))
    assert rows, "no model rows parsed from site/index.html - has the markup changed?"
    return rows


@pytest.fixture(scope="module")
def defaults() -> dict:
    return json.loads(_DEFAULTS.read_text())


class TestEveryRowMatchesTheRegistry:
    def test_the_rows_parsed_are_the_ones_we_expect(self, site_rows):
        """Guards the test itself: a markup change that silently stopped
        matching rows would make every assertion below vacuously pass."""
        assert set(site_rows) == set(_ROW_TO_AGENT), (
            "the agents listed on site/index.html no longer match this test's map. "
            "If a row was added or renamed, update _ROW_TO_AGENT."
        )

    @pytest.mark.parametrize("label,agent_key", sorted(_ROW_TO_AGENT.items()))
    def test_row_matches_shipped_default(self, label, agent_key, site_rows, defaults):
        claimed = site_rows[label]
        actual = _normalise(defaults[agent_key]["api"])
        assert claimed == actual, (
            f"site/index.html says {label} runs {claimed!r}, but "
            f"agent_models.default.json assigns {actual!r}. The public page is "
            "claiming a model the code does not use - fix the page (or the "
            "registry), not this test."
        )


class TestTheCaptionDoesNotOverclaim:
    """The numbers being right today is not enough: the registry is editable at
    runtime, so the page must not assert them as fixed truth."""

    @pytest.fixture(scope="class")
    def note(self):
        m = re.search(r'<div class="note">(.*?)</div>', _SITE.read_text(), re.S)
        assert m
        return m.group(1)

    def test_it_does_not_claim_these_are_the_live_assignments(self, note):
        assert "Real assignments from the registry" not in note

    def test_it_says_they_are_defaults_and_can_change(self, note):
        assert "shipped defaults" in note
        assert "runtime" in note

    def test_the_registry_path_it_quotes_exists(self):
        m = re.search(r'<span class="mono">([^<]*agent_models\.json)</span>', _SITE.read_text())
        assert m, "the page no longer names the registry file"
        assert (_ROOT / m.group(1)).parent.is_dir(), (
            f"site/index.html points at {m.group(1)!r}, which is not where the "
            "registry lives. It said 'config/agent_models.json' for a while after "
            "the sdlc/ -> req2prod/ move."
        )
