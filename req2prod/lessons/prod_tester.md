# Lessons — prod_tester

## PT-01 — An HTTP 200 does not prove a healthy deploy
- **Source:** 2026-07-08 · ModuleNotFoundError incident
- **Severity:** blocking
- **Trigger:** judging deploy health from `check_production_health` output.
- **Rule:** Streamlit renders a script-level crash *inside* a normal HTTP 200
  response, not as an HTTP failure. On 2026-07-08 a new dependency was never
  installed on the server and the app crashed on every request, while HTTP stayed
  200. `page_renders_cleanly` is the one field that catches this class of failure —
  treat `page_renders_cleanly == false` as an unconditional fail even if every HTTP
  status looks perfect.

## PT-02 — page_check_error is inconclusive, not a failure
- **Source:** 2026-07-09 · false-rollback incident (day after PT-01)
- **Severity:** false-positive
- **Trigger:** `page_check_error` is set and `page_renders_cleanly` is null.
- **Rule:** that means the page-render check itself could not run (its browser
  tooling was missing on the machine running you) — it is NOT a confirmed page
  failure. Confusing the two once already triggered a false rollback of a perfectly
  healthy deploy. When `page_check_error` is set, treat the render check as
  inconclusive and judge health from the HTTP status codes instead. Never fail the
  deploy on `page_check_error` alone.

## PT-03 — Don't disrupt real users; ground the verdict in tool output
- **Source:** operating principle
- **Severity:** process
- **Trigger:** running a smoke test against live prod.
- **Rule:** first check whether someone is actively using the app (recent nginx
  activity, open sessions) and flag it rather than barrelling ahead. Run only
  minimal, non-destructive checks. Never create test data that could be mistaken for
  a real account, and clean up anything you create. Call `check_production_health`
  with the exact connection details you're given, and base pass/fail only on what it
  actually returned — never on what a healthy deploy is "supposed" to look like.
