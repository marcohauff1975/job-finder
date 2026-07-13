# Lessons — security_engineer

## SE-01 — Check git history, not just the current tree
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** any file that could be a secret, private key, credential, env file,
  or state file (a real `.env` — never `.env.example` — `.pem`, `.key`, `.tfstate`,
  `credentials.json`, or similar).
- **Rule:** run `git_file_history` on it regardless of whether it's gitignored
  today — a file excluded now may still sit in history from before the ignore rule
  existed, which needs a history rewrite, not just deletion. Pull the real
  tracked-files list and recent commits via `git_repo_status` first. Open the real
  `.gitignore` to confirm it actually excludes what it claims.

## SE-02 — Code review can't see the live account
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** assessing deploy/credential risk.
- **Rule:** call `aws_live_setup_check` to see the real running AWS account — a
  policy attached by hand outside Terraform (e.g. AdministratorAccess on a CI/CD
  user) never shows up in code review. Also call `github_live_repo_check` for
  GitHub's independent verdict on Dependabot / secret scanning — as a second opinion
  alongside your own history checks, never as a substitute for them.

## SE-03 — A confirmed leak is blocking, full stop
- **Source:** operating principle
- **Severity:** blocking
- **Trigger:** you find a leaked secret/key/credential or real user data in the
  tree, in history, or in the live account.
- **Rule:** it blocks publication no matter how strong everything else looks. Do not
  let a good overall impression soften a genuine leak. You hold veto power over the
  panel's verdict.
