"""
AI viewer mode - a transparency pane that shows, in real time, every
step (tool call, tool result, final answer) each CrewAI agent takes
while it works. Off by default; toggled in the sidebar.

Kept separate from job_search.py so that file stays UI-agnostic (its
own terminal test path runs with no Streamlit context at all) - this
module owns everything Streamlit-specific: the toggle, the split
layout, and the event-bus listeners that feed the live log.

Why the event bus and not CrewAI's step_callback: step_callback only
fires once per task, with the agent's final answer - this CrewAI
version's tool-calling path (native tool use) doesn't invoke it per
tool call. crewai.events fires ToolUsageStartedEvent/FinishedEvent
around every real tool call, globally, for any crew running anywhere
in the process - so these listeners are registered exactly once, here,
rather than threaded through every job_search.py function.

Why the explicit ScriptRunContext handling: CrewAI dispatches
synchronous event handlers on its own ThreadPoolExecutor, not the
thread Streamlit is running this user's script on - st.session_state
needs that thread's context attached explicitly, or it silently
resolves to no session at all.

get_script_run_ctx() itself can't be called from inside the handler
to fix this - it reads a plain attribute off threading.current_thread(),
which by the time the handler runs IS the CrewAI worker thread, not
the one that called crew.kickoff(). What actually survives that hop
is contextvars: CrewAI's event bus does
`contextvars.copy_context().run(...)` before dispatching, which
snapshots whatever contextvars.ContextVars were set on the calling
thread. So the context is captured once per Streamlit rerun (in
setup_layout(), still running on the correct thread) into a
ContextVar of our own - by the time a crew.kickoff() call later in
that same script run triggers an event, copy_context() carries that
ContextVar's value across to the worker thread, where it's read back
and attached to make st.session_state resolve correctly.
"""

import threading
from contextvars import ContextVar
from typing import Any

import streamlit as st
from crewai.events.event_bus import crewai_event_bus
from crewai.events.event_types import (
    AgentExecutionCompletedEvent,
    AgentExecutionErrorEvent,
    AgentExecutionStartedEvent,
    LLMCallCompletedEvent,
    LLMCallFailedEvent,
    LLMCallStartedEvent,
    TaskCompletedEvent,
    TaskFailedEvent,
    TaskStartedEvent,
    ToolUsageFinishedEvent,
    ToolUsageStartedEvent,
)
from streamlit.runtime.scriptrunner import add_script_run_ctx, get_script_run_ctx
from streamlit.runtime.scriptrunner_utils.script_run_context import ScriptRunContext

MAX_FIELD_CHARS = 500

_script_ctx_var: ContextVar[ScriptRunContext | None] = ContextVar(
    "ai_viewer_script_ctx", default=None
)


def _capture_script_ctx() -> None:
    """Call once near the top of a script run, on the real Streamlit
    thread - stashes its ScriptRunContext somewhere that will actually
    survive CrewAI's later hop onto a worker thread (see module
    docstring)."""
    _script_ctx_var.set(get_script_run_ctx())


def _truncate(text: Any) -> str:
    text = str(text)
    return text if len(text) <= MAX_FIELD_CHARS else text[:MAX_FIELD_CHARS] + "... (truncated)"


def _add_step(step_id: str, label: str, input_text: str) -> None:
    if not st.session_state.get("ai_viewer_mode"):
        return
    st.session_state.setdefault("ai_steps", [])
    st.session_state["ai_steps"].append(
        {"id": step_id, "label": label, "input": input_text, "output": None, "state": "running"}
    )
    _render_steps()


def _complete_step(step_id: str, output: str, state: str = "complete") -> None:
    if not st.session_state.get("ai_viewer_mode"):
        return
    for step in st.session_state.get("ai_steps", []):
        if step["id"] == step_id:
            step["output"] = output
            step["state"] = state
            break
    _render_steps()


def _agent_role(event, agent_attr: str | None = "agent") -> str:
    """event.agent_role isn't always populated on every event; the
    actual agent/from_agent object (where present) always has its
    role - prefer that, falling back to agent_role, then a generic
    label."""
    agent_obj = getattr(event, agent_attr, None) if agent_attr else None
    return getattr(agent_obj, "role", None) or getattr(event, "agent_role", None) or "Agent"


def _with_script_ctx(fn, *args) -> None:
    """Runs fn(*args) with the captured ScriptRunContext attached to
    whichever thread CrewAI's event bus happens to call this on, so
    st.session_state resolves to the session that was actually running
    the crew."""
    ctx = _script_ctx_var.get()
    if ctx is not None:
        add_script_run_ctx(threading.current_thread(), ctx)
    try:
        fn(*args)
    except Exception:
        pass  # never let a logging failure break the actual crew run


def _on_tool_started(source, event: ToolUsageStartedEvent) -> None:
    _with_script_ctx(
        _add_step,
        event.event_id,
        f"{event.agent_role} → {event.tool_name}",
        _truncate(event.tool_args),
    )


def _on_tool_finished(source, event: ToolUsageFinishedEvent) -> None:
    _with_script_ctx(_complete_step, event.started_event_id, _truncate(event.output))


def _on_agent_started(source, event: AgentExecutionStartedEvent) -> None:
    _with_script_ctx(
        _add_step,
        event.event_id,
        f"{_agent_role(event)} → started",
        _truncate(event.task_prompt),
    )


def _on_agent_finished(source, event: AgentExecutionCompletedEvent) -> None:
    _with_script_ctx(_complete_step, event.started_event_id, _truncate(event.output))


def _on_agent_error(source, event: AgentExecutionErrorEvent) -> None:
    _with_script_ctx(_complete_step, event.started_event_id, _truncate(event.error), "error")


def _on_task_started(source, event: TaskStartedEvent) -> None:
    description = getattr(event.task, "description", "") if event.task else ""
    _with_script_ctx(
        _add_step,
        event.event_id,
        f"{_agent_role(event, agent_attr=None)} → task started",
        _truncate(description),
    )


def _on_task_completed(source, event: TaskCompletedEvent) -> None:
    _with_script_ctx(_complete_step, event.started_event_id, _truncate(event.output))


def _on_task_failed(source, event: TaskFailedEvent) -> None:
    _with_script_ctx(_complete_step, event.started_event_id, _truncate(event.error), "error")


def _on_llm_call_started(source, event: LLMCallStartedEvent) -> None:
    _with_script_ctx(
        _add_step,
        event.event_id,
        f"{_agent_role(event, agent_attr='from_agent')} → LLM call ({event.model})",
        "",
    )


def _on_llm_call_completed(source, event: LLMCallCompletedEvent) -> None:
    summary = f"finish_reason: {event.finish_reason}"
    if event.usage:
        summary += f"\nusage: {event.usage}"
    _with_script_ctx(_complete_step, event.started_event_id, _truncate(summary))


def _on_llm_call_failed(source, event: LLMCallFailedEvent) -> None:
    _with_script_ctx(_complete_step, event.started_event_id, _truncate(event.error), "error")


# Registered exactly once, at first import - not per crew run. These
# fire for every crew executing anywhere in the process; each handler
# checks st.session_state["ai_viewer_mode"] itself, so a run in one
# user's session never appears in another's (each is on its own
# ScriptRunContext).
crewai_event_bus.on(ToolUsageStartedEvent)(_on_tool_started)
crewai_event_bus.on(ToolUsageFinishedEvent)(_on_tool_finished)
crewai_event_bus.on(AgentExecutionStartedEvent)(_on_agent_started)
crewai_event_bus.on(AgentExecutionCompletedEvent)(_on_agent_finished)
crewai_event_bus.on(AgentExecutionErrorEvent)(_on_agent_error)
crewai_event_bus.on(TaskStartedEvent)(_on_task_started)
crewai_event_bus.on(TaskCompletedEvent)(_on_task_completed)
crewai_event_bus.on(TaskFailedEvent)(_on_task_failed)
crewai_event_bus.on(LLMCallStartedEvent)(_on_llm_call_started)
crewai_event_bus.on(LLMCallCompletedEvent)(_on_llm_call_completed)
crewai_event_bus.on(LLMCallFailedEvent)(_on_llm_call_failed)


def _render_steps() -> None:
    """Fully redraws every recorded step, in order, as its own
    st.status() card - a spinner while running, a checkmark once
    complete - into a fresh container each time. Rebuilding from
    scratch (rather than patching one existing widget in place) is
    what avoids duplicating earlier steps: a plain st.container() only
    ever appends across repeated `with` blocks in the same script run,
    so redrawing into a fresh placeholder.container() is what makes
    an update look like an in-place change instead of a growing pile."""
    placeholder = st.session_state.get("ai_action_placeholder")
    if placeholder is None:
        return
    steps = st.session_state.get("ai_steps", [])
    with placeholder.container():
        if not steps:
            st.caption("No AI activity yet - actions will appear here as they happen.")
            return
        for step in steps:
            running = step["state"] == "running"
            with st.status(step["label"], state=step["state"], expanded=running):
                if step["input"]:
                    st.code(step["input"], language="json")
                if step["output"] is not None:
                    st.write(step["output"])


def render_sidebar_toggle() -> None:
    """Renders the regular/AI-viewer-mode toggle in the sidebar."""
    st.sidebar.toggle("AI viewer mode", key="ai_viewer_mode")


def setup_layout():
    """Call once near the top of the authenticated page body, before
    any of the existing page content. Returns the container the rest
    of the page should be rendered inside (via `with setup_layout():`)
    - a real column (left, 3/5 width) if AI viewer mode is on, or a
    full-width container otherwise. Also sets up the live step feed
    in the right column, if applicable.

    Both sides are bordered containers (st.container(border=True)),
    each carrying its own title ("Job Finder" on the left, added by
    streamlit_app.py; "AI viewer mode" here on the right) - two
    visually separate windows side by side, not one shared header
    floating above a column split."""
    _capture_script_ctx()
    if st.session_state.get("ai_viewer_mode"):
        main_col, log_col = st.columns([3, 2])
        window = log_col.container(border=True)
        with window:
            st.markdown(
                '<div class="hero-badge">✨ Powered by AI agents</div>'
                '<div class="hero-title" style="font-size:1.8rem;">AI viewer mode</div>',
                unsafe_allow_html=True,
            )
            if st.button("Clear log", key="clear_ai_log"):
                st.session_state["ai_steps"] = []
            st.session_state["ai_action_placeholder"] = st.empty()
            _render_steps()
        return main_col.container(border=True)
    st.session_state["ai_action_placeholder"] = None
    return st.container()
