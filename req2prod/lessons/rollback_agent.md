# Lessons — rollback_agent

## RB-01 — Only act after a confirmed test failure
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** considering a rollback.
- **Rule:** never roll back speculatively. Act only once a smoke-test failure has
  already been confirmed to you. (Paired with prod_tester PT-02: a `page_check_error`
  is not a confirmed failure — don't roll back on it.)

## RB-02 — Roll back the way this app deploys, then re-verify
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** performing a rollback.
- **Rule:** prod tracks a git remote and updates with `git pull --ff-only` + service
  restart. A rollback means pointing prod back at the previous known-good commit and
  restarting the same controlled way, then re-running the smoke test to confirm the
  rollback itself worked. Use `rollback_production` (it runs `git reset --hard` to the
  previous commit, restarts, and re-checks the public URL). Report the commit and the
  post-rollback status the tool actually returned — never an assumed outcome.

## RB-03 — Don't attempt fixes
- **Source:** operating principle
- **Severity:** process
- **Trigger:** after rolling back.
- **Rule:** state plainly what broke, what commit you rolled back to, and what still
  needs a human. Do not attempt fixes yourself.
