"""
Usage reporting for the hidden admin page in streamlit_app.py.

Stores CV-generation events in the same SQLite database auth.py
already uses (data/auth.db), rather than a separate file - one
database to back up, one place that knows about the schema. Counting
starts from whenever this was added; nothing before that is counted,
since it was never tracked.
"""

import sqlite3
from datetime import datetime, timezone

from auth import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS cv_generation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(SCHEMA)
    return conn


def record_cv_generated(username: str, kind: str) -> None:
    """kind is 'tailored' (resume tailored for a specific job posting)
    or 'format' (resume rebuilt in a different visual template)."""
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO cv_generation_events (username, kind, created_at) VALUES (?, ?, ?)",
            (username, kind, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


def get_report() -> dict:
    conn = _connect()
    try:
        registered_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        tailored_count = conn.execute(
            "SELECT COUNT(*) FROM cv_generation_events WHERE kind = 'tailored'"
        ).fetchone()[0]
        format_count = conn.execute(
            "SELECT COUNT(*) FROM cv_generation_events WHERE kind = 'format'"
        ).fetchone()[0]
    finally:
        conn.close()

    return {
        "registered_users": registered_users,
        "cvs_tailored": tailored_count,
        "cvs_format": format_count,
        "cvs_total": tailored_count + format_count,
    }
