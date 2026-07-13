"""
Admin UI for the CTO Cockpit product's own "CTO Cockpit" tab in
streamlit_app.py, split into three sub-tabs: Architecture (a live,
self-updating diagram of the actual repo's product boundaries),
Connectivity (editable AWS/GitHub/Anthropic/other-tool credentials -
see cto_cockpit_connectivity.py for the actual save/test logic), and
Cost (spend for AWS/GitHub/AI, built from those same connections).

"Live" here means: PRODUCT_PATHS below is a small, explicit, rarely-
touched map of which top-level repo paths belong to which product - NOT
automatic import-graph introspection (this repo has no diagramming/graph
library and one isn't worth adding just for this; an import graph also
risks surfacing accidental coupling instead of intended product
boundaries). Everything BELOW that top level is listed for real via
pathlib at render time, so the "one level deeper" drill-down is always
accurate for the common case (files added, removed, or moved within a
product) without ever touching this file - only a brand-new top-level
product or a repo reorg needs the map below edited by hand.
unclassified_top_level_paths() is the drift guard that keeps that "rarely
touched" promise honest: a pytest assertion fails the moment a new
top-level file/dir shows up unclassified, rather than it silently just
not appearing anywhere.

Follows the "product owns its own admin module" convention set by
jobfinder_admin.py / req2prod/admin_ui.py: data-building (pure,
filesystem-only, no network calls) is kept separate from SVG rendering
(pure, string-only) and both are separate from the Streamlit-facing
render function, matching req2prod_pr_flow.py's established split. The
Connectivity/Cost tabs make real external calls (boto3, requests), so
that logic lives in cto_cockpit_connectivity.py rather than here -
this file stays Streamlit rendering only for those two tabs.
"""

import os
from pathlib import Path

import streamlit as st

from cto_cockpit_connectivity import (
    ANTHROPIC_FIELDS,
    AWS_FIELDS,
    GITHUB_FIELDS,
    OTHER_TOOL_FIELDS,
    ConnectivityField,
    get_github_actions_billing,
    save_env_values,
    test_anthropic_connection,
    test_aws_connection,
    test_github_connection,
)
from reporting import get_serper_balance

REPO_ROOT = Path(__file__).parent

PRODUCTS: list[str] = ["Job Finder", "Req2Prod", "CTO Cockpit"]

# Top-level repo path -> product name. Anything NOT listed here and NOT
# in SHARED_INFRA_PATHS or EXCLUDED_NOISE below is unclassified - see
# unclassified_top_level_paths().
PRODUCT_PATHS: dict[str, str] = {
    # Dispatcher-heavy (wires together every product's admin tab) but
    # Job-Finder-dominant by actual line count (job search flow, quotas,
    # resume upload) - a defensible judgment call either way, not hidden.
    "streamlit_app.py": "Job Finder",
    "auth.py": "Job Finder",
    "job_search.py": "Job Finder",
    "jobfinder_admin.py": "Job Finder",
    "notify.py": "Job Finder",
    "reporting.py": "Job Finder",
    "ai_viewer.py": "Job Finder",
    "config": "Job Finder",  # Job Finder's own CrewAI config - distinct from req2prod/config/
    "assets": "Job Finder",
    "req2prod": "Req2Prod",
    "req2prod_agent_backend_mode.py": "Req2Prod",
    "req2prod_agent_steps.py": "Req2Prod",
    "req2prod_deploy_mode.py": "Req2Prod",
    "req2prod_pr_flow.py": "Req2Prod",
    "cto_cockpit_admin.py": "CTO Cockpit",
    "cto_cockpit_connectivity.py": "CTO Cockpit",
}

SHARED_INFRA_PATHS: list[str] = [
    "infra",
    ".github/workflows",
    "tests",
    ".streamlit/config.toml",
    "requirements.txt",
    "pytest.ini",
]

EXCLUDED_NOISE: set[str] = {
    "venv",
    "__pycache__",
    ".pytest_cache",
    "data",
    "users",
    ".vscode",
    ".git",
    ".env",
    ".env.example",
    ".gitignore",
    "README.md",
    "LICENSE",
    "run_job_finder.command",
    ".claude",
}

PRODUCT_COLORS: dict[str, str] = {
    "Job Finder": "#8b5cf6",  # violet - matches primaryColor in .streamlit/config.toml
    "Req2Prod": "#22d3ee",  # cyan
    "CTO Cockpit": "#f59e0b",  # amber - also signals "not yet built" via dashed border
}


def _path_count_label(count: int) -> str:
    return f"{count} path" if count == 1 else f"{count} paths"


def unclassified_top_level_paths(repo_root: Path = REPO_ROOT) -> list[str]:
    """Real repo-root entries not accounted for by PRODUCT_PATHS,
    SHARED_INFRA_PATHS, or EXCLUDED_NOISE. Should always be empty -
    covered by a pytest assertion so a new top-level file/dir forces a
    deliberate classification decision instead of silently not
    appearing anywhere in the live diagram."""
    known = set(PRODUCT_PATHS) | {p.split("/")[0] for p in SHARED_INFRA_PATHS} | EXCLUDED_NOISE
    return sorted(entry.name for entry in repo_root.iterdir() if entry.name not in known)


_NOISE_CHILD_NAMES = {"__pycache__", ".pytest_cache"}


def _list_one_level_deeper(repo_root: Path, top_level_path: str) -> list[str]:
    """For a file entry: just itself. For a directory entry: its
    immediate children (files and subdirs, subdirs NOT walked further -
    "one level deeper" than the product itself, not a full recursive
    tree), skipping generated bytecode-cache dirs wherever they show up
    - EXCLUDED_NOISE only filters top-level entries, but __pycache__/
    appears nested inside req2prod/, tests/, etc. too and is never
    meaningful architecture. Missing paths return []."""
    full = repo_root / top_level_path
    if not full.exists():
        return []
    if full.is_file():
        return [top_level_path]
    return sorted(
        f"{top_level_path}/{child.name}{'/' if child.is_dir() else ''}"
        for child in full.iterdir()
        if child.name not in _NOISE_CHILD_NAMES
    )


def build_product_tree(repo_root: Path = REPO_ROOT) -> dict[str, list[str]]:
    """For each product, every real path one level deeper than its
    declared top-level ownership. This is what makes the drill-down
    genuinely live: add/remove/move a file inside an owned path and this
    reflects it on the very next render, no code change needed."""
    tree: dict[str, list[str]] = {product: [] for product in PRODUCTS}
    for top_level_path, product in PRODUCT_PATHS.items():
        tree[product].extend(_list_one_level_deeper(repo_root, top_level_path))
    return {product: sorted(paths) for product, paths in tree.items()}


def build_shared_infra_tree(repo_root: Path = REPO_ROOT) -> list[str]:
    paths: list[str] = []
    for top_level_path in SHARED_INFRA_PATHS:
        paths.extend(_list_one_level_deeper(repo_root, top_level_path))
    return sorted(paths)


def render_architecture_svg(products: list[str] = PRODUCTS, path_counts: dict[str, int] | None = None) -> str:
    """Level-1 diagram: one box per product (with its live path count,
    so even this stable top-level view visibly reflects real change) plus
    a shared foundation box below - the live equivalent of the earlier
    one-off hand-drawn chat diagram. Pure string-building, no Streamlit
    calls, same palette/pattern as req2prod_pr_flow.py's
    render_pr_flow_svg. Deliberately static in shape (box count/labels) -
    the real "live" data is the drill-down below, not this box layout;
    architecture boundaries should look stable, not churn every render."""
    path_counts = path_counts or {}
    box_w, box_h, gap, top = 200, 90, 40, 30
    n = len(products)
    width = n * box_w + (n - 1) * gap + 40
    foundation_top = top + box_h + 50
    foundation_h = 60
    height = foundation_top + foundation_h + 30

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'font-family="Inter, sans-serif">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#0a0a0f"/>',
    ]

    x = 20
    for product in products:
        color = PRODUCT_COLORS.get(product, "#8b5cf6")
        not_built = product == "CTO Cockpit"
        dash = ' stroke-dasharray="6,4"' if not_built else ""
        parts.append(
            f'<rect x="{x}" y="{top}" width="{box_w}" height="{box_h}" rx="12" '
            f'fill="{color}" fill-opacity="0.14" stroke="{color}" stroke-width="2"{dash}/>'
        )
        count = path_counts.get(product)
        label = f"{product} ({_path_count_label(count)})" if count is not None else product
        parts.append(
            f'<text x="{x + box_w / 2}" y="{top + box_h / 2 - 4}" fill="#f1f5f9" '
            f'font-size="14" font-weight="700" text-anchor="middle">{label}</text>'
        )
        if not_built:
            parts.append(
                f'<text x="{x + box_w / 2}" y="{top + box_h / 2 + 16}" fill="{color}" '
                f'font-size="11" text-anchor="middle">not yet built</text>'
            )
        parts.append(
            f'<line x1="{x + box_w / 2}" y1="{top + box_h}" '
            f'x2="{x + box_w / 2}" y2="{foundation_top}" stroke="#94a3b8" stroke-width="2"/>'
        )
        x += box_w + gap

    parts.append(
        f'<rect x="20" y="{foundation_top}" width="{width - 40}" height="{foundation_h}" '
        f'rx="10" fill="#94a3b8" fill-opacity="0.10" stroke="#94a3b8" stroke-width="1.5"/>'
    )
    parts.append(
        f'<text x="{width / 2}" y="{foundation_top + foundation_h / 2 + 5}" fill="#94a3b8" '
        f'font-size="13" text-anchor="middle">Infra as code (shared foundation)</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def render_architecture_tab() -> None:
    """The "Architecture" sub-tab: live level-1 architecture diagram
    plus a level-2 drill-down per product via st.expander (native
    Streamlit widget, matching this app's existing convention rather
    than custom JS interactivity)."""
    st.markdown("### 🏗️ Live system architecture")
    st.caption(
        "Generated from this file's PRODUCT_PATHS map plus a real "
        "directory listing at render time - always reflects the "
        "current repo. Only needs a manual edit here when a brand-new "
        "top-level product or file is added."
    )

    tree = build_product_tree()
    shared = build_shared_infra_tree()
    path_counts = {product: len(paths) for product, paths in tree.items()}

    st.markdown(render_architecture_svg(path_counts=path_counts), unsafe_allow_html=True)

    st.markdown("#### One level deeper")
    for product in PRODUCTS:
        paths = tree[product]
        with st.expander(f"{product} ({_path_count_label(len(paths))})"):
            st.code("\n".join(paths) if paths else "No files yet.", language="text")

    with st.expander(f"Shared / foundation ({_path_count_label(len(shared))})"):
        st.code("\n".join(shared) if shared else "No files yet.", language="text")

    unclassified = unclassified_top_level_paths()
    if unclassified:
        st.caption(
            "⚠️ Not shown in the diagram above - classify in "
            "cto_cockpit_admin.py's PRODUCT_PATHS/SHARED_INFRA_PATHS/"
            f"EXCLUDED_NOISE: {', '.join(unclassified)}"
        )


DEFAULT_GITHUB_REPO = "marcohauff1975/job-finder"


def _secret_hint(current_value: str) -> str:
    """'Currently set (ends ...ab12)' / 'Not set' - never puts the
    real secret value on the page, just enough to confirm something's
    there without re-exposing it."""
    if not current_value:
        return "Not set"
    if len(current_value) <= 4:
        return "Currently set"
    return f"Currently set (ends ...{current_value[-4:]})"


def _render_credential_section(
    title: str,
    fields: list[ConnectivityField],
    test_fn,
    key_prefix: str,
    link: str | None = None,
) -> None:
    """One Connectivity section (AWS/GitHub/Anthropic/each other tool).
    Non-secret fields pre-fill from os.getenv via
    st.text_input(value=...); secret fields render blank with a
    "Currently set (ends ...)" caption instead of pre-filling the real
    value into the page - type="password" only masks display, the
    value is still inspectable via dev tools, and there's no reason to
    re-expose an already-configured secret on every render. An empty
    secret submission means "leave unchanged", not "clear it".
    test_fn takes {env_var: current-or-typed value} and returns
    (bool, message) - a per-section closure, since AWS/GitHub/
    Anthropic/Serper each validate differently."""
    if title:
        st.markdown(f"#### {title}")
    if link:
        st.caption(f"[Open {title} →]({link})")

    typed_values: dict[str, str] = {}
    for field in fields:
        current = os.getenv(
            field.env_var,
            DEFAULT_GITHUB_REPO if field.env_var == "GITHUB_REPO" else "",
        )
        if field.secret:
            st.caption(_secret_hint(current))
            typed_values[field.env_var] = st.text_input(
                field.label,
                type="password",
                key=f"{key_prefix}_{field.env_var}",
                help=field.help or None,
                placeholder="Leave blank to keep the current value",
            )
        else:
            typed_values[field.env_var] = st.text_input(
                field.label,
                value=current,
                key=f"{key_prefix}_{field.env_var}",
                help=field.help or None,
            )

    test_col, save_col = st.columns(2)
    with test_col:
        if st.button("Test connection", key=f"{key_prefix}_test"):
            effective_values = {
                field.env_var: typed_values[field.env_var] or os.getenv(field.env_var, "")
                for field in fields
            }
            ok, message = test_fn(effective_values)
            (st.success if ok else st.error)(message)
    with save_col:
        if st.button("Save", key=f"{key_prefix}_save"):
            to_save = {key: value for key, value in typed_values.items() if value}
            if not to_save:
                st.info("Nothing to save - all fields left blank.")
            elif save_env_values(to_save):
                st.success("Saved. Restart the app for this to take effect.")
            else:
                st.error("Couldn't find a .env file to save to.")


def _test_serper(_values: dict[str, str]) -> tuple[bool, str]:
    """Unlike the other three sections, this validates the currently
    SAVED SERPER_API_KEY (via reporting.get_serper_balance(), which
    reads os.getenv directly) rather than whatever's typed but not yet
    saved - reusing that existing health-check as-is rather than
    writing a parameterized variant just for one field. Surfaced to
    the user via the caption in render_connectivity_tab, not hidden."""
    balance = get_serper_balance()
    if balance is None:
        return False, "Couldn't reach Serper - this tests the saved key, so save first."
    return True, f"Connected - {balance} credits remaining"


def render_connectivity_tab() -> None:
    """AWS / GitHub / Anthropic / Serper sections, each backed by
    cto_cockpit_connectivity's field specs and test_*_connection
    functions. This is what lets CTO Cockpit eventually ship as a
    product: a customer hooks up their own AWS/GitHub/Anthropic access
    from this page instead of needing SSH access to hand-edit .env."""
    st.caption(
        "Saves to this app's own .env file - the same single source "
        "of truth every other credential in this app already reads "
        "from. Changes to the Anthropic key need a restart to take "
        "effect (CrewAI's agents are built once, at process start)."
    )

    _render_credential_section(
        "AWS",
        AWS_FIELDS,
        lambda v: test_aws_connection(
            v["AWS_ACCESS_KEY_ID"], v["AWS_SECRET_ACCESS_KEY"], v["AWS_REGION"]
        ),
        "aws",
        link="https://console.aws.amazon.com/",
    )

    st.divider()

    github_repo = os.getenv("GITHUB_REPO", DEFAULT_GITHUB_REPO)
    _render_credential_section(
        "GitHub",
        GITHUB_FIELDS,
        lambda v: test_github_connection(v["GITHUB_VARIABLES_TOKEN"], v["GITHUB_REPO"]),
        "github",
        link=f"https://github.com/{github_repo}",
    )

    st.divider()

    st.markdown("#### Claude / Anthropic")
    st.caption(
        "Only the API key is configurable here - Subscription-mode "
        "billing (AGENT_BACKEND=subscription) is separate "
        "infrastructure that needs an actual `claude login` terminal "
        "session on whichever machine runs the self-hosted GitHub "
        "Actions runner, which a web form can't do. A shipped "
        "deployment would realistically run API-backend mode only."
    )
    _render_credential_section(
        "",
        ANTHROPIC_FIELDS,
        lambda v: test_anthropic_connection(v["ANTHROPIC_API_KEY"]),
        "anthropic",
    )

    for tool_name, fields in OTHER_TOOL_FIELDS.items():
        st.divider()
        _render_credential_section(tool_name, fields, _test_serper, "serper")


def render_cost_tab() -> None:
    """AWS stays an honest placeholder (needs a new IAM permission not
    yet granted). GitHub is gated behind a "Check GitHub usage" button
    rather than firing on every render - Streamlit runs every tab's
    body on every rerun regardless of which tab is on-screen (see
    req2prod/admin_ui.py's render_requirements_tab docstring), so an
    unconditional live network call here would fire even while the
    user is looking at a completely different admin tab. Gracefully
    degrades if the call fails - genuinely untested against a personal
    (non-org) account. AI reuses the same manual dashboard link already
    shown on the AI Models tab (req2prod/admin_ui.py's
    render_ai_models_tab) - duplicated here for one unified cost view,
    not removed there."""
    st.caption("Spend across the services Connectivity is hooked up to.")

    st.markdown("#### AWS")
    st.info(
        "Not yet connected - reading real AWS spend needs the "
        "ce:GetCostAndUsage permission, which isn't in this app's "
        "current scoped IAM policy. Ask to add it via Terraform "
        "(crewai-infra) when you're ready."
    )

    st.markdown("#### GitHub")
    if st.button("Check GitHub usage", key="cost_check_github"):
        token = os.getenv("GITHUB_VARIABLES_TOKEN", "")
        repo = os.getenv("GITHUB_REPO", DEFAULT_GITHUB_REPO)
        owner = repo.split("/")[0] if "/" in repo else repo
        usage, reason = get_github_actions_billing(token, owner)
        if usage is not None:
            st.json(usage)
        elif reason == "no_token":
            st.caption("Not yet connected - add a GitHub token on the Connectivity tab.")
        else:
            st.caption(
                "Not available for this account - GitHub's Actions billing "
                "API may only be exposed for organizations, not personal "
                "accounts like this repo's owner (unverified)."
            )

    st.markdown("#### AI (Anthropic)")
    st.caption(
        "[Top up Anthropic credits / check real balance](https://platform.claude.com/dashboard)"
    )
