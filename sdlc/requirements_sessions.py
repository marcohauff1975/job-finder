"""
Session persistence for the Requirements Challenge admin page - each
session is one back-and-forth with product_manager and
software_architect over a single feature idea, saved as one JSON file
so Marco can resume a past conversation the same way Claude Code lets
you pick a session back up.

Kept separate from sdlc/SDLC.py (which owns the actual agent/crew
logic) the same way reporting.py is kept separate from job_search.py -
this file only reads and writes session files, it never talks to
CrewAI.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

SESSIONS_DIR = Path(__file__).parent.parent / "data" / "requirements_sessions"

TITLE_MAX_CHARS = 60


def _session_path(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def new_session_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def _title_from_first_message(messages: list[dict]) -> str:
    for message in messages:
        if message["role"] == "user":
            text = message["content"].strip().replace("\n", " ")
            return text[:TITLE_MAX_CHARS] + ("…" if len(text) > TITLE_MAX_CHARS else "")
    return "New session"


def list_sessions() -> list[dict]:
    """Every saved session's id/title/updated_at, newest first - not the
    full message history, so the sidebar list stays cheap to render
    even as sessions accumulate."""
    if not SESSIONS_DIR.exists():
        return []
    sessions = []
    for path in SESSIONS_DIR.glob("*.json"):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        sessions.append(
            {
                "id": data["id"],
                "title": data.get("title") or "New session",
                "updated_at": data.get("updated_at", ""),
            }
        )
    return sorted(sessions, key=lambda s: s["updated_at"], reverse=True)


def load_session(session_id: str) -> dict | None:
    path = _session_path(session_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def save_session(session_id: str, messages: list[dict]) -> None:
    """Overwrites the session file with the full current message list -
    called after every turn, so a session is saved as-you-go rather
    than needing an explicit save action."""
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    existing = load_session(session_id)
    created_at = existing["created_at"] if existing else datetime.now(timezone.utc).isoformat()
    data = {
        "id": session_id,
        "title": _title_from_first_message(messages),
        "created_at": created_at,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "messages": messages,
    }
    _session_path(session_id).write_text(json.dumps(data, indent=2))
