# Lessons — pr_fix_agent

## PF-01 — Act only on code_reviewer's findings
- **Source:** operating principle
- **Severity:** process
- **Trigger:** fixing a PR that code_reviewer requested changes on.
- **Rule:** only act on findings code_reviewer already reported. Never go looking
  for additional problems, and never refactor, rename, or "improve" beyond what each
  finding specifically calls for.

## PF-02 — Confirm each finding against the real file first
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** applying a fix for a finding.
- **Rule:** read the actual file at the flagged location first and confirm the
  problem is really there, then apply the narrowest fix that resolves it. If a
  finding doesn't reproduce (code_reviewer misread, or a prior round already fixed
  it), leave that file alone rather than changing something that isn't wrong.

## PF-03 — Stay within scope; don't commit
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** working through a fix round.
- **Rule:** never touch `.env`, anything under `data/` or `users/`, or any file not
  named in the findings you were given. Do not commit or push — the pipeline handles
  that once after every attempt is finished. Your only job is making the named files
  correct.
