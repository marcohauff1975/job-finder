"""
Which services a deploy actually needs to restart, given what changed.

The public Job Finder (jobfinder.service, streamlit_app.py) and the admin
console (req2prod.service, req2prod_app.py) are two Streamlit processes out of
one checkout. Restarting either one drops every session on it, so restarting
both for every change means shipping a Job Finder tweak kills the SDLC view
someone is watching that very deploy through - the problem the split exists to
fix. Splitting the processes only helps if the deploy stops bouncing both.

Deliberately NOT reusing cto_cockpit_admin.PRODUCT_PATHS, which already maps
top-level paths to products and looks like the obvious single source of truth.
It answers a different question: *which product owns this file*, for the
architecture diagram. This needs *which process imports this file*.
jobfinder_admin.py is Job Finder's product but renders inside the console, so a
product-based rule would restart the public app and leave the console running
stale code - exactly backwards, and silently.

Anything not on a list restarts both, which is what happens today, so an
unclassified file is never worse than the status quo - only a missed
optimisation. Non-code (site/, docs/, tests fixtures, .github/) restarts
nothing, which is also today's behaviour: the old rule keyed on `\\.py$`.

Usage (the deploy pipes `git diff --name-only` in):
    git diff --name-only A B | python3 -m req2prod.deploy_targets
Prints the service names, space separated, or nothing at all.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

JOBFINDER = "jobfinder"
REQ2PROD = "req2prod"

# Imported only by req2prod_app.py's process.
_ADMIN_ONLY = (
    "req2prod/",  # the package
    "req2prod_",  # req2prod_app.py, req2prod_pr_flow.py, req2prod_deploy_mode.py, ...
    "cto_cockpit_",  # cto_cockpit_admin.py, cto_cockpit_connectivity.py
    "jobfinder_admin.py",  # Job Finder's product, but it renders in the console
)

# Imported only by streamlit_app.py's process.
_PUBLIC_ONLY = (
    "streamlit_app.py",
    "job_search.py",
    "ai_viewer.py",
    "config/",  # Job Finder's own CrewAI config, distinct from req2prod/config/
    "assets/",
)


def _is_admin_only(path: str) -> bool:
    return path.startswith(_ADMIN_ONLY)


def _is_public_only(path: str) -> bool:
    return path.startswith(_PUBLIC_ONLY)


def _is_live_code(path: str) -> bool:
    """Could this file change what a running process executes? Only Python and
    the dependency list can; a page under site/ or a note under docs/ is
    published or read, never imported."""
    return path.endswith(".py") or path == "requirements.txt"


def services_to_restart(changed_paths: Iterable[str]) -> set[str]:
    """The services that must restart for these changes to take effect.

    Empty means nothing needs restarting. Both means either something shared
    changed (auth.py, reporting.py, requirements.txt), or something nobody has
    classified did - and an unclassified file gets the safe answer, not a
    guess.
    """
    services: set[str] = set()
    for raw in changed_paths:
        path = raw.strip()
        if not path:
            continue
        if _is_admin_only(path):
            services.add(REQ2PROD)
        elif _is_public_only(path):
            services.add(JOBFINDER)
        elif _is_live_code(path):
            services.update((JOBFINDER, REQ2PROD))
    return services


def main() -> int:
    print(" ".join(sorted(services_to_restart(sys.stdin))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
