"""
Admin UI for the Req2Prod product's own tabs on the "AI Models"/
"Req2Prod Pipeline"/"Request a New Feature" pages in streamlit_app.py.

Pulled out of streamlit_app.py so that file's admin section reflects the
actual product boundary already true at runtime: this code only ever
touches req2prod/*, req2prod_agent_backend_mode.py, req2prod_agent_steps.py,
req2prod_deploy_mode.py, and req2prod_pr_flow.py - never the Job Finder product's
own code (auth.py/job_search.py/reporting.py, which streamlit_app.py's
Overview tab and Job Finder path still own directly - see
jobfinder_admin.py for the Overview tab's equivalent). Pure extraction:
every function body below is unchanged from streamlit_app.py, just
relocated (and in two cases wrapped in a def or renamed - see each
function's own note).

First file inside req2prod/ to import a top-level sibling module
(req2prod_agent_backend_mode, req2prod_agent_steps, req2prod_deploy_mode,
req2prod_pr_flow) - that's fine, pytest.ini's pythonpath=. and Streamlit's
own sys.path both already make this resolvable the same way
streamlit_app.py always has; just a new pattern for this package, not a
new risk.
"""

import json
import time
from pathlib import Path

import streamlit as st
import yaml

from req2prod.Req2Prod import (
    ArchitectureDirectionResult,
    EngineerQuestion,
    FeatureRequirementsResult,
    build_feature,
    challenge_requirement,
)
from req2prod.model_registry import (
    AGENT_DISPLAY_NAMES,
    MODEL_DISPLAY_NAMES,
    RECOMMENDATIONS,
    TECH_EXCELLENCE_AGENT_KEYS,
    load_agent_models,
    set_agent_model,
)
from req2prod.requirements_sessions import new_session_id, save_session
from req2prod_agent_backend_mode import get_agent_backend, set_agent_backend
from req2prod_agent_steps import get_agent_activity
from req2prod_deploy_mode import get_auto_deploy_mode, set_auto_deploy_mode
from req2prod_pr_flow import get_latest_pr_flow, render_pr_flow_svg


# Placeholder text of the requirements st.chat_input - shared by the widget
# itself and the JS selector that prefills it (see _prefill_chat_input_if_requested).
_REQUIREMENT_INPUT_PLACEHOLDER = "Describe a feature, or answer the open questions above..."

# The Req2Prod sub-tab labels, in render order, for streamlit_app.py's
# st.tabs() call. Here rather than inline there because
# _jump_to_pipeline_tab_if_requested has to find the Pipeline tab in the
# rendered DOM by its visible text - st.tabs exposes no way to select a tab
# from Python, so the label a button clicks and the label the tab actually
# carries have to be one string rather than two that happen to agree.
REQ2PROD_TAB_LABELS: tuple[str, str, str] = (
    "Request a New Feature",
    "Pipeline",
    "Documentation",
)
PIPELINE_TAB_LABEL = REQ2PROD_TAB_LABELS[1]


# --- Demo prefill requirements -------------------------------------------
# Ready-made feature requests seeded (not auto-run) by the "Demo" buttons on
# the Request-a-New-Feature page. They demonstrate Req2Prod shipping a real
# change to the Job Finder app: one adds the req2prod logo to the top-right of
# the main public landing page, the other removes it. The SVG is the
# self-contained "primary lockup" from req2prod_option4_kit.html - pure inline
# markup, no file:// path, no external font or network request - so the logo
# renders on the live site with Marco's Mac switched off once merged/deployed.
# The stable id="req2prod-demo-logo" makes Add and Remove exact inverses.
# top:72px, not 12px: Streamlit renders its own header as a full-width,
# 60px-tall, opaque bar at z-index 999990. A logo at top:12px sits entirely
# inside that band, and z-index:1000 loses to 999990 - so it rendered
# perfectly and was painted over, every time. That's what made three
# separate "add the logo" runs look like they'd produced nothing: the SVG
# was never the problem, and regenerating it could never have fixed it.
# 72 = the header's 60px plus the 12px gap the original was going for.
_DEMO_LOGO_SVG = (
    '<div id="req2prod-demo-logo" style="position:fixed;top:72px;right:16px;z-index:1000;">'
    '<svg width="150" height="45" viewBox="0 0 300 90" fill="none" xmlns="http://www.w3.org/2000/svg">'
    '<defs><linearGradient id="r2p-c1" x1="8" y1="26" x2="30" y2="64" gradientUnits="userSpaceOnUse">'
    '<stop stop-color="#818cf8"/><stop offset="1" stop-color="#a78bfa"/></linearGradient></defs>'
    '<path d="M10 28 L28 45 L10 62" stroke="url(#r2p-c1)" stroke-width="6" fill="none" '
    'stroke-linecap="round" stroke-linejoin="round"/>'
    '<text x="44" y="59" font-family="ui-monospace,\'SF Mono\',\'JetBrains Mono\',Menlo,Consolas,monospace" '
    'font-size="42" font-weight="700" letter-spacing="-2" fill="#f1f5f9">req<tspan fill="#818cf8">2</tspan>prod</text>'
    '<rect x="258" y="30" width="15" height="30" rx="2.5" fill="#818cf8">'
    '<animate attributeName="opacity" values="1;1;0;0;1" keyTimes="0;.5;.5;1;1" dur="1.1s" repeatCount="indefinite"/>'
    '</rect></svg></div>'
)

DEMO_ADD_LOGO_REQUIREMENT = (
    "Add the req2prod logo to the top-right corner of the main public Job Finder "
    "landing page - the page rendered in streamlit_app.py after the admin block "
    "that ends at the `st.stop()` around line 253, NOT the admin tabs.\n\n"
    "Inject it once, near the top of that main-page render, via "
    "`st.markdown(..., unsafe_allow_html=True)`. Use this exact self-contained "
    "inline SVG (no external files, fonts, or network requests) - it is already "
    "wrapped in a fixed-position container pinned to the top-right corner that "
    "overlays the page without disturbing layout:\n\n"
    "```html\n" + _DEMO_LOGO_SVG + "\n```\n\n"
    "Keep the container's `id=\"req2prod-demo-logo\"` exactly as given so it can be "
    "removed cleanly later. Do not add the logo to any admin page - only the main "
    "public landing page."
)

DEMO_REMOVE_LOGO_REQUIREMENT = (
    "Remove the req2prod demo logo from the main public Job Finder landing page. "
    "Delete the fixed-position container with `id=\"req2prod-demo-logo\"` (and its "
    "inline SVG) that was previously injected near the top of the main-page render "
    "in streamlit_app.py, along with the `st.markdown(...)` call that emits it. "
    "Leave the rest of the page unchanged. After this change the main landing page "
    "should render with no req2prod logo in the corner."
)


def _seed_demo_request(text: str) -> None:
    """Drop a ready-made request into the chat input, ready to review and send.

    Nothing runs here: the demo goes out through the same ↑ send button as a
    typed message, so it exercises the real flow rather than a shortcut past
    it. The nonce makes the browser inject this exact text once per click
    (see _prefill_chat_input_if_requested's guard), and rc_refine_open asks
    for the input box back - a demo needs the box it prefills to exist, and
    the box is hidden while a requirement is waiting to be pushed, so without
    this the button would look dead."""
    st.session_state["rc_demo_inject"] = text
    st.session_state["rc_demo_nonce"] = st.session_state.get("rc_demo_nonce", 0) + 1
    st.session_state["rc_refine_open"] = True


def _run_with_retry(func, *args, retries=1, **kwargs):
    """Run func(*args, **kwargs), retrying on failure (useful for flaky
    network/API calls). Re-raises the last error if all attempts fail.

    Duplicated verbatim from streamlit_app.py rather than shared - it's
    also called twice in that file's own (untouched) Job Finder path, so
    the original stays there too. Keep both in sync if this ever
    changes, or dedupe deliberately in a later phase."""
    last_error = None
    for attempt in range(retries + 1):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            last_error = e
            if attempt < retries:
                time.sleep(1)
    raise last_error


def _original_request_text(messages: list[dict]) -> str:
    """The user's own messages from this conversation, verbatim - the
    request exactly as they wrote it (plus any answers they gave to open
    questions), before product_manager summarized it into a user story.

    build_feature() passes this to software_engineer alongside the PM's
    requirements: the summary refers to exact content the request carried
    (e.g. the demo logo request embeds the precise inline SVG) without
    reproducing it, so without the verbatim text the engineer has no way
    to honour it and correctly refuses to invent its own."""
    return "\n\n".join(
        message["content"] for message in messages if message.get("role") == "user"
    )


def _format_pm_result(result) -> str:
    """Renders a FeatureRequirementsResult (see req2prod/Req2Prod.py) as the
    Product Manager's chat bubble content."""
    lines = ["**Senior Product Manager**", "", f"**User story:** {result.user_story}"]
    lines.append("")
    lines.append("**Acceptance criteria:**")
    for criterion in result.acceptance_criteria:
        lines.append(f"- {criterion}")
    if result.open_questions:
        lines.append("")
        lines.append("**Open questions:**")
        for question in result.open_questions:
            lines.append(f"- {question}")
    return "\n".join(lines)


def _format_architect_result(result) -> str:
    """Renders an ArchitectureDirectionResult (see req2prod/Req2Prod.py) as the
    Software Architect's chat bubble content."""
    lines = ["**Senior Software Architect**", ""]
    lines.append(
        "Builds on the existing app as-is."
        if result.builds_on_existing_app
        else "Needs new infrastructure or a separate service."
    )
    if result.new_infrastructure_needed:
        lines.append("")
        lines.append("**New infrastructure needed:**")
        for item in result.new_infrastructure_needed:
            lines.append(f"- {item}")
    if result.non_functional_requirements:
        lines.append("")
        lines.append("**Non-functional requirements:**")
        for item in result.non_functional_requirements:
            lines.append(f"- {item}")
    lines.append("")
    lines.append(f"**Technical notes:** {result.technical_notes}")
    if result.clarifications_needed:
        lines.append("")
        lines.append("**Clarifications needed:**")
        for item in result.clarifications_needed:
            lines.append(f"- {item}")
    return "\n".join(lines)


def _format_engineer_result(result) -> str:
    """Renders a FeatureBuildResult (see req2prod/Req2Prod.py) as the Software
    Engineer's chat bubble content."""
    lines = ["**Senior Software Engineer**", ""]
    # Lead with the outcome. The whole point of this step is that the
    # requirement is now a real pull request in review - but that was a bare
    # URL sitting above a long summary, so the one thing the user actually
    # needs to know ("it's been pushed for merge, and here's what happens
    # next") was the easiest thing to scroll past.
    if result.pr_url:
        lines.append(
            f"✅ **Pushed for review** — opened [pull request]({result.pr_url}). "
            "Nothing is merged yet: code_reviewer reviews it automatically, and it "
            "merges once it passes."
        )
    else:
        lines.append(
            "⚠️ **No pull request was opened** — this change has not been pushed "
            "for review, so nothing will ship from it."
        )
    lines.append("")
    lines.append(f"**Branch:** `{result.branch_name}`")
    lines.append("")
    lines.append(f"**Summary:** {result.summary}")
    if result.files_changed:
        lines.append("")
        lines.append("**Files changed:**")
        for path in result.files_changed:
            lines.append(f"- `{path}`")
    if result.questions_asked:
        lines.append("")
        # These are a record of software_engineer's own mid-build delegation to
        # the product_manager / software_architect *agents* (allow_delegation),
        # already asked and answered between them before this result existed -
        # not open questions waiting on Marco. The old wording ("Questions asked
        # while building") read as a prompt to him, so he answered in the chat
        # and was surprised it started a fresh requirements round with the PM
        # instead of reaching the engineer - which is by design, but the label
        # invited it.
        lines.append(
            "**Asked the PM/Architect agents while building** "
            "_(already answered between them — nothing needed from you)_:"
        )
        for question in result.questions_asked:
            lines.append(f"- {question}")
    return "\n".join(lines)


def _latest_ready_pair(
    messages: list[dict],
) -> tuple[FeatureRequirementsResult, ArchitectureDirectionResult] | None:
    """If the conversation's last two messages are a product_manager +
    software_architect pair that both came back ready_for_development,
    reconstructs and returns their structured results (for the "Push to
    Software Engineer" button) - otherwise None. Requiring them to be
    the literal last two messages means the button naturally disappears
    once a newer message is appended after them, whether that's a fresh
    user follow-up (superseding this verdict) or the engineer's own
    build result (so the same requirements can't be pushed twice)."""
    if len(messages) < 2:
        return None
    pm_message, architect_message = messages[-2], messages[-1]
    if pm_message.get("role") != "product_manager" or architect_message.get("role") != "software_architect":
        return None
    pm_data, architect_data = pm_message.get("data"), architect_message.get("data")
    if pm_data is None or architect_data is None:
        return None
    pm_result = FeatureRequirementsResult(**pm_data)
    architect_result = ArchitectureDirectionResult(**architect_data)
    if not (pm_result.ready_for_development and architect_result.ready_for_development):
        return None
    return pm_result, architect_result


def _format_conversation_for_agents(messages: list[dict]) -> str:
    """Turns the saved chat history into one text block for the
    requirements-challenge crew's feature_request input - each turn is
    labeled by speaker, so the agents see exactly what was already said
    and can treat a later user message as a follow-up/answer rather
    than a brand new unrelated request."""
    speaker_labels = {
        "user": "Marco",
        "product_manager": "Product Manager",
        "software_architect": "Software Architect",
    }
    parts = [
        f"{speaker_labels.get(message['role'], message['role'])}: {message['content']}"
        for message in messages
    ]
    return "\n\n".join(parts)


def _prefill_chat_input_if_requested() -> None:
    """If a demo button set rc_demo_inject, push that text into the real
    requirements st.chat_input box client-side, so the user reviews it and
    sends it with the same ↑ button as any typed message - the demo then
    follows the exact same flow as the rest of the page. st.chat_input has
    no server-side prefill, so we set the textarea's value in the browser
    using the same window.parent pattern already used for the admin-login
    autofocus in streamlit_app.py; the native value setter + input event is
    what makes Streamlit's React widget register the text (enabling its send
    button).

    We deliberately do NOT pop rc_demo_inject here: popping it in the same
    run tears the injecting iframe down before its async script runs. Instead
    the flag persists and a per-click nonce guards the browser so the text is
    injected exactly once per button press - later reruns re-render this
    (harmless) script but the nonce check skips re-injection, so it never
    clobbers whatever the user has since typed. The flag is cleared on submit
    (see the st.chat_input handler)."""
    text = st.session_state.get("rc_demo_inject")
    if not text:
        return
    nonce = st.session_state.get("rc_demo_nonce", 0)
    # json.dumps handles quotes/newlines; the </ -> <\/ guard keeps any
    # markup in the requirement from prematurely closing the <script> tag.
    payload = json.dumps(text).replace("</", "<\\/")
    st.components.v1.html(
        f"""
        <script>
        (function () {{
            try {{
                const nonce = {nonce};
                const w = window.parent;
                if (w.__r2pInjectNonce === nonce) return;  // already injected this click
                const ta = w.document.querySelector(
                    'textarea[placeholder="{_REQUIREMENT_INPUT_PLACEHOLDER}"]'
                );
                if (!ta) return;
                const setter = Object.getOwnPropertyDescriptor(
                    w.HTMLTextAreaElement.prototype, 'value'
                ).set;
                setter.call(ta, {payload});
                ta.dispatchEvent(new Event('input', {{ bubbles: true }}));
                ta.focus();
                w.__r2pInjectNonce = nonce;
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
    )


def _latest_build_result(messages: list[dict]) -> dict | None:
    """The engineer's build result, if opening the pull request is the most
    recent thing that happened - so the "view it in the Pipeline" button shows
    up the moment the merge request exists and goes away again as soon as
    anything else is said. Same last-message rule as _latest_ready_pair.

    Only a successful build carries "data" (a failure appends a plain warning
    string instead), so its presence is exactly the difference between "there
    is a PR worth going to look at" and "the build produced nothing"."""
    if not messages:
        return None
    last = messages[-1]
    if last.get("role") != "software_engineer":
        return None
    return last.get("data")


def _jump_to_pipeline_tab_if_requested() -> None:
    """If the "view it in the Pipeline" button was clicked, actually switch to
    that tab - by clicking it in the browser, because there is no other way.

    st.tabs doesn't surface the active tab as state and offers no API to
    select one, so this can't be done from Python at all. The only lever is
    the rendered DOM, via the same window.parent pattern
    _prefill_chat_input_if_requested and streamlit_app.py's admin-login
    autofocus already use. Streamlit renders each tab as
    `button[role="tab"]` whose innerText is the label (verified against
    Streamlit 1.58, which is pinned in requirements.txt) - matching on
    PIPELINE_TAB_LABEL rather than a hardcoded string is what keeps this
    working if the tab is ever renamed.

    Same no-pop + nonce discipline as _prefill_chat_input_if_requested, and
    for the same reason spelled out there: clearing the flag in this run
    would tear this iframe down before its own async script gets to run. The
    nonce instead makes the browser do it exactly once per click, so an
    ordinary rerun re-renders this (harmless) script and skips. That
    guard matters more here than for the prefill - this tab's reruns are
    live crew calls, and a tab switch firing on every one of them is the
    same class of interruption render_req2prod_pipeline_tab's comment
    describes cancelling crew runs in production."""
    if not st.session_state.get("rc_jump_to_pipeline"):
        return
    nonce = st.session_state.get("rc_jump_nonce", 0)
    label = json.dumps(PIPELINE_TAB_LABEL).replace("</", "<\\/")
    st.components.v1.html(
        f"""
        <script>
        (function () {{
            try {{
                const nonce = {nonce};
                const w = window.parent;
                if (w.__r2pJumpNonce === nonce) return;  // already jumped for this click
                w.__r2pJumpNonce = nonce;
                const label = {label};
                for (const tab of w.document.querySelectorAll('button[role="tab"]')) {{
                    if ((tab.innerText || "").trim() === label) {{ tab.click(); return; }}
                }}
            }} catch (e) {{}}
        }})();
        </script>
        """,
        height=0,
    )


def _submit_requirement(text: str) -> None:
    """Feed one feature-request message into the requirements challenge and
    render the agents' response. Shared entry point for both the free-form
    st.chat_input below and the demo prefill "Send to agents" button - the
    body is the former inline st.chat_input handler, extracted verbatim
    (prompt -> text) so both paths behave identically."""
    if not st.session_state.get("rc_session_id"):
        st.session_state["rc_session_id"] = new_session_id()
    session_id = st.session_state["rc_session_id"]

    messages = st.session_state.get("rc_messages", [])
    messages.append({"role": "user", "content": text})
    st.session_state["rc_messages"] = messages
    save_session(session_id, messages)

    result = None
    error = None
    # See the "Push to Software Engineer" handler for why save_session()
    # runs inside this `with` block rather than after it - st.spinner()'s
    # __exit__ is where a disconnected client would abort the script via a
    # BaseException our `except Exception` below can't catch, silently
    # losing the result. TEMPORARY [DIAG] prints, see that handler for why
    # they log only presence/shape and never actual user/agent content.
    print(f"[DIAG] challenge_requirement starting, session={session_id}", flush=True)
    with st.spinner("✨ Magic is happening, please wait..."):
        try:
            result = _run_with_retry(
                challenge_requirement, _format_conversation_for_agents(messages)
            )
            print(f"[DIAG] challenge_requirement returned, got_result={result is not None}", flush=True)
        except Exception as e:
            error = f"The requirements challenge failed: {e}"
            print(f"[DIAG] challenge_requirement raised: {type(e).__name__}", flush=True)

        if error is not None or result is None:
            messages.append(
                {
                    "role": "product_manager",
                    "content": f"⚠️ {error or 'Something went wrong and no response was produced.'} "
                    "Try rephrasing or resubmitting.",
                }
            )
        else:
            pm_result, architect_result = result
            messages.append(
                {
                    "role": "product_manager",
                    "content": _format_pm_result(pm_result),
                    "data": pm_result.model_dump(),
                }
            )
            messages.append(
                {
                    "role": "software_architect",
                    "content": _format_architect_result(architect_result),
                    "data": architect_result.model_dump(),
                }
            )

        # See the "Push to Software Engineer" handler for why save_session()
        # now runs before the st.session_state write.
        save_session(session_id, messages)
        print(f"[DIAG] save_session done, session={session_id}", flush=True)
        st.session_state["rc_messages"] = messages

    print("[DIAG] spinner block exited cleanly, about to st.rerun()", flush=True)
    st.rerun()


def render_requirements_tab() -> None:
    """Admin-only sub-page where Marco types a raw feature idea and gets
    it challenged by the product_manager and software_architect agents
    (req2prod/Req2Prod.py's challenge_requirement) - chat layout modeled on
    Claude Code's own UI: message history on top, input pinned to the
    bottom. No session list/switcher UI - a session id is created
    automatically the moment the first message is sent (see the
    st.chat_input handler below) and persists for the life of this
    browser tab's Streamlit session; still saved to a JSON file under
    data/requirements_sessions/ (req2prod/requirements_sessions.py) so
    a run isn't lost if the page reruns mid-conversation.

    Renamed from streamlit_app.py's _render_requirements_challenge_page -
    body otherwise unchanged, including the [DIAG] print statements and
    the deploy-mode-resync handling below (both address real production
    issues, not cleanup candidates)."""
    st.markdown(
        '<div class="hero-badge">✨ Powered by AI agents</div>'
        '<div class="hero-title" style="font-size:1.8rem;">Request a New Feature</div>'
        '<div class="hero-tagline">Describe a feature idea - the Senior Product Manager and '
        "Senior Software Architect agents will challenge it before a line of code gets written."
        "</div>",
        unsafe_allow_html=True,
    )

    # --- Demo prefill buttons ---------------------------------------------
    # Seed (never auto-run) a ready-made feature request that has Req2Prod add
    # or remove the req2prod logo on the main Job Finder page. Clicking a button
    # only drops the request text into the normal chat input at the bottom of
    # the page (see _prefill_chat_input_if_requested) - the human reviews/edits
    # it and hits the same ↑ send button as any typed message, so the demo runs
    # through the exact same flow as the rest of the page (challenge -> Push to
    # Software Engineer -> PR -> merge -> deploy). Nothing runs on this click.
    #
    # Behind a toggle, off by default: these are presentation props, and this
    # is the page for describing a real feature. A toggle rather than a tab of
    # their own because every st.tabs() panel's code runs on every rerun
    # whether or not it's the visible one, so a second tab rendering this same
    # page would put every widget on it in the script twice - two chat inputs,
    # two "Push to Software Engineer" buttons with the same key. One page,
    # one set of widgets, and the props appear when they're wanted.
    demo_mode = st.toggle(
        "🎬 Demo mode",
        key="rc_demo_mode",
        help="Show the ready-made demo requests. Off for ordinary use.",
    )
    if demo_mode:
        st.caption(
            "🎬 Demo — prefill a ready-made request into the box below, then send it like any other:"
        )
        demo_add_col, demo_remove_col = st.columns(2)
        # Bump a nonce on each click so the browser injects the fresh text exactly
        # once (see _prefill_chat_input_if_requested's nonce guard).
        if demo_add_col.button("➕ Demo: Add Req2Prod Logo", key="rc_demo_add"):
            _seed_demo_request(DEMO_ADD_LOGO_REQUIREMENT)
        if demo_remove_col.button("➖ Demo: Remove Req2Prod Logo", key="rc_demo_remove"):
            _seed_demo_request(DEMO_REMOVE_LOGO_REQUIREMENT)

    current_deploy_mode = get_auto_deploy_mode()
    is_live_deploy_mode = current_deploy_mode is not None
    # Single-use: popped here every render (whether or not it ends up being
    # used) so a value from one successful toggle can never sit around and
    # get mistaken for a live read on some later, unrelated fetch failure.
    stale_confirmed_deploy_mode = st.session_state.pop("_rc_deploy_mode_confirmed", None)
    if current_deploy_mode is None:
        # Immediately after our own successful set_auto_deploy_mode() call
        # below, we rerun to refresh this section - if that immediate
        # re-fetch hits a transient failure (timeout, rate limit), fall back
        # to the value we just confirmed we wrote instead of hiding the
        # whole toggle behind the warning right after the user's own click.
        current_deploy_mode = stale_confirmed_deploy_mode
    if current_deploy_mode is None:
        st.caption(
            "⚠️ Can't read the current deploy workflow mode - GITHUB_VARIABLES_TOKEN is "
            "either missing or doesn't have permission to read repo Actions variables."
        )
    else:
        if not is_live_deploy_mode:
            # This is the single-use fallback, not a fresh read - never treat
            # it as authoritative for resync/write-back purposes below, so a
            # stale value can't force the toggle or get written back to
            # GitHub over someone else's real, external change.
            st.caption(
                "⚠️ Showing the value from this session's last successful toggle - "
                "the live read just failed, so this may not reflect a change made "
                "elsewhere since then."
            )
        else:
            # st.toggle's own cached state (via its key) persists across
            # reruns/page loads within a session, independent of value= -
            # value= only seeds it on first render. If AUTO_DEPLOY_ON_MERGE
            # changes through anything other than this exact toggle (the
            # desktop "Toggle Demo Mode" icon, editing the GitHub variable
            # directly), a stale cached state can silently disagree with
            # the freshly-fetched current_deploy_mode - and the mismatch
            # check below would then treat that staleness as if it were a
            # fresh user click, silently pushing the stale cached value
            # back to GitHub and reverting someone else's change. Observed
            # live (2026-07-12): the caption and the toggle disagreed after
            # an external change, and GitHub's real value had already been
            # overwritten back to the toggle's stale position. Resyncing
            # here, before the widget renders, whenever the external value
            # has moved since we last saw it, closes that gap. Gated on
            # is_live_deploy_mode so this only ever runs off a real fetch,
            # never off the single-use fallback above.
            if st.session_state.get("_rc_deploy_mode_last_seen") != current_deploy_mode:
                st.session_state["rc_auto_deploy_toggle"] = current_deploy_mode
            st.session_state["_rc_deploy_mode_last_seen"] = current_deploy_mode

        st.caption(
            f"Current value: **{'ON' if current_deploy_mode else 'OFF'}** "
            f"(`AUTO_DEPLOY_ON_MERGE={'true' if current_deploy_mode else 'false'}` on GitHub)"
        )
        toggled_deploy_mode = st.toggle(
            "🚀 Automated deploy on merge",
            key="rc_auto_deploy_toggle",
            help="Same switch as the 'Toggle Demo Mode' desktop icon. Off (default): "
            "deploying to production after a merge still needs a manual 'Run workflow' "
            "click. On: merging a PR into main auto-deploys straight to production.",
        )
        if is_live_deploy_mode and toggled_deploy_mode != current_deploy_mode:
            # Rerun after applying the change so the "Current value"
            # caption above (rendered with the pre-toggle value) re-reads
            # the freshly-applied state - otherwise it keeps showing the
            # old value until the next interaction, which reads like the
            # toggle didn't take. On failure we also snap the toggle back
            # to the real state.
            if not set_auto_deploy_mode(toggled_deploy_mode):
                st.error(
                    "Couldn't update the deploy mode - check GITHUB_VARIABLES_TOKEN's "
                    "permissions."
                )
                st.session_state["rc_auto_deploy_toggle"] = current_deploy_mode
                st.session_state["_rc_deploy_mode_last_seen"] = current_deploy_mode
                st.rerun()
            else:
                st.session_state["_rc_deploy_mode_last_seen"] = toggled_deploy_mode
                # Recorded so the immediate re-fetch on rerun can fall back
                # to this confirmed value if it transiently fails.
                st.session_state["_rc_deploy_mode_confirmed"] = toggled_deploy_mode
                # Rerun so the "Current value" caption above re-reads the
                # freshly-applied value, instead of continuing to show the
                # pre-toggle state until the next interaction.
                st.rerun()

    messages = st.session_state.get("rc_messages", [])

    avatars = {
        "user": "🧑‍💼",
        "product_manager": "📋",
        "software_architect": "🏗️",
        "software_engineer": "👷",
    }
    for message in messages:
        role = "user" if message["role"] == "user" else "assistant"
        with st.chat_message(role, avatar=avatars.get(message["role"])):
            st.markdown(message["content"])

    ready_pair = _latest_ready_pair(messages)
    # Once both agents are ready the page is asking one question - push it, or
    # don't - so the "describe a feature" box below stops applying and only
    # competes with the answer. It stays hidden until either the requirement
    # is pushed (the engineer's reply makes ready_pair None again) or "Add
    # something first" deliberately asks for it back. That escape isn't
    # optional: st.chat_input is the only way to add a message at all, there
    # is no session switcher to start over with (see this function's
    # docstring), and the whole point of this half of the pipeline is that
    # Marco can still say "no, also do X" after both agents have agreed.
    awaiting_push = ready_pair is not None and not st.session_state.get("rc_refine_open")
    if ready_pair is not None:
        st.success("Both agents have confirmed this is ready for development.")
        if awaiting_push:
            _, refine_col = st.columns([2, 1])
            if refine_col.button(
                "✏️ Add something first", key="rc_refine", use_container_width=True
            ):
                st.session_state["rc_refine_open"] = True
                st.rerun()
        if st.button("🚀 Push to Software Engineer", key="rc_push_to_engineer", type="primary"):
            pm_result, architect_result = ready_pair
            session_id = st.session_state["rc_session_id"]
            build_result = None
            error = None
            # TEMPORARY diagnostic logging (see [DIAG] markers below),
            # to be removed once the actual gap is found - PR #22 moved
            # save_session() inside this `with` block on the theory
            # that st.spinner()'s __exit__ was the only point a
            # disconnected client's BaseException could abort the
            # script before the result was saved. That fix is deployed
            # but a session was still observed on production where the
            # crew genuinely completed (per journalctl) yet
            # save_session() never ran - meaning that theory was
            # incomplete. These prints pin down how far execution gets
            # next time, logging only presence/shape, never the actual
            # build content (which can include user-submitted feature
            # requests and generated code) - journalctl is a broader-
            # access surface than this app's per-user file storage.
            print(f"[DIAG] rc_push_to_engineer clicked, session={session_id}", flush=True)
            with st.spinner("👷 Software Engineer is building this feature..."):
                try:
                    build_result = _run_with_retry(
                        build_feature,
                        pm_result,
                        architect_result,
                        _original_request_text(messages),
                    )
                    print(
                        "[DIAG] build_feature returned, "
                        f"got_result={build_result is not None} "
                        f"kind={type(build_result).__name__}",
                        flush=True,
                    )
                except Exception as e:
                    error = f"The build failed: {e}"
                    print(f"[DIAG] build_feature raised: {type(e).__name__}", flush=True)

                if isinstance(build_result, EngineerQuestion):
                    # The engineer had something to say rather than something
                    # to build - show what it said. This used to arrive as
                    # None and render as "Something went wrong", which is how
                    # "the feature you asked for already exists" came out
                    # looking like a malfunction (2026-07-16).
                    messages.append(
                        {
                            "role": "software_engineer",
                            "content": f"💬 **The engineer has a question rather than a build:**\n\n{build_result.question}",
                        }
                    )
                elif error is not None or build_result is None:
                    messages.append(
                        {
                            "role": "software_engineer",
                            "content": f"⚠️ {error or 'Something went wrong and no build result was produced.'}",
                        }
                    )
                else:
                    messages.append(
                        {
                            "role": "software_engineer",
                            "content": _format_engineer_result(build_result),
                            "data": build_result.model_dump(),
                        }
                    )

                # save_session() (plain file I/O, no Streamlit API) now
                # runs before the st.session_state write, not after -
                # observed on production: even with the append/save code
                # already moved inside this `with` block (see the long
                # comment above), a run died between the "build_feature
                # returned" print and "save_session done" ever logging,
                # meaning st.session_state.__setitem__ itself - not just
                # st.spinner()'s __exit__ - can be where a disconnected
                # client's BaseException fires. save_session() has no
                # such risk since it never touches Streamlit at all.
                save_session(session_id, messages)
                print(f"[DIAG] save_session done, session={session_id}", flush=True)
                st.session_state["rc_messages"] = messages

            print("[DIAG] spinner block exited cleanly, about to st.rerun()", flush=True)
            st.rerun()

    # Shown the moment the merge request exists, and only while it's still the
    # latest thing that happened - the Pipeline tab reports on the most
    # recently active PR, so this is exactly the window in which going there
    # shows the PR just opened rather than someone else's.
    if _latest_build_result(messages) is not None:
        if st.button("View it in the Pipeline →", key="rc_view_in_pipeline", type="primary"):
            st.session_state["rc_jump_to_pipeline"] = True
            st.session_state["rc_jump_nonce"] = st.session_state.get("rc_jump_nonce", 0) + 1
            st.rerun()

    prompt = None if awaiting_push else st.chat_input(_REQUIREMENT_INPUT_PLACEHOLDER)
    # A demo button click lands here (same run) and injects its text into the
    # chat_input above; the user then sends it exactly like a typed message.
    # Skipped while the box is hidden - the injector needs a real textarea to
    # write into and silently gives up without one, so this would be a no-op
    # that looks like a broken button. The demo buttons ask for the box back
    # rather than being hidden themselves, so this only skips a click that
    # can't happen.
    if not awaiting_push:
        _prefill_chat_input_if_requested()
    _jump_to_pipeline_tab_if_requested()
    if prompt:
        # Whether typed or demo-prefilled, the requirement now goes out through
        # this one path - clear the demo flag so it can't re-inject afterwards.
        st.session_state.pop("rc_demo_inject", None)
        # Same for the pipeline jump: a new requirement means the build result
        # it belonged to is no longer the latest thing said, so the flag has
        # served its purpose and must not survive into an unrelated run.
        st.session_state.pop("rc_jump_to_pipeline", None)
        # And for the refine escape: this message is what it was opened for,
        # and it makes ready_pair None on its own, so leaving it set would keep
        # the box open through the next ready verdict too.
        st.session_state.pop("rc_refine_open", None)
        _submit_requirement(prompt)


def render_req2prod_pipeline_tab() -> None:
    """The "Req2Prod Pipeline" admin tab: a live, read-only view of the most
    recently active pull request's real journey through the review
    pipeline, plus live agent activity for the deploy job and
    devops_agent's auto-fix runs. Mechanical extraction of
    streamlit_app.py's former `with tab_req2prod:` body - no logic changes."""
    st.caption(
        "Live view of the most recently active pull request's real "
        "journey through the review pipeline - Merge Request, each "
        "actual Code Review round, looping back on rework, and Push "
        "to Master once merged. Stops there: the deploy pipeline "
        "isn't tied to a specific PR yet, so it isn't stitched on. "
        "Read-only: nothing here can trigger, cancel, or re-run "
        "anything."
    )
    # Not auto-polling on a timer: every st.tabs() panel's code runs
    # on every script rerun regardless of which tab is visually
    # active (Streamlit doesn't skip hidden tabs), so a timer armed
    # here would keep ticking client-side even while a different
    # tab (e.g. Request a New Feature) is mid-crew-call, and firing
    # it there forces Streamlit to cancel that run before it can
    # save its result - exactly what happened in production. A
    # manual button avoids that risk entirely.
    if st.button("🔄 Refresh", key="req2prod_flow_refresh"):
        st.rerun()

    try:
        pr_info, stages, flow_error = get_latest_pr_flow()
    except Exception:
        pr_info, stages, flow_error = None, [], "error"

    if flow_error == "no_token":
        st.info("GITHUB_ACTIONS_TOKEN isn't configured - set it in .env to enable this tab.")
    elif flow_error in ("unreachable", "error"):
        st.warning("Couldn't reach the GitHub API just now - it'll retry automatically.")
    elif not stages:
        st.info("No pull requests found yet.")
    else:
        st.markdown(f"**#{pr_info['number']}** — {pr_info['title']} ([view PR]({pr_info['url']}))")
        st.markdown(render_pr_flow_svg(stages), unsafe_allow_html=True)

    st.markdown("#### Live Req2Prod agent activity")
    st.caption(
        "Real-time step status for prod_tester/rollback_agent (inside "
        "the deploy job) and devops_agent's own auto-fix runs. GitHub "
        "can't stream a job's log while it's still running, so the "
        "step list below is genuinely live, but each job's full agent "
        "trace (what it actually reasoned/did) only appears once that "
        "job finishes."
    )
    try:
        agent_runs, agent_error = get_agent_activity()
    except Exception:
        agent_runs, agent_error = [], "error"

    if agent_error == "no_token":
        st.info("GITHUB_ACTIONS_TOKEN isn't configured - set it in .env to enable this.")
    elif agent_error in ("unreachable", "error"):
        st.warning("Couldn't reach the GitHub API just now - it'll retry automatically.")
    elif not agent_runs:
        st.info("No agent runs found yet.")
    else:
        step_icons = {"completed": "✅", "in_progress": "🔄", "queued": "⏳"}
        for run in agent_runs:
            conclusion_suffix = f", {run['run_conclusion']}" if run["run_conclusion"] else ""
            st.markdown(
                f"**{run['workflow']}** — [run #{run['run_id']}]({run['run_url']}) "
                f"({run['run_status']}{conclusion_suffix})"
            )
            for step in run["steps"]:
                icon = step_icons.get(step["status"], "•")
                if step["conclusion"] == "failure":
                    icon = "❌"
                st.caption(f"{icon} {step['name']}")
            if run.get("trace"):
                with st.expander("Agent trace"):
                    st.code(run["trace"], language=None)
            st.markdown("---")


# --- Documentation tab ---------------------------------------------------
# The public site, published from this repo's own site/ tree - the deploy
# syncs that whole directory to the server, so these URLs always serve the
# same commit the app itself is running.
REQ2PROD_SITE_URL = "https://req2prod.nl"
REQ2PROD_DETAILS_URL = f"{REQ2PROD_SITE_URL}/details.html"


def render_documentation_tab() -> None:
    """The "Documentation" admin tab: links out to the public req2prod.nl site.

    This embedded both pages in iframes until the site had a public URL worth
    pointing at - req2prod.nl was still a parked domain when the tab was
    written, so there was nowhere to send anyone. Embedding was the worse
    option even then, and measurably so once it could be measured:
    components.html cannot size an iframe to its content, so both pages sat
    behind nested scrollbars (details.html renders 6514px of content, which
    no fixed height guesses correctly), and an 820px standalone page inside a
    704px admin column reads as cramped at any height. Self-sizing the frames
    fixes the scrollbars but needs Streamlit's own stElementContainer resized
    too - private DOM internals, one upgrade away from breaking - and still
    leaves two full marketing pages, heroes and footers and all, stacked
    inside an admin tab.

    Linking is simpler and can't drift from what's published, because it is
    what's published. st.link_button opens a new tab, so the admin session
    (and any crew call running in it) survives the click.
    """
    st.markdown("### req2prod.nl")
    st.caption(
        "The public site, published straight from this repo's site/ directory "
        "- the deploy syncs it, so what's live there is what's in this commit. "
        "Opens in a new tab."
    )
    overview, details = st.columns(2)
    with overview:
        st.link_button(
            "Overview ↗", REQ2PROD_SITE_URL, type="primary", use_container_width=True
        )
        st.caption("What Req2Prod is: the agent crew, the feedback loops, the model tiering.")
    with details:
        st.link_button("How it works ↗", REQ2PROD_DETAILS_URL, use_container_width=True)
        st.caption(
            "The full path a change takes, from a typed requirement to a verified deploy."
        )


_AGENTS_YAML_PATH = Path(__file__).parent / "config" / "agents.yaml"
_LESSONS_DIR = Path(__file__).parent / "lessons"


def _load_clean_agent_identity(agent_key: str) -> dict:
    """role/backstory straight from agents.yaml, read fresh (not
    req2prod.Req2Prod's own module-level agents_config) - that copy has
    its backstory mutated in place at import time to append lessons
    text (see Req2Prod.py's _augment_backstories_with_lessons), so
    importing it here would show the same lessons text twice: once
    folded into "Backstory", once again in the "Lessons" section
    below. Reading the file independently keeps the two genuinely
    separate. Read on every render (not cached), matching this app's
    existing "live" convention for admin pages (e.g. cto_cockpit_admin.py's
    filesystem reads) - agents.yaml changes should show up without a
    restart."""
    with open(_AGENTS_YAML_PATH) as f:
        config = yaml.safe_load(f)
    return config[agent_key]


def _load_lessons(agent_key: str) -> str | None:
    """Raw markdown from req2prod/lessons/<agent_key>.md, or None if
    that agent has no lessons file (yet)."""
    path = _LESSONS_DIR / f"{agent_key}.md"
    if not path.exists():
        return None
    text = path.read_text().strip()
    return text or None


def _render_agent_model_table(agent_keys: list[str], widget_key_prefix: str) -> None:
    """Renders one editable Agent/API model/Subscription model/Why table
    for the given agent_keys (see AGENT_DISPLAY_NAMES in
    req2prod/model_registry.py) on the admin "AI Models" tab, with its own
    save button. Each agent has two independent model assignments - one
    per backend (see RECOMMENDATIONS' own module comment for why the two
    recommendations differ: cost is a real constraint on the API, not on
    a flat-rate subscription). widget_key_prefix keeps this group's
    Streamlit widget keys distinct from any other group rendered on the
    same page."""
    current_models = load_agent_models()
    display_to_model_id = {label: model_id for model_id, label in MODEL_DISPLAY_NAMES.items()}

    rows = []
    for agent_key in agent_keys:
        api_recommended_id, api_rationale = RECOMMENDATIONS[agent_key]["api"]
        sub_recommended_id, sub_rationale = RECOMMENDATIONS[agent_key]["subscription"]
        api_label = MODEL_DISPLAY_NAMES[current_models[agent_key]["api"]]
        sub_label = MODEL_DISPLAY_NAMES[current_models[agent_key]["subscription"]]
        rows.append(
            {
                "Agent": AGENT_DISPLAY_NAMES[agent_key],
                "API model": api_label,
                "Recommended (API)": MODEL_DISPLAY_NAMES[api_recommended_id],
                "Subscription model": sub_label,
                "Recommended (Subscription)": MODEL_DISPLAY_NAMES[sub_recommended_id],
                "Why": f"API: {api_rationale}\n\nSubscription: {sub_rationale}",
            }
        )

    edited_rows = st.data_editor(
        rows,
        column_config={
            "Why": st.column_config.TextColumn(width="large"),
            "API model": st.column_config.SelectboxColumn(
                options=list(MODEL_DISPLAY_NAMES.values()), required=True
            ),
            "Subscription model": st.column_config.SelectboxColumn(
                options=list(MODEL_DISPLAY_NAMES.values()), required=True
            ),
        },
        disabled=["Agent", "Recommended (API)", "Recommended (Subscription)", "Why"],
        use_container_width=True,
        hide_index=True,
        # Tall enough to wrap the longest "Why" text (the API+Subscription
        # rationale for pr_fix_agent/pr_arbiter, ~450 characters) fully into
        # view at the "large" column width above, rather than clipping it
        # to one line and requiring a click into the cell to read the
        # rest. Shorter agents' rows get the same fixed height with blank
        # space below the wrapped text - st.data_editor only supports one
        # uniform row_height for the whole grid, not per-row sizing.
        row_height=260,
        key=f"{widget_key_prefix}_editor",
    )

    if st.button("Save model changes", key=f"{widget_key_prefix}_save"):
        changed = 0
        for agent_key, edited in zip(agent_keys, edited_rows):
            new_api_id = display_to_model_id[edited["API model"]]
            if new_api_id != current_models[agent_key]["api"]:
                set_agent_model(agent_key, new_api_id, "api")
                changed += 1
            new_sub_id = display_to_model_id[edited["Subscription model"]]
            if new_sub_id != current_models[agent_key]["subscription"]:
                set_agent_model(agent_key, new_sub_id, "subscription")
                changed += 1
        if changed:
            st.success(f"Updated {changed} model assignment(s) - takes effect immediately, no restart needed.")
            st.rerun()
        else:
            st.info("No changes to save.")


def render_ai_models_tab() -> None:
    """The "AI Models" admin tab: per-agent API/Subscription model
    pickers plus the AGENT_BACKEND CI toggle, grouped by product
    (Job Finder / Req2Prod) so it's clear which agents belong to which.
    Only Req2Prod's agents are actually editable here - Job Finder's
    own agents (job_finder, company_researcher, resume_tailor,
    resume_reviewer, resume_formatter in job_search.py) pick their
    model per-user tier, a separate mechanism entirely, adjusted from
    the Jobfinder Admin tab instead. CTO Cockpit has no agents at all,
    so it isn't shown here."""
    st.caption(
        "[Top up Anthropic credits / check real balance](https://platform.claude.com/dashboard)"
    )

    st.markdown("### Job Finder")
    st.caption(
        "Job Finder's own agents (Job Finder, Company Researcher, "
        "Resume Tailor, Resume Reviewer, Resume Formatter) pick their "
        "model per-user tier, not here - adjust that from the "
        "**Jobfinder Admin** tab's per-user Tier column."
    )

    st.divider()

    st.markdown("### Req2Prod")
    st.caption(
        "Every Req2Prod agent has two independent model assignments "
        "below - one for the API, one for a Claude subscription - "
        "each with its own recommendation (see "
        "req2prod/model_registry.py): cost per call is a real "
        "constraint on the API, but not on a flat-rate "
        "subscription, so the two don't always agree. Changes "
        "apply immediately and are saved so they survive the next "
        "restart."
    )
    current_agent_backend = get_agent_backend()
    if current_agent_backend is None:
        st.caption(
            "⚠️ Can't read the current CI agent backend - "
            "GITHUB_VARIABLES_TOKEN is either missing or doesn't "
            "have permission to read repo Actions variables."
        )
    else:
        is_subscription = current_agent_backend == "subscription"
        st.caption(
            f"GitHub Actions CI is currently on: "
            f"**{'Subscription' if is_subscription else 'API'}** "
            f"(`AGENT_BACKEND={current_agent_backend}` on GitHub)"
        )
        toggled_to_subscription = st.toggle(
            "🧑‍💻 Run CI agents on Marco's Claude subscription",
            value=is_subscription,
            key="agent_backend_toggle",
            help="Off (default): every Req2Prod agent in GitHub Actions "
            "runs against the metered Anthropic API, as normal. On: "
            "those same agents run instead as local `claude -p` "
            "calls on a self-hosted runner (Marco's own laptop, "
            "logged in via `claude login`) - only takes effect for "
            "his own pushes/PRs, never a fork's, and only while "
            "that runner is online. Meant for active testing "
            "sessions, not left on permanently.",
        )
        if toggled_to_subscription != is_subscription:
            new_value = "subscription" if toggled_to_subscription else "api"
            if not set_agent_backend(new_value):
                st.error(
                    "Couldn't update the CI agent backend - check "
                    "GITHUB_VARIABLES_TOKEN's permissions."
                )
                st.session_state["agent_backend_toggle"] = is_subscription
                st.rerun()

    app_agent_keys = [
        key for key in AGENT_DISPLAY_NAMES if key not in TECH_EXCELLENCE_AGENT_KEYS
    ]

    st.markdown("#### Req2Prod pipeline agents")
    st.caption("Called by this app itself, as part of its own Req2Prod pipeline.")
    _render_agent_model_table(app_agent_keys, "app_agents")

    st.divider()

    st.markdown("#### Technology Excellence panel")
    st.caption(
        "Only ever invoked from a Claude Code session running the "
        "pre-publish readiness review (req2prod/Req2Prod.py's "
        "technology_excellence_crew) - never called by this "
        "deployed app itself."
    )
    _render_agent_model_table(TECH_EXCELLENCE_AGENT_KEYS, "tech_excellence_agents")

    st.markdown("##### How this crew is built")
    st.caption(
        "Role, backstory, and lessons read live from "
        "req2prod/config/agents.yaml and req2prod/lessons/ - not a "
        "static writeup, so this always matches what each persona "
        "actually runs with. Skills (which tools each persona can "
        "call) isn't wired in here yet - coming soon."
    )
    for agent_key in TECH_EXCELLENCE_AGENT_KEYS:
        cfg = _load_clean_agent_identity(agent_key)
        lessons = _load_lessons(agent_key)
        with st.expander(AGENT_DISPLAY_NAMES[agent_key]):
            st.markdown(f"**Role**\n\n{cfg['role'].strip()}")
            st.markdown(f"**Backstory**\n\n{cfg['backstory'].strip()}")
            st.markdown("**Lessons**")
            if lessons:
                st.markdown(lessons)
            else:
                st.caption("None yet.")
            st.caption("Skills: coming soon")
