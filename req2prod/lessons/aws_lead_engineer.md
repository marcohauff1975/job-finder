# Lessons — aws_lead_engineer

## AW-01 — Never trust the Terraform alone; compare to the live account
- **Source:** operating principle (been burned by drift before)
- **Severity:** correctness
- **Trigger:** reviewing infrastructure-as-code.
- **Rule:** always call `aws_live_setup_check` and compare what's actually running
  against what the code claims. A policy attached by hand, or a port opened for one
  afternoon of debugging and never closed, won't appear in the code. Any mismatch is
  itself a finding, before you even judge whether either side is good practice.

## AW-02 — Judge IaC like a take-home
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** reading the Terraform.
- **Rule:** check that state is managed remotely (no `.tfstate` in the repo), IAM
  policies are least-privilege (no wildcard actions/resources), security groups have
  no unnecessary open ingress (`0.0.0.0/0` on anything sensitive), variables are used
  instead of hardcoded values, and there's a workflow actually applying the IaC
  rather than it only ever being run by hand.

## AW-03 — History-check anything credential-shaped
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** you find a `.pem`, `.key`, or tfvars with real values.
- **Rule:** run `git_file_history` on it — gitignored-now doesn't mean clean-history.
  A committed private key or state file is a blocking finding, not a style nit.
