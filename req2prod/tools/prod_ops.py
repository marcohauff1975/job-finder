"""
SSH/curl-backed tools that let prod_tester and rollback_agent actually
act on the production Lightsail instance, instead of only reasoning
over text passed to them - closes the gap noted in req2prod/Req2Prod.py for
these two agents (ux_reviewer already has an equivalent for the app's
front end, via tools/ux_inspector.py).

Neither tool fetches or handles AWS/SSH credentials itself - both
expect a short-lived SSH private key to already exist on disk at
key_path, fetched by whatever is driving the crew (see
.github/workflows/req2prod-pipeline.yml). This keeps credential handling
entirely in the calling workflow, not in agent-callable code.
"""

import json
import subprocess
import time

from crewai.tools import BaseTool
from playwright.sync_api import sync_playwright

SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]

# Real text Streamlit renders into the page itself when a script
# raises an uncaught exception (e.g. a missing dependency) - the
# 2026-07-08 outage where a new requirements.txt entry was never
# installed on the server shipped a ModuleNotFoundError on every
# request, but every HTTP-status check here still saw a plain 200:
# Streamlit's websocket-delivered app content, including an in-page
# error render, arrives inside a normal 200 response - the HTTP layer
# has no idea the script itself crashed. Checking the actually-rendered
# page text (via a real headless browser, not curl) is the only way to
# catch this class of failure.
PAGE_ERROR_MARKERS = [
    "ModuleNotFoundError",
    "ImportError",
    "Traceback (most recent call last)",
    "This app has encountered an error",
]


def _check_page_renders_cleanly(url: str, timeout_ms: int = 20000) -> dict:
    """Loads url in headless Chromium and waits for the real,
    websocket-rendered content (not just the initial HTTP response) -
    returns whether it actually shows the app or an in-page exception.
    No login/test account needed: a script-level crash (like a missing
    import) happens before any auth logic runs, so it breaks even the
    unauthenticated landing page.

    Deliberately keeps two different kinds of "didn't get a clean
    result" apart: page_renders_cleanly is only ever True/False when
    the check actually ran to completion (browser launched, page
    loaded, text inspected) - if the check itself couldn't run at all
    (e.g. Playwright's browser binary isn't installed on this machine,
    a bug that once caused a real deploy to falsely roll back), that's
    page_check_error instead, and page_renders_cleanly stays None. A
    tool-execution failure says nothing about whether the app is
    actually broken - callers must not treat it as a confirmed
    page-content failure."""
    result = {"page_renders_cleanly": None, "page_error_snippet": None, "page_check_error": None}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                body_text = page.inner_text("body")
                marker = next((m for m in PAGE_ERROR_MARKERS if m in body_text), None)
                result["page_renders_cleanly"] = marker is None
                if marker:
                    idx = body_text.find(marker)
                    result["page_error_snippet"] = body_text[max(0, idx - 50) : idx + 250]
            finally:
                browser.close()
    except Exception as e:
        result["page_check_error"] = f"{type(e).__name__}: {e}"
    return result


def _ssh_run(key_path: str, remote_user: str, instance_ip: str, command: str, timeout: int = 30) -> tuple[int, str, str]:
    """Runs a single command on the remote host over SSH, returning
    (exit_code, stdout, stderr). Never raises on a non-zero remote
    exit code - callers decide what that means."""
    result = subprocess.run(
        ["ssh", *SSH_OPTS, "-i", key_path, f"{remote_user}@{instance_ip}", command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _curl_status(url: str, timeout: int = 30) -> str:
    result = subprocess.run(
        ["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", url],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return result.stdout.strip()


class ProdHealthCheckTool(BaseTool):
    name: str = "check_production_health"
    description: str = (
        "Checks the real, live production instance: whether a real user "
        "session currently looks active (open connections on the app "
        "port), the HTTP status of the app on the server itself, the "
        "HTTP status of the public domain, and - critically - whether "
        "the public domain's page actually RENDERS THE REAL APP rather "
        "than an in-page exception ('page_renders_cleanly' / "
        "'page_error_snippet'). A 200 status code on its own does NOT "
        "prove the app works: Streamlit delivers its real content over "
        "a websocket after the initial HTTP response, so a script-level "
        "crash (e.g. a missing dependency) still returns a plain 200 "
        "while showing every visitor a traceback - treat "
        "page_renders_cleanly == false as a failure regardless of what "
        "the HTTP status codes say. IMPORTANT: if the page-render check "
        "itself couldn't run (e.g. this machine's own browser tooling is "
        "missing), that's reported as 'page_check_error' with "
        "page_renders_cleanly left as null/None - this is NOT the same "
        "as a confirmed page failure, it means the check is inconclusive, "
        "so judge health from the HTTP status codes in that case instead "
        "of assuming the app is broken. Returns a JSON string with all of "
        "the above, plus an 'error' key if any step failed. Requires a "
        "short-lived SSH private key already present at key_path "
        "(fetched by the workflow before the crew ran) - this tool "
        "never fetches or handles credentials itself."
    )

    def _run(
        self,
        instance_ip: str,
        key_path: str,
        remote_user: str,
        service_url: str,
        app_port: int = 8501,
    ) -> str:
        result: dict = {}
        try:
            _, out, _ = _ssh_run(
                key_path, remote_user, instance_ip,
                f"sudo ss -tnp state established '( sport = :{app_port} or dport = :{app_port} )' || true",
            )
            result["active_connections"] = out

            _, out, _ = _ssh_run(
                key_path, remote_user, instance_ip,
                f"curl -s -o /dev/null -w '%{{http_code}}' http://localhost:{app_port}",
            )
            result["local_http_code"] = out
            result["domain_http_code"] = _curl_status(service_url)
            result.update(_check_page_renders_cleanly(service_url))
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"

        return json.dumps(result, indent=2)


class ProdRollbackTool(BaseTool):
    name: str = "rollback_production"
    description: str = (
        "Actually reverts production to a specific earlier commit and "
        "restarts the app service: runs `git reset --hard "
        "<previous_commit>` in the app's directory on the server, "
        "restarts the systemd service, and re-checks the public "
        "domain's HTTP status afterward. Only call this after a "
        "confirmed production health-check failure - never "
        "speculatively. Requires the same short-lived SSH key as "
        "check_production_health."
    )

    def _run(
        self,
        instance_ip: str,
        key_path: str,
        remote_user: str,
        remote_app_dir: str,
        service_name: str,
        previous_commit: str,
        service_url: str,
    ) -> str:
        result: dict = {"target_commit": previous_commit}
        try:
            code, out, err = _ssh_run(
                key_path, remote_user, instance_ip,
                f"cd {remote_app_dir} && git reset --hard {previous_commit}",
            )
            result["reset_exit_code"] = code
            result["reset_output"] = out or err

            code, _, _ = _ssh_run(
                key_path, remote_user, instance_ip,
                f"sudo systemctl restart {service_name}",
            )
            result["restart_exit_code"] = code

            _, out, _ = _ssh_run(
                key_path, remote_user, instance_ip,
                f"cd {remote_app_dir} && git rev-parse HEAD",
            )
            result["current_commit"] = out

            time.sleep(5)
            result["post_rollback_domain_http_code"] = _curl_status(service_url)
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"

        return json.dumps(result, indent=2)
