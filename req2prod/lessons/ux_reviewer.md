# Lessons — ux_reviewer

## UX-01 — Review against this app's own written guidelines, not generic best practice
- **Source:** operating principle
- **Severity:** process
- **Trigger:** about to give feedback on a changed flow.
- **Rule:** never give freeform "looks a bit off" or generic listicle feedback (add a
  spinner, improve contrast, add whitespace). Check against `{ux_guidelines}` — the
  app's documented conventions (purple-to-cyan gradient brand language, which
  spinner-copy style applies to which operation, error/success message tone,
  session_state view routing with matching back-buttons/reruns, per-item widget-key
  namespacing). Every finding names the guideline section it violates and the exact
  screen/step where you saw it.

## UX-02 — Drive the real app; one tool call per user-facing view
- **Source:** operating principle
- **Severity:** correctness
- **Trigger:** inspecting a view.
- **Rule:** call `inspect_job_finder_page` once per front-end view you're asked to
  cover (`main` or `format`) — never for the hidden admin page, which isn't part of
  the user-facing UX. Base every finding on what the tool actually returned
  (screenshot, visible text, measured styles/contrast), not on what a Streamlit app
  is "supposed" to look like.

## UX-03 — Say so when the guidelines themselves are wrong
- **Source:** operating principle
- **Severity:** process
- **Trigger:** the guidelines conflict with what the rest of the app actually does now.
- **Rule:** don't silently override them and don't flag pure matters of taste not
  covered by them. If the guidelines are out of date against current app behavior,
  say so explicitly.
