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


def _append_and_render(entry: str) -> None:
    if not st.session_state.get("ai_viewer_mode"):
        return
    st.session_state.setdefault("ai_action_log", [])
    st.session_state["ai_action_log"].append(entry)
    _render_log()


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
    entry = (
        f"**{event.agent_role}** → tool call: `{event.tool_name}`\n"
        f"- input: `{_truncate(event.tool_args)}`\n"
        f"- result: _(running...)_"
    )
    _with_script_ctx(_append_and_render, entry)


def _on_tool_finished(source, event: ToolUsageFinishedEvent) -> None:
    entry = (
        f"**{event.agent_role}** → tool call: `{event.tool_name}`\n"
        f"- input: `{_truncate(event.tool_args)}`\n"
        f"- result: {_truncate(event.output)}"
    )
    _with_script_ctx(_append_and_render, entry)


def _on_agent_finished(source, event: AgentExecutionCompletedEvent) -> None:
    entry = f"**{event.agent_role}** → finished\n{_truncate(event.output)}"
    _with_script_ctx(_append_and_render, entry)


# Registered exactly once, at first import - not per crew run. These
# fire for every crew executing anywhere in the process; each handler
# checks st.session_state["ai_viewer_mode"] itself, so a run in one
# user's session never appears in another's (each is on its own
# ScriptRunContext).
crewai_event_bus.on(ToolUsageStartedEvent)(_on_tool_started)
crewai_event_bus.on(ToolUsageFinishedEvent)(_on_tool_finished)
crewai_event_bus.on(AgentExecutionCompletedEvent)(_on_agent_finished)


def _render_log() -> None:
    placeholder = st.session_state.get("ai_action_placeholder")
    if placeholder is None:
        return
    entries = st.session_state.get("ai_action_log", [])
    if not entries:
        placeholder.caption("No AI activity yet - actions will appear here as they happen.")
        return
    placeholder.markdown("\n\n---\n\n".join(entries))


def render_sidebar_toggle() -> None:
    """Renders the regular/AI-viewer-mode toggle in the sidebar."""
    st.sidebar.toggle("AI viewer mode", key="ai_viewer_mode")


def setup_layout():
    """Call once near the top of the authenticated page body, before
    any of the existing page content. Returns the container the rest
    of the page should be rendered inside (via `with setup_layout():`)
    - a real column (left, 3/5 width) if AI viewer mode is on, or a
    full-width container otherwise. Also sets up the live log
    placeholder in the right column, if applicable."""
    _capture_script_ctx()
    if st.session_state.get("ai_viewer_mode"):
        main_col, log_col = st.columns([3, 2])
        with log_col:
            st.markdown("#### AI actions")
            if st.button("Clear log", key="clear_ai_log"):
                st.session_state["ai_action_log"] = []
            st.session_state["ai_action_placeholder"] = st.empty()
            _render_log()
        return main_col
    st.session_state["ai_action_placeholder"] = None
    return st.container()
