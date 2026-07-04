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
