# Req2Prod demo prefill buttons — design

**Date:** 2026-07-15
**Status:** Approved (design), pending implementation plan
**Area:** `req2prod/admin_ui.py` — "Request a New Feature" admin sub-page

## Purpose

Give a fast, credible live demo of Req2Prod executing a real change on the
Job Finder app. Two buttons on the "Request a New Feature" page pre-fill the
feature-request input with a ready-made requirement — one that **adds** the
`req2prod` logo to the top-right of the main Job Finder page, and one that
**removes** it. The human reviews the prefilled text and initiates the run;
from there the existing SDLC pipeline (Product Manager → Software Architect →
Software Engineer → PR → code-review bot merge → auto-deploy) ships the change
to production.

The demo must also work on the live site with Marco's Mac switched off, so the
logo asset must be fully self-contained (no `file://`, no external requests).

## Non-goals (YAGNI)

- **No auto-execution.** Clicking a button never calls an agent, never opens a
  PR, never deploys. It only prefills. The user drives initiation.
- **No new pipeline.** We reuse the existing `challenge_requirement` →
  "Push to Software Engineer" → `build_feature` → PR → merge → deploy path
  unchanged. The buttons only seed its input.
- **No auto-clicking** through the "Push to Software Engineer" / merge / deploy
  gates. Those stay human-driven exactly as today.
- **No visual toggle.** The logo is not injected directly by the buttons; it
  reaches the page only through a real pipeline run and merge.

## Key technical constraint

Streamlit's `st.chat_input` cannot be programmatically pre-filled — its value
is React-controlled and clears on submit. So we do **not** try to inject text
into the chat input via DOM/JS. Instead, when a demo button is clicked we render
an editable `st.text_area` (which *can* be seeded from session state) holding
the canned requirement, plus an explicit **Send to agents** button. The existing
`st.chat_input` stays in place for free-form typing.

## UI / flow

In `render_requirements_tab()` (`req2prod/admin_ui.py`), directly under the
existing hero markdown:

1. **Demo row** — two `st.button`s side by side:
   - `➕ Demo: Add Req2Prod Logo`
   - `➖ Demo: Remove Req2Prod Logo`

   Clicking either sets `st.session_state["rc_demo_prefill"] = <canned text>`
   and calls `st.rerun()`. No agent/pipeline call.

2. **Prefilled editable field** — rendered only when
   `st.session_state.get("rc_demo_prefill")` is set:
   - `st.text_area` seeded with the canned text (editable by the user).
   - A **Send to agents** button and a **Clear** button (Clear wipes
     `rc_demo_prefill` and reruns, hiding the field).
   - On **Send**, call the shared `_submit_requirement(text)` helper (below),
     then clear `rc_demo_prefill`.

3. **Existing `st.chat_input`** — unchanged; still available for typed input.

### Refactor: extract `_submit_requirement(text)`

The current submit logic lives inline in the `st.chat_input` handler
(`req2prod/admin_ui.py:337`–`400`): it creates a session id if needed, appends
the `{"role": "user", "content": text}` message, saves the session, runs
`challenge_requirement` inside the spinner (with the existing `[DIAG]` prints and
`_run_with_retry`), appends the PM + architect results, saves, and reruns.

Extract this body verbatim into `_submit_requirement(text: str) -> None` and call
it from both:
- the `st.chat_input` path (`if prompt: _submit_requirement(prompt)`), and
- the demo **Send to agents** button.

No behavior change to the typed path — this is a pure extraction so both entry
points share one code path.

## The canned prompts (the crux of reliability)

The prompts are specific enough that the Software Engineer agent ships the exact
change. Both target the **main public Job Finder landing page** rendered in
`streamlit_app.py` *after* the admin block (the `st.stop()` at
`streamlit_app.py:253`), and use a stable container id so add/remove are exact
inverses.

### Add-logo prompt

> Add the `req2prod` logo to the top-right corner of the main public Job Finder
> landing page (the page rendered in `streamlit_app.py` after the admin block
> that ends at the `st.stop()` around line 253 — not the admin tabs).
>
> Inject it once, near the top of that main-page render, via
> `st.markdown(..., unsafe_allow_html=True)`. Wrap it in a single fixed-position
> container with `id="req2prod-demo-logo"` pinned to the top-right
> (`position: fixed; top: 12px; right: 16px; z-index: 1000;`) so it overlays the
> corner and does not disturb page layout. Use this exact self-contained inline
> SVG (no external files, fonts, or network requests):
>
> ```html
> <div id="req2prod-demo-logo" style="position:fixed;top:12px;right:16px;z-index:1000;">
>   <svg width="150" height="45" viewBox="0 0 300 90" fill="none" xmlns="http://www.w3.org/2000/svg">
>     <defs><linearGradient id="r2p-c1" x1="8" y1="26" x2="30" y2="64" gradientUnits="userSpaceOnUse"><stop stop-color="#818cf8"/><stop offset="1" stop-color="#a78bfa"/></linearGradient></defs>
>     <path d="M10 28 L28 45 L10 62" stroke="url(#r2p-c1)" stroke-width="6" fill="none" stroke-linecap="round" stroke-linejoin="round"/>
>     <text x="44" y="59" font-family="ui-monospace,'SF Mono','JetBrains Mono',Menlo,Consolas,monospace" font-size="42" font-weight="700" letter-spacing="-2" fill="#f1f5f9">req<tspan fill="#818cf8">2</tspan>prod</text>
>     <rect x="258" y="30" width="15" height="30" rx="2.5" fill="#818cf8"><animate attributeName="opacity" values="1;1;0;0;1" keyTimes="0;.5;.5;1;1" dur="1.1s" repeatCount="indefinite"/></rect>
>   </svg>
> </div>
> ```
>
> Do not add the logo to the admin pages — only the main public landing page.

### Remove-logo prompt

> Remove the `req2prod` demo logo from the main public Job Finder landing page.
> Delete the fixed-position container with `id="req2prod-demo-logo"` (and its
> inline SVG) that was previously injected near the top of the main-page render
> in `streamlit_app.py`, along with the `st.markdown(...)` call that emits it.
> Leave the rest of the page unchanged. After this change the main landing page
> should render with no `req2prod` logo in the corner.

The exact prompt strings live as module-level constants in `req2prod/admin_ui.py`
(e.g. `DEMO_ADD_LOGO_REQUIREMENT`, `DEMO_REMOVE_LOGO_REQUIREMENT`) so the SVG
markup is defined once and easy to tweak.

## Why the logo works with the Mac off

The SVG is the "primary lockup" from `req2prod_option4_kit.html` — pure inline
markup with no `file://` reference, no external font (monospace system stack),
no network request. Once the pipeline merges and deploys it, the live Lightsail
server renders it independently of Marco's Mac.

## Testing / verification

- **Prefill behavior (local):** clicking each demo button shows the editable
  field seeded with the correct canned text; no agent call fires on click
  (verify via absence of the `[DIAG] challenge_requirement starting` log until
  Send is pressed). Clear hides the field.
- **Shared path:** typing in `st.chat_input` and pressing Send from the demo
  field both route through `_submit_requirement` and produce identical message
  flow.
- **End-to-end (staging/live, human-driven):** run the Add prompt through the
  real pipeline once and confirm the logo appears top-right on the deployed main
  page; run Remove and confirm it's gone. This is a live pipeline run, done
  manually, not part of automated tests.

## Files touched

- `req2prod/admin_ui.py` — demo button row, prefilled field, `_submit_requirement`
  extraction, two prompt constants. Additive; typed path unchanged.
