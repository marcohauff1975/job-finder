# Job Finder

**An AI-powered job search platform, built and operated by a fleet of purpose-built AI agents — not just using one.**

Job Finder helps a user search live job postings, research the hiring company, and get a resume automatically tailored to a specific role. What makes this project worth a second look isn't just the product: the entire software delivery lifecycle around it — code review, testing, deployment, rollback, infrastructure, and security auditing — is itself run by a coordinated system of specialized AI agents, each with its own model tier, tools, and accountability.

![SDLC Pipeline](https://github.com/marcohauff1975/job-finder/actions/workflows/sdlc-pipeline.yml/badge.svg)

---

## What it does

- **Job Finder agent** — searches the live web (via Serper) for postings matching a role, location, and remote preference.
- **Company Researcher agent** — takes a result and researches the hiring company.
- **Resume Tailor agent** — combines a user's uploaded resume, a specific job posting, and that research into a tailored resume, downloadable in multiple formats.
- **Multi-user by design** — every user's resumes, search history, and quotas are isolated under their own directory; no shared state between accounts.

## The part most projects don't have: an AI-run SDLC

Job Finder's own development pipeline is staffed by 15 named CrewAI agents across two systems — not a single "review my code" prompt, but specialists with distinct roles, tools, and model tiers matched to how much judgment and blast radius each one carries:

| Stage | Agent(s) | What it actually does |
|---|---|---|
| Requirements | `product_manager`, `software_architect` | Interactively challenge and refine a feature idea before a line of code is written; sessions are persisted so a conversation can be resumed later |
| Build | `software_engineer` | Implements the feature, opens a real pull request — never pushes to `main` directly |
| Code review | `code_reviewer` | Reviews every PR as a genuinely separate GitHub identity, capable of approving or requesting changes on its own review |
| Local/perf testing | `local_tester` | Boots the app, drives the actual changed flow in a browser, measures against a performance baseline |
| UX review | `ux_reviewer` | Checks the rendered UI against this app's own written UX guidelines, not generic taste |
| Deploy & verify | `prod_tester`, `rollback_agent` | Confirms no active user session before restarting, smoke-tests production, and automatically rolls back on failure |
| Incident response | `devops_agent` | Diagnoses a failed deploy from real CI logs, ships the smallest fix, and re-triggers the pipeline — capped at one automatic attempt before flagging a human |
| Readiness & security audit | `cto`, `aws_lead_engineer`, `python_lead_engineer`, `data_engineer`, `ai_engineer`, `security_engineer` | The **Technology Excellence panel** — six specialist personas that audit the real, live state of this project (not just the code) before anything ships publicly |

Every agent run — review rounds, deploys, rollbacks, incident fixes — is visualized live in an in-app "AI Viewer" dashboard, so the pipeline's actual behavior is observable, not a black box.

## AI engineering: right-sized models, not one-size-fits-all

Every agent's model tier (Claude Haiku, Sonnet, or Opus) is a deliberate choice, not a default — documented per agent against how often it runs, how subjective its judgment call is, and how much damage a wrong call could do. A mechanical pass/fail check (`local_tester`) runs on the cheapest tier; the one agent with sole veto power over publishing the entire project publicly (`security_engineer`) runs on the strongest. Assignments are editable live from an admin panel and take effect on an agent's next run — no redeploy needed.

## Infrastructure as Code

All AWS infrastructure — the Lightsail application server, its firewall rules, a fixed public IP, AWS Secrets Manager, IAM policies, and monthly budget alerts — is defined in Terraform in a companion repo, [job-finder-infra](https://github.com/marcohauff1975/job-finder-infra), not clicked together by hand. Two examples of what that discipline actually caught and fixed:

- **No secrets in code.** The app's admin credential used to be a hardcoded string; it's now generated and stored in AWS Secrets Manager, fetched at runtime, with the old value purged from git history entirely.
- **Least-privilege IAM, proven, not assumed.** The credential Terraform itself runs as previously held full `AdministratorAccess` over the AWS account. It now runs on a scoped policy — granting only what the actual Terraform resources need — with the removal of that admin access itself defined and applied as code, verified by actually running the deploy pipeline under the reduced permissions before calling it done.

## Security posture

- Application secrets (API keys, the admin password) are never committed — loaded from environment variables or fetched from Secrets Manager at runtime.
- `.gitignore` excludes the auth database, per-user data, and generated resumes by policy, not by accident.
- CI/CD deploys authenticate via short-lived, per-run SSH certificates fetched through AWS Lightsail's own access API — not a long-lived key sitting in a repo secret.
- Every deploy checks for an active user session before restarting the service, and automatically rolls back if a post-deploy health check fails.
- GitHub secret scanning (with push protection) and Dependabot alerts/security updates are enabled on the repo itself, as an independent second layer alongside the Technology Excellence panel's own checks below.

## Auditing itself before shipping

Before any change goes live publicly, the **Technology Excellence panel** — six AI personas covering architecture, AWS, Python, data handling, AI engineering, and security — reviews the actual current state of the project: real git history (not just the working tree), the real running AWS account (not just the Terraform files), and the real GitHub-hosted repo (visibility, security settings, open alerts). Findings are evidence-based only — nothing is reported unless a tool call actually surfaced it — and any confirmed leaked secret or over-privileged credential is a hard block on publishing, regardless of how good everything else looks.

## Tech stack

| Layer | Technology |
|---|---|
| UI | Streamlit (multi-user, session-isolated) |
| AI orchestration | CrewAI, Anthropic Claude (Haiku 4.5 / Sonnet 5 / Opus 4.8, tiered per agent) |
| Auth & data | SQLite, per-user file storage |
| Infrastructure | Terraform, AWS Lightsail, AWS Secrets Manager, AWS Budgets, IAM |
| CI/CD | GitHub Actions — automated PR review, deploy, prod smoke test, auto-rollback, auto-incident-fix |
| Email | Amazon SES |

## Setup

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your API keys
```

## Run

```bash
streamlit run streamlit_app.py
```
