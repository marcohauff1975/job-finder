"""
Driver for the "AI prod flow" GitHub Actions workflow - calls
test_production() and, if it fails, rollback(), so prod_tester and
rollback_agent (using their real tools in sdlc/tools/prod_ops.py)
make the verify/rollback decision themselves, instead of plain bash
doing it (compare .github/workflows/deploy-to-prod.yml, which is
bash/curl only).

Reads its parameters from environment variables so the calling
workflow never has to interpolate values into a Python string
directly:
    INSTANCE_IP, SSH_KEY_PATH, REMOTE_USER, REMOTE_APP_DIR,
    SERVICE_NAME, SERVICE_URL, PREVIOUS_COMMIT, NEW_COMMIT,
    CHANGE_SUMMARY (optional), FLOW_TO_TEST (optional)

Exit code is 0 only if production was healthy with no rollback
needed. A needed-and-successful rollback still exits 1, so the
workflow run is visibly marked failed - the deploy itself did not
succeed even though production was returned to a working state.
"""

import os
import sys

from sdlc.SDLC import rollback, test_production


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    instance_ip = _require("INSTANCE_IP")
    key_path = _require("SSH_KEY_PATH")
    remote_user = _require("REMOTE_USER")
    remote_app_dir = _require("REMOTE_APP_DIR")
    service_name = _require("SERVICE_NAME")
    service_url = _require("SERVICE_URL")
    previous_commit = _require("PREVIOUS_COMMIT")
    new_commit = _require("NEW_COMMIT")
    change_summary = os.environ.get("CHANGE_SUMMARY", "Deploy to production.")
    flow_to_test = os.environ.get(
        "FLOW_TO_TEST", "The homepage loads and login/registration renders."
    )

    print("=== Running prod_tester ===")
    prod_result = test_production(
        service_url=service_url,
        change_summary=change_summary,
        flow_to_test=flow_to_test,
        instance_ip=instance_ip,
        key_path=key_path,
        remote_user=remote_user,
    )
    print(prod_result)

    if prod_result is None:
        print("::error::prod_tester crew produced no result")
        return 1

    if prod_result.passed:
        print("Production healthy, no rollback needed.")
        return 0

    print(f"::warning::Production smoke test failed: {prod_result.observation}")
    print("=== Running rollback_agent ===")
    rollback_result = rollback(
        new_commit=new_commit,
        failure_reason=prod_result.observation,
        previous_commit=previous_commit,
        instance_ip=instance_ip,
        key_path=key_path,
        remote_user=remote_user,
        remote_app_dir=remote_app_dir,
        service_name=service_name,
        service_url=service_url,
    )
    print(rollback_result)

    if rollback_result is None or not rollback_result.rollback_test_passed:
        print("::error::Rollback did not restore health either - needs manual investigation")
        return 1

    print(f"::warning::Rolled back to {rollback_result.current_commit}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
