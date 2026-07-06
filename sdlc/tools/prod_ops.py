"""
SSH/curl-backed tools that let prod_tester and rollback_agent actually
act on the production Lightsail instance, instead of only reasoning
over text passed to them - closes the gap noted in sdlc/SDLC.py for
these two agents (ux_reviewer already has an equivalent for the app's
front end, via tools/ux_inspector.py).

Neither tool fetches or handles AWS/SSH credentials itself - both
expect a short-lived SSH private key to already exist on disk at
key_path, fetched by whatever is driving the crew (see
.github/workflows/ai-prod-flow.yml). This keeps credential handling
entirely in the calling workflow, not in agent-callable code.
"""

import json
import subprocess
import time

from crewai.tools import BaseTool

SSH_OPTS = ["-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10"]


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
        "port), the HTTP status of the app on the server itself, and "
        "the HTTP status of the public domain. Returns a JSON string "
        "with all three, plus an 'error' key if any step failed. "
        "Requires a short-lived SSH private key already present at "
        "key_path (fetched by the workflow before the crew ran) - this "
        "tool never fetches or handles credentials itself."
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
