"""
Usage reporting for the hidden admin page in streamlit_app.py.

Stores CV-generation events in the same SQLite database auth.py
already uses (data/auth.db), rather than a separate file - one
database to back up, one place that knows about the schema. Counting
starts from whenever this was added; nothing before that is counted,
since it was never tracked.
"""

import os
import sqlite3
from datetime import datetime, timezone

import requests

from auth import DB_PATH

SERPER_ACCOUNT_URL = "https://google.serper.dev/account"

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


def get_serper_balance() -> int | None:
    """Live remaining-credits balance straight from Serper's own account
    endpoint - not something we track ourselves, so it can't drift from
    what Serper actually thinks. Returns None if the API key is missing
    or the request fails, so the admin page can show that clearly
    instead of a wrong number."""
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        return None
    try:
        response = requests.post(
            SERPER_ACCOUNT_URL, headers={"X-API-KEY": api_key}, timeout=5
        )
        response.raise_for_status()
        return response.json().get("balance")
    except (requests.RequestException, ValueError):
        return None


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

        usernames = [
            row[0] for row in conn.execute("SELECT username FROM users ORDER BY username")
        ]
        per_user = []
        for username in usernames:
            user_tailored = conn.execute(
                "SELECT COUNT(*) FROM cv_generation_events WHERE username = ? AND kind = 'tailored'",
                (username,),
            ).fetchone()[0]
            user_format = conn.execute(
                "SELECT COUNT(*) FROM cv_generation_events WHERE username = ? AND kind = 'format'",
                (username,),
            ).fetchone()[0]
            per_user.append({
                "email": username,
                "tailored": user_tailored,
                "format": user_format,
                "total": user_tailored + user_format,
            })
    finally:
        conn.close()

    return {
        "registered_users": registered_users,
        "cvs_tailored": tailored_count,
        "cvs_format": format_count,
        "cvs_total": tailored_count + format_count,
        "per_user": per_user,
    }
