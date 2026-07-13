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

import time

import streamlit as st

from req2prod.Req2Prod import (
    ArchitectureDirectionResult,
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
    lines = ["**Senior Software Engineer**", "", f"**Branch:** `{result.branch_name}`"]
    if result.pr_url:
        lines.append(f"**Pull request:** {result.pr_url}")
    lines.append("")
    lines.append(f"**Summary:** {result.summary}")
    if result.files_changed:
        lines.append("")
        lines.append("**Files changed:**")
        for path in result.files_changed:
            lines.append(f"- `{path}`")
    if result.questions_asked:
        lines.append("")
        lines.append("**Questions asked while building:**")
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

    current_deploy_mode = get_auto_deploy_mode()
    if current_deploy_mode is None:
        st.caption(
            "⚠️ Can't read the current deploy workflow mode - GITHUB_VARIABLES_TOKEN is "
            "either missing or doesn't have permission to read repo Actions variables."
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
        # has moved since we last saw it, closes that gap.
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
        if toggled_deploy_mode != current_deploy_mode:
            # Streamlit already reran once to deliver this toggle
            # interaction, and the widget's own state already reflects
            # the user's choice - no need to force a second rerun (and
            # a second get_auto_deploy_mode() API call) on success. Only
            # force one on failure, to snap the toggle back to the real
            # state instead of leaving it showing a value that was never
            # actually applied.
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
    if ready_pair is not None:
        st.success("Both agents have confirmed this is ready for development.")
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
                    build_result = _run_with_retry(build_feature, pm_result, architect_result)
                    print(f"[DIAG] build_feature returned, got_result={build_result is not None}", flush=True)
                except Exception as e:
                    error = f"The build failed: {e}"
                    print(f"[DIAG] build_feature raised: {type(e).__name__}", flush=True)

                if error is not None or build_result is None:
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

    prompt = st.chat_input("Describe a feature, or answer the open questions above...")
    if prompt:
        if not st.session_state.get("rc_session_id"):
            st.session_state["rc_session_id"] = new_session_id()
        session_id = st.session_state["rc_session_id"]

        messages = st.session_state.get("rc_messages", [])
        messages.append({"role": "user", "content": prompt})
        st.session_state["rc_messages"] = messages
        save_session(session_id, messages)

        result = None
        error = None
        # See the "Push to Software Engineer" handler above for why
        # save_session() runs inside this `with` block rather than after
        # it - st.spinner()'s __exit__ is where a disconnected client
        # would abort the script via a BaseException our `except
        # Exception` below can't catch, silently losing the result.
        # TEMPORARY [DIAG] prints, see that handler for why they log
        # only presence/shape and never actual user/agent content.
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

            # See the "Push to Software Engineer" handler above for why
            # save_session() now runs before the st.session_state write.
            save_session(session_id, messages)
            print(f"[DIAG] save_session done, session={session_id}", flush=True)
            st.session_state["rc_messages"] = messages

        print("[DIAG] spinner block exited cleanly, about to st.rerun()", flush=True)
        st.rerun()


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
    (Job Finder / Req2Prod / CTO Cockpit - same three products as the
    CTO Cockpit tab's live architecture diagram) so it's clear which
    agents belong to which. Only Req2Prod's agents are actually
    editable here - Job Finder's own agents (job_finder,
    company_researcher, resume_tailor, resume_reviewer,
    resume_formatter in job_search.py) pick their model per-user tier,
    a separate mechanism entirely, adjusted from the Jobfinder Admin
    tab instead. CTO Cockpit has no agents yet."""
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

    st.divider()

    st.markdown("### CTO Cockpit")
    st.caption("Not yet built - no agents yet.")
