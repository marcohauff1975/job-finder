"""
Read-only AWS inspection for the AWS Lead Engineer and Security
Engineer personas (see technology_excellence_* agents in sdlc/SDLC.py)
- checks the REAL, currently-running AWS setup, not just what the
Terraform code in crewai-infra claims it should be. Drift between the
two (e.g. a policy attached by hand that Terraform never touches) is
exactly what an infra-as-code-only review can't catch.

Every call here is a read-only AWS API call (Describe*/Get*/List* only
- never Create/Put/Delete/Attach/Update/Detach). Uses whatever AWS
credentials are already configured for the calling environment (the
same `aws configure` default credential chain the Terraform provider
itself uses) - this tool never fetches, stores, or handles credentials.
"""

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from crewai.tools import BaseTool


class AWSLiveSetupTool(BaseTool):
    name: str = "aws_live_setup_check"
    description: str = (
        "Inspects the REAL, currently-running AWS setup for the Job "
        "Finder project - not what the Terraform code in crewai-infra "
        "claims it should be. Returns: which IAM identity these API "
        "calls are actually running as; every Lightsail instance (its "
        "state, public IP, blueprint/bundle size, and its ACTUAL open "
        "firewall ports) so you can compare against "
        "aws_lightsail_instance_public_ports in main.tf; Lightsail "
        "static IPs and what they're attached to; every IAM user's "
        "attached AND inline policies, so you can catch something like "
        "an AdministratorAccess policy attached by hand that Terraform "
        "never defined and would never show up in a code-only review; "
        "and the AWS Budgets currently active, to confirm the "
        "budget_amount in budget.tf actually matches what AWS has "
        "live. Every call is read-only (Describe/Get/List) - never "
        "Create/Put/Delete/Attach/Update. Call this once; it always "
        "checks everything above in one pass, there's no way to narrow "
        "it, and a second call won't return anything new."
    )

    def _run(self, region: str = "eu-north-1") -> str:
        result: dict = {"region": region}

        try:
            identity = boto3.client("sts").get_caller_identity()
            result["running_as"] = {
                "account": identity.get("Account"),
                "arn": identity.get("Arn"),
            }
        except (BotoCoreError, ClientError) as e:
            result["running_as_error"] = (
                f"{e} - no valid AWS credentials found, so nothing below could "
                "be checked either. Report this as a finding: the live AWS "
                "setup could not be verified in this review."
            )
            return json.dumps(result, indent=2, default=str)

        lightsail = boto3.client("lightsail", region_name=region)

        try:
            instances = lightsail.get_instances().get("instances", [])
            result["lightsail_instances"] = [
                {
                    "name": i.get("name"),
                    "state": i.get("state", {}).get("name"),
                    "blueprint_id": i.get("blueprintId"),
                    "bundle_id": i.get("bundleId"),
                    "public_ip_address": i.get("publicIpAddress"),
                    "is_static_ip": i.get("isStaticIp"),
                    "open_ports": [
                        {
                            "protocol": p.get("protocol"),
                            "from_port": p.get("fromPort"),
                            "to_port": p.get("toPort"),
                            "access_from": p.get("cidrs") or p.get("accessFrom"),
                        }
                        for p in i.get("networking", {}).get("ports", [])
                    ],
                }
                for i in instances
            ]
        except (BotoCoreError, ClientError) as e:
            result["lightsail_instances_error"] = str(e)

        try:
            static_ips = lightsail.get_static_ips().get("staticIps", [])
            result["lightsail_static_ips"] = [
                {
                    "name": s.get("name"),
                    "ip_address": s.get("ipAddress"),
                    "is_attached": s.get("isAttached"),
                    "attached_to": s.get("attachedTo"),
                }
                for s in static_ips
            ]
        except (BotoCoreError, ClientError) as e:
            result["lightsail_static_ips_error"] = str(e)

        try:
            iam = boto3.client("iam")
            iam_summary = []
            for u in iam.list_users().get("Users", []):
                name = u["UserName"]
                attached = iam.list_attached_user_policies(UserName=name).get(
                    "AttachedPolicies", []
                )
                inline = iam.list_user_policies(UserName=name).get("PolicyNames", [])
                iam_summary.append(
                    {
                        "user": name,
                        "attached_policies": [p["PolicyName"] for p in attached],
                        "inline_policies": inline,
                    }
                )
            result["iam_users"] = iam_summary
        except (BotoCoreError, ClientError) as e:
            result["iam_users_error"] = str(e)

        try:
            budgets = boto3.client("budgets")
            account_id = result["running_as"]["account"]
            budget_list = budgets.describe_budgets(AccountId=account_id).get(
                "Budgets", []
            )
            result["aws_budgets"] = [
                {
                    "name": b.get("BudgetName"),
                    "limit": b.get("BudgetLimit"),
                    "actual_spend": b.get("CalculatedSpend", {}).get("ActualSpend"),
                }
                for b in budget_list
            ]
        except (BotoCoreError, ClientError) as e:
            result["aws_budgets_error"] = str(e)

        return json.dumps(result, indent=2, default=str)
