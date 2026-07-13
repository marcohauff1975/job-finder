"""
Driver for the "DevOps auto-fix on deploy failure" GitHub Actions
workflow - calls fix_deploy_failure() and emails a summary of what
devops_agent found and did, so the fix is never applied silently (see
.github/workflows/devops-agent.yml).

Reads its parameters from environment variables:
    FAILED_WORKFLOW_NAME, FAILED_WORKFLOW_FILE, FAILED_RUN_ID

Exit code is 0 only if a fix was both committed/pushed and the
workflow was re-triggered. Anything else (no result, or either step
not completed) exits 1 so the run is visibly marked failed.
"""

import os
import sys

from notify import send_devops_fix_notification
from req2prod.Req2Prod import fix_deploy_failure


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


def main() -> int:
    workflow_name = _require("FAILED_WORKFLOW_NAME")
    workflow_file = _require("FAILED_WORKFLOW_FILE")
    run_id = _require("FAILED_RUN_ID")

    print(f"=== Diagnosing failure in {workflow_name} (run {run_id}) ===")
    result = fix_deploy_failure(workflow_name, workflow_file, run_id)
    print(result)

    if result is None:
        print("::error::devops_agent crew produced no result")
        return 1

    send_devops_fix_notification(
        workflow_name=workflow_name,
        root_cause=result.root_cause,
        fix_summary=result.fix_summary,
        files_changed=result.files_changed,
        commit_pushed=result.commit_pushed,
        workflow_retriggered=result.workflow_retriggered,
    )

    if not (result.commit_pushed and result.workflow_retriggered):
        print("::error::Fix was not fully applied/redeployed - check the email/logs")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
