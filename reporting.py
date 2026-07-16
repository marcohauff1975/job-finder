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

# Anthropic doesn't let a regular API key check credit balance (that
# needs a separate, more sensitive "Admin API key" scoped to the whole
# org) - so instead of a real balance, we track actual token usage
# ourselves and estimate spend from it. Published Claude Sonnet rates,
# per million tokens - update here if pricing changes.
PRICE_PER_MILLION_INPUT = 3.00
PRICE_PER_MILLION_OUTPUT = 15.00
PRICE_PER_MILLION_CACHE_READ = 0.30
PRICE_PER_MILLION_CACHE_WRITE = 3.75

SCHEMA = """
CREATE TABLE IF NOT EXISTS cv_generation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS anthropic_token_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_tokens INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    cached_prompt_tokens INTEGER NOT NULL,
    cache_creation_tokens INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS user_tiers (
    username TEXT PRIMARY KEY,
    tier TEXT NOT NULL
);
"""

# Which Claude model tier each registered user is on, controlling which
# agents in job_search.py get the expensive model vs the cheap one -
# see job_search.py's TIER_HIGH_MODEL_AGENTS for the actual mapping.
TIER_FREE = "free"
TIER_PAID = "paid"
TIER_POWER = "power"
VALID_TIERS = (TIER_FREE, TIER_PAID, TIER_POWER)

# The account that bypasses the daily search/research quotas, and the one the
# admin console's Overview tab reports on. Here rather than in streamlit_app.py
# because both sides need it and they are about to stop being the same process:
# the public app checks it per search, the console passes it to
# render_overview_tab(). Two copies of an address is one rename away from an
# admin page quietly reporting on nobody.
UNLIMITED_USER = "marco.hauff@gmail.com"

# Throwaway accounts used by automated/manual testing (the ux_reviewer
# Req2Prod agent, local QA, this session's own scratch testing) all use the
# @example.com domain - forcing them onto the free tier regardless of
# whatever's stored keeps testing from silently running up paid-tier
# model costs.
TEST_ACCOUNT_DOMAIN = "@example.com"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
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


def record_token_usage(
    prompt_tokens: int,
    completion_tokens: int,
    cached_prompt_tokens: int = 0,
    cache_creation_tokens: int = 0,
) -> None:
    """Records one crew run's Anthropic token usage (from CrewOutput.token_usage),
    so estimated spend survives across runs/restarts."""
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO anthropic_token_events
                (prompt_tokens, completion_tokens, cached_prompt_tokens,
                 cache_creation_tokens, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                prompt_tokens,
                completion_tokens,
                cached_prompt_tokens,
                cache_creation_tokens,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_estimated_anthropic_cost() -> float:
    """Estimated USD spend so far, computed from actual recorded token
    usage and published Sonnet pricing - not a real account balance
    (Anthropic doesn't expose that to a regular API key), but a
    reasonable approximation to track burn rate."""
    conn = _connect()
    try:
        row = conn.execute(
            """
            SELECT
                COALESCE(SUM(prompt_tokens), 0),
                COALESCE(SUM(completion_tokens), 0),
                COALESCE(SUM(cached_prompt_tokens), 0),
                COALESCE(SUM(cache_creation_tokens), 0)
            FROM anthropic_token_events
            """
        ).fetchone()
    finally:
        conn.close()

    prompt_tokens, completion_tokens, cached_prompt_tokens, cache_creation_tokens = row
    return (
        prompt_tokens * PRICE_PER_MILLION_INPUT
        + completion_tokens * PRICE_PER_MILLION_OUTPUT
        + cached_prompt_tokens * PRICE_PER_MILLION_CACHE_READ
        + cache_creation_tokens * PRICE_PER_MILLION_CACHE_WRITE
    ) / 1_000_000


def get_user_tier(username: str) -> str:
    """This user's model tier, defaulting to free if the admin has
    never set one. Test accounts (@example.com) always get free,
    regardless of what's stored, so testing can't run up paid-tier
    model costs."""
    if username.endswith(TEST_ACCOUNT_DOMAIN):
        return TIER_FREE
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT tier FROM user_tiers WHERE username = ?", (username,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row and row[0] in VALID_TIERS else TIER_FREE


def set_user_tier(username: str, tier: str) -> None:
    if tier not in VALID_TIERS:
        raise ValueError(f"Unknown tier: {tier!r}")
    conn = _connect()
    try:
        conn.execute(
            """
            INSERT INTO user_tiers (username, tier) VALUES (?, ?)
            ON CONFLICT(username) DO UPDATE SET tier = excluded.tier
            """,
            (username, tier),
        )
        conn.commit()
    finally:
        conn.close()


def delete_user_data(username: str) -> None:
    """Removes this user's usage/tier records - called alongside
    auth.delete_user() when an admin fully deletes an account, so the
    per-user table doesn't keep showing a ghost entry afterward."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM cv_generation_events WHERE username = ?", (username,))
        conn.execute("DELETE FROM user_tiers WHERE username = ?", (username,))
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
                "tier": get_user_tier(username),
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
