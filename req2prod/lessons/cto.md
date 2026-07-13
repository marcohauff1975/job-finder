# Lessons — cto

## CT-01 — Never assume the local checkout is what the world sees
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** judging the repo's public first impression.
- **Rule:** always call `github_live_repo_check`. A repo that's flawless locally is
  worthless linked from a resume if it's sitting private on GitHub with no
  description. Only report what you actually opened and read via tools — never guess
  README quality or structure from file names.

## CT-02 — Judge the resume-click first impression
- **Source:** operating principle
- **Severity:** process
- **Trigger:** chairing the readiness review.
- **Rule:** a hiring manager who clicks the link should, in 2–5 minutes, get a clear
  evidence-backed picture of what Marco can build. Check: does the README explain what
  this is, why it exists, and what's impressive in under a minute; is the top-level
  layout self-explanatory to a stranger; is there a LICENSE; is there leftover
  scaffolding cruft, embarrassing commit messages, or TODO placeholders in tracked
  files that undercut the pitch.

## CT-03 — Security findings win; don't inflate or omit
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** synthesizing the panel's verdict.
- **Rule:** the security_engineer's blocking findings always win, no exceptions.
  Assemble the strongest *honest* case from what the panel actually observed — never
  inflate a claim beyond the evidence, never omit a real problem to make the pitch
  sound better.
