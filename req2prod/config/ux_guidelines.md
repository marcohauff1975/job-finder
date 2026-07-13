# Job Finder UX Guidelines

Reference rubric for `ux_reviewer`. These are the conventions this app
already follows (as implemented in `streamlit_app.py`), not generic
best practices - a finding should cite which point below it violates,
not just assert something "feels off."

## Visual language

- Theme is fixed and dark: background `#0a0a0f`, secondary surface
  `#161622`, body text `#f1f5f9`, defined in `.streamlit/config.toml`.
  Don't flag "dark mode has poor contrast" in the abstract - check the
  *actual* computed contrast ratio of specific text against its actual
  background.
- Brand accent is a purple-to-cyan gradient, used for the hero title,
  primary buttons, and info-alert borders - but two distinct violet
  shades are intentionally used, not one: the hero title is
  `#a78bfa -> #22d3ee` (lighter violet), while primary buttons are
  `#8b5cf6 -> #22d3ee` (deeper violet). Don't flag one using the
  other's exact shade as a mismatch - only flag a genuinely different,
  unrelated accent color being introduced.
- All custom CSS lives in exactly one `st.markdown(..., unsafe_allow_html=True)`
  block at the top of `streamlit_app.py`. If a change adds a second,
  separate `unsafe_allow_html` style block elsewhere in the file, flag
  it - styles should stay centralized so selectors don't conflict.
- Font is Inter, loaded via Google Fonts `@import`. Flag anything that
  overrides this per-element.

## Loading states

There are two established copy patterns for `st.spinner`, and a
change should match the one appropriate to what's happening:
- **Multi-agent / genuinely slow operations** (resume review, job
  search) use the playful, vague "✨ Magic is happening, please
  wait..." - because the real steps are numerous and not worth
  itemizing to the user.
- **Single, scoped sub-steps** (rebuilding one format, researching one
  named company, tailoring for one specific job) use literal copy that
  names the specific entity: `"Researching {company}..."`,
  `"Rebuilding your resume in this format..."`. A new scoped operation
  should get specific copy, not reuse the vague "Magic is happening"
  line, and vice versa - don't itemize steps for something that's
  genuinely one indivisible multi-agent call.
- Never leave a button-triggered multi-second operation with no
  `st.spinner` at all - flag any new CrewAI/API call added to a button
  handler without one.

## Error, success, and empty-state copy

- Errors are actionable, not just "something went wrong": e.g. the
  quota-limit error names the exact limit and gives a concrete next
  step (`"Contact support for the paid version: marco.hauff@gmail.com"`),
  the missing-env-var error names the exact variable and says where to
  add it (`.env` file). A new error should follow this pattern: state
  what happened and what the user can concretely do about it.
- Successes confirm the specific thing that changed (`"Resume
  replaced."`, `"Saved - {format} is now your resume on file."`), not
  a generic "Success!".
- Empty states say plainly that there's nothing, without extra
  padding or dead space (`st.info("No postings found for this
  search.")`). A new list/results view should have an explicit empty
  state, not silently render nothing.

## Layout and flow structure

- Section starts are `st.markdown("### ...")` followed by a plain
  `st.write()` sentence explaining what the section does in one line
  (see "Review your resume", "Search for jobs"). A new section should
  follow the same header + one-line explainer pattern, not a header
  alone.
- Secondary/optional content (replacing an existing resume, expanded
  research/tailoring results) lives in `st.expander`, not inline -
  keeps the primary flow (upload -> review/search -> results)
  uninterrupted. A new secondary action added inline instead of in an
  expander should be flagged.
- Multi-step flows use `st.session_state["view"]` as the only routing
  mechanism (e.g. `"main"` vs `"format"`), always paired with an
  explicit `"← Back"` button, and a `st.rerun()` after every state
  change that should immediately reflect on screen. A new multi-step
  flow should follow the same pattern - flag any new step that changes
  state without a matching way back or without a `rerun()`.
- Card-style columns (e.g. the format-selection columns) rely on the
  flex CSS block that stretches columns and anchors the action button
  to the bottom regardless of description length. A new column-based
  layout with per-card buttons should reuse this pattern, not
  introduce misaligned buttons at inconsistent heights.

## Session state and identity

- Widget keys that repeat per item are namespaced by index or a stable
  id (`key=f"fmt_prep_{template_key}"`, `key=f"research_btn_{i}"`). Any
  new repeated widget without a unique, stable key should be flagged -
  it will silently collide in Streamlit's session state.
- Anything cached in `session_state` that's derived from the uploaded
  resume (`resume_review`, `resume_content`, `fmt_bytes_*`) is
  explicitly cleared whenever the resume is replaced. A new cached
  value derived from the resume that *isn't* cleared alongside these
  should be flagged as stale-data risk, not just a style nitpick.

## What NOT to flag

- Generic "add more whitespace" / "use a different font" / "consider
  a design system" feedback with no tie to a guideline above.
- Accessibility or contrast complaints without an actual measured
  ratio against the specific colors involved.
- Anything not actually observed by driving the running app - don't
  infer a UX problem purely from reading source code.
