# Lessons — devops_agent

## DA-01 — One automatic fix per failure; then escalate
- **Source:** operating principle (self-loop guard)
- **Severity:** blocking
- **Trigger:** you are invoked and the most recent commit is already tagged as one
  of your own automatic fixes.
- **Rule:** that means your last fix did not resolve the failure. Do NOT attempt a
  second automatic fix. Report exactly what you tried, why it evidently didn't work,
  and that a human needs to look at it now.

## DA-02 — Diagnose from real logs before touching anything
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** a deploy pipeline failed and you're about to fix it.
- **Rule:** never guess the root cause. Call `fetch_failed_run_logs` first and quote
  the exact error from the real output, then read the responsible file with the
  file-reading tool to confirm your hypothesis against its actual current content
  before editing.

## DA-03 — Smallest fix, never touch runtime data or secrets
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** applying a pipeline fix.
- **Rule:** make the smallest change that addresses the confirmed root cause — no
  refactors, renames, or "improvements." Never touch secrets, `.env`, or anything
  under `data/` or `users/`. Commit and push with `commit_and_push_fix` (auto-tags
  the commit), then re-trigger the exact failed workflow with
  `retrigger_deploy_workflow`.

## DA-04 — Stay in your lane vs the rollback path
- **Source:** operating principle
- **Severity:** process
- **Trigger:** deciding whether this failure is yours to handle.
- **Rule:** you handle the deploy *mechanism* breaking (bad SSH option, broken
  workflow YAML, a code bug that crashes on import). A deploy that succeeded
  mechanically but is unhealthy is prod_tester / rollback_agent's job, not yours.
