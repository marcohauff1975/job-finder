# Lessons — local_tester

## LT-01 — Exercise the exact flow the change touches
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** verifying a change locally.
- **Rule:** start the app the way a developer would, watch startup logs for
  tracebacks or warnings, then exercise exactly the flow the change touches — e.g. if
  `auth.py` changed, register and log in; if a template renderer changed, generate a
  resume in that format and confirm it opens without error.

## LT-02 — Report concrete pass/fail, never "looks fine"
- **Source:** operating principle
- **Severity:** process
- **Trigger:** reporting the result.
- **Rule:** give a clear pass/fail. If something breaks, say exactly what happened and
  paste the relevant error — never a vague "looks fine."
