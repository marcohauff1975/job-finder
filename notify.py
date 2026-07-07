"""
Emails the app owner whenever someone registers, via Amazon SES.

This is a side effect of registration, not part of it - a failure here
(SES down, credentials missing, whatever) is logged and swallowed so it
never blocks someone from creating an account.
"""

import logging

import boto3
from botocore.exceptions import BotoCoreError, ClientError

NOTIFY_TO = "marco.hauff@gmail.com"
NOTIFY_FROM = "marco.hauff@gmail.com"
AWS_REGION = "eu-north-1"

logger = logging.getLogger(__name__)


def send_registration_notification(email: str, name: str) -> None:
    try:
        client = boto3.client("ses", region_name=AWS_REGION)
        client.send_email(
            Source=NOTIFY_FROM,
            Destination={"ToAddresses": [NOTIFY_TO]},
            Message={
                "Subject": {"Data": f"Job Finder: new registration - {email}"},
                "Body": {
                    "Text": {
                        "Data": f"{name} ({email}) just registered for Job Finder."
                    }
                },
            },
        )
    except (BotoCoreError, ClientError):
        logger.exception("Failed to send registration notification for %s", email)


def send_devops_fix_notification(
    workflow_name: str,
    root_cause: str,
    fix_summary: str,
    files_changed: list[str],
    commit_pushed: bool,
    workflow_retriggered: bool,
) -> None:
    """Emails a summary whenever devops_agent auto-fixes and redeploys
    after a deploy failure, so this never happens silently."""
    try:
        client = boto3.client("ses", region_name=AWS_REGION)
        client.send_email(
            Source=NOTIFY_FROM,
            Destination={"ToAddresses": [NOTIFY_TO]},
            Message={
                "Subject": {
                    "Data": f"Job Finder: {workflow_name} failed - auto-fixed and redeployed"
                },
                "Body": {
                    "Text": {
                        "Data": (
                            f"Root cause: {root_cause}\n\n"
                            f"Files changed: {', '.join(files_changed) or '(none)'}\n\n"
                            f"Fix: {fix_summary}\n\n"
                            f"Commit pushed: {commit_pushed}\n"
                            f"Workflow re-triggered: {workflow_retriggered}"
                        )
                    }
                },
            },
        )
    except (BotoCoreError, ClientError):
        logger.exception("Failed to send devops-agent fix notification for %s", workflow_name)


def send_devops_giveup_notification(workflow_name: str, run_id: str) -> None:
    """Emails a warning when a deploy failed again right after a
    devops_agent auto-fix - it deliberately didn't try a second
    automatic fix, to avoid looping, so this needs a human now."""
    try:
        client = boto3.client("ses", region_name=AWS_REGION)
        client.send_email(
            Source=NOTIFY_FROM,
            Destination={"ToAddresses": [NOTIFY_TO]},
            Message={
                "Subject": {
                    "Data": f"Job Finder: {workflow_name} failed again after auto-fix - needs you"
                },
                "Body": {
                    "Text": {
                        "Data": (
                            f"Run {run_id} of {workflow_name} failed, and the most recent "
                            "commit on main is already a devops-agent auto-fix - so it did "
                            "not try to fix it again automatically, to avoid looping. "
                            "Please investigate manually."
                        )
                    }
                },
            },
        )
    except (BotoCoreError, ClientError):
        logger.exception("Failed to send devops-agent give-up notification for %s", workflow_name)
