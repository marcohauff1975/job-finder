"""
Authentication for the Job Finder app.

Kept separate from job_search.py (the CrewAI agents/business logic) and
from streamlit_app.py (the page layout) - this file's only job is
registration, login, logout, and persisting credentials to a local
SQLite database (data/auth.db). It wraps the streamlit-authenticator
package, which handles password hashing and session cookies for us.

The database lives under data/, a sibling of users/ - both are runtime
state that only ever exists on the server, never in the project source
tree, so re-syncing the app's source files to the server can't clobber
either one. That's the opposite of the old config/auth_config.yaml
approach, where the file lived in the project tree and got wiped by a
deploy that overwrote it with the local dev copy.
"""

import json
import secrets
import shutil
import sqlite3
from pathlib import Path

import streamlit as st
import streamlit_authenticator as stauth
import yaml

from notify import send_registration_notification

DB_PATH = Path(__file__).parent / "data" / "auth.db"
USERS_DIR = Path(__file__).parent / "users"

# Only consulted once, on first run, to migrate anyone already registered
# before the switch to SQLite. Safe to leave in place indefinitely - once
# the database has a cookie_config row, this file is never read again.
LEGACY_YAML_PATH = Path(__file__).parent / "config" / "auth_config.yaml"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cookie_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    name TEXT NOT NULL,
    key TEXT NOT NULL,
    expiry_days INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    email TEXT,
    first_name TEXT,
    last_name TEXT,
    password TEXT,
    password_hint TEXT,
    failed_login_attempts INTEGER DEFAULT 0,
    logged_in INTEGER DEFAULT 0,
    roles TEXT
);
"""


def set_user_password(username: str, new_password: str) -> bool:
    """Admin action: directly resets a user's password, bypassing the
    normal change-password flow (no old password required). Hashes with
    the same bcrypt scheme login checks against, so it takes effect
    immediately on their next login. Returns False if the user doesn't
    exist."""
    conn = sqlite3.connect(DB_PATH)
    try:
        new_hash = stauth.Hasher.hash(new_password)
        cursor = conn.execute(
            "UPDATE users SET password = ? WHERE username = ?", (new_hash, username)
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def delete_user(username: str) -> bool:
    """Admin action: removes a user's account and all their on-disk
    data (resume, tailored resumes, search history) - a full account
    deletion, not just a login lockout. Returns False if the user
    didn't exist."""
    conn = sqlite3.connect(DB_PATH)
    try:
        cursor = conn.execute("DELETE FROM users WHERE username = ?", (username,))
        conn.commit()
        deleted = cursor.rowcount > 0
    finally:
        conn.close()

    if deleted:
        user_dir = USERS_DIR / username
        if user_dir.exists():
            shutil.rmtree(user_dir)
    return deleted


class SimpleValidator(stauth.Validator):
    """Overrides streamlit-authenticator's default password rules (8-20
    chars, upper+lower+digit+symbol) with a much simpler one: at least 4
    characters, nothing else required. Revisit this before a real public
    launch - a 4-character minimum with no complexity requirement is
    intentionally relaxed for early/internal testing only."""

    MIN_LENGTH = 4

    def validate_password(self, password: str) -> bool:
        return len(password) >= self.MIN_LENGTH

    def diagnose_password(self, password: str) -> str:
        if len(password) < self.MIN_LENGTH:
            return f"**Password must be at least {self.MIN_LENGTH} characters long.**"
        return ""


class AuthManager:
    """Owns the auth database and the streamlit-authenticator object,
    and renders the login/register UI when needed."""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.config = self._load_config()

        self.authenticator = stauth.Authenticate(
            self.config["credentials"],
            self.config["cookie"]["name"],
            self.config["cookie"]["key"],
            self.config["cookie"]["expiry_days"],
            validator=SimpleValidator(),
        )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA)
            already_seeded = conn.execute(
                "SELECT 1 FROM cookie_config WHERE id = 1"
            ).fetchone()
            if not already_seeded:
                self._seed_from_legacy_yaml_or_defaults(conn)
            conn.commit()
        finally:
            conn.close()

    def _seed_from_legacy_yaml_or_defaults(self, conn: sqlite3.Connection) -> None:
        """Runs exactly once, the first time the app starts after this
        database was created. Pulls in whatever was in the old YAML file
        (if it's still there) so nobody who registered before the switch
        loses their account; otherwise starts from a fresh cookie key."""
        legacy = None
        if LEGACY_YAML_PATH.exists():
            with open(LEGACY_YAML_PATH, "r") as f:
                legacy = yaml.safe_load(f)

        if legacy:
            cookie = legacy["cookie"]
            usernames = legacy.get("credentials", {}).get("usernames", {}) or {}
        else:
            cookie = {
                "name": "job_finder_auth_cookie",
                "key": secrets.token_hex(32),
                "expiry_days": 30,
            }
            usernames = {}

        conn.execute(
            "INSERT INTO cookie_config (id, name, key, expiry_days) VALUES (1, ?, ?, ?)",
            (cookie["name"], cookie["key"], cookie["expiry_days"]),
        )
        for username, data in usernames.items():
            self._upsert_user(conn, username, data)

    @staticmethod
    def _upsert_user(conn: sqlite3.Connection, username: str, data: dict) -> None:
        roles = data.get("roles")
        conn.execute(
            """
            INSERT INTO users
                (username, email, first_name, last_name, password,
                 password_hint, failed_login_attempts, logged_in, roles)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                email=excluded.email,
                first_name=excluded.first_name,
                last_name=excluded.last_name,
                password=excluded.password,
                password_hint=excluded.password_hint,
                failed_login_attempts=excluded.failed_login_attempts,
                logged_in=excluded.logged_in,
                roles=excluded.roles
            """,
            (
                username,
                data.get("email"),
                data.get("first_name"),
                data.get("last_name"),
                data.get("password"),
                data.get("password_hint"),
                int(data.get("failed_login_attempts") or 0),
                int(bool(data.get("logged_in"))),
                json.dumps(roles) if roles is not None else None,
            ),
        )

    def _load_config(self) -> dict:
        conn = self._connect()
        try:
            name, key, expiry_days = conn.execute(
                "SELECT name, key, expiry_days FROM cookie_config WHERE id = 1"
            ).fetchone()

            usernames = {}
            for row in conn.execute(
                """SELECT username, email, first_name, last_name, password,
                          password_hint, failed_login_attempts, logged_in, roles
                   FROM users"""
            ):
                (
                    username, email, first_name, last_name, password,
                    password_hint, failed_login_attempts, logged_in, roles,
                ) = row
                usernames[username] = {
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "password": password,
                    "password_hint": password_hint,
                    "failed_login_attempts": failed_login_attempts,
                    "logged_in": bool(logged_in),
                    "roles": json.loads(roles) if roles else None,
                }
        finally:
            conn.close()

        return {
            "cookie": {"name": name, "key": key, "expiry_days": expiry_days},
            "credentials": {"usernames": usernames},
        }

    def save(self) -> None:
        """streamlit-authenticator updates self.config['credentials'] in
        place (e.g. after a login attempt or new registration) - this
        writes those changes back to the database so they persist across
        runs and restarts."""
        conn = self._connect()
        try:
            cookie = self.config["cookie"]
            conn.execute(
                "UPDATE cookie_config SET name=?, key=?, expiry_days=? WHERE id=1",
                (cookie["name"], cookie["key"], cookie["expiry_days"]),
            )
            for username, data in self.config["credentials"]["usernames"].items():
                self._upsert_user(conn, username, data)
            conn.commit()
        finally:
            conn.close()

    def render_login_or_register(self) -> bool:
        """Renders the login/register UI. Returns True if the user is
        now authenticated (the caller can go on to render the rest of
        the page), or False if a login/register form was shown instead
        (the caller should stop, e.g. via st.stop())."""
        if st.session_state.get("authentication_status"):
            with st.sidebar:
                display_name = st.session_state.get("name", self.username)
                st.write(f"Signed in as **{display_name}**")
                self.authenticator.logout(location="sidebar")
            self.save()
            return True

        st.markdown(
            '<p style="color:#94a3b8; font-size:1rem; margin-bottom:1rem;">'
            "Sign in or register, and we'll automatically tailor your resume for each "
            "specific company and role you apply to."
            "</p>",
            unsafe_allow_html=True,
        )

        if "auth_mode" not in st.session_state:
            st.session_state["auth_mode"] = (
                "Register" if st.query_params.get("mode") == "register" else "Login"
            )

        mode = st.radio(
            "Mode",
            ["Login", "Register"],
            index=0 if st.session_state["auth_mode"] == "Login" else 1,
            horizontal=True,
            label_visibility="collapsed",
        )

        if mode == "Login":
            try:
                self.authenticator.login(fields={"Username": "Email"})
            except Exception as e:
                st.error(str(e))

            if st.session_state.get("authentication_status"):
                # streamlit-authenticator only auto-reruns after a
                # successful login when credentials were loaded from a
                # file path (self.path truthy) - this app passes
                # credentials as a dict loaded from SQLite instead, so
                # that path never fires. Without an explicit rerun here,
                # the page would keep showing the stale login form until
                # some unrelated interaction happened to trigger one.
                self.save()
                st.rerun()
            elif st.session_state.get("authentication_status") is False:
                st.error("Email or password is incorrect.")
            elif st.session_state.get("authentication_status") is None:
                st.info("Enter your email and password, or switch to Register if you're new.")
        else:
            try:
                email, username, name = self.authenticator.register_user(
                    pre_authorized=None,
                    merge_username_email=True,  # no separate Username field - email IS the login
                    captcha=False,  # TODO: turn back on before a real public launch
                    fields={"Password hint": "Password hint (optional)"},
                )
                if email:
                    st.success("Registration successful! Switch to Login above to sign in.")
                    send_registration_notification(email, name)
            except Exception as e:
                st.error(str(e))

        st.markdown(
            '<p style="color:#64748b; font-size:0.85rem; margin-top:2rem;">'
            'Questions? Contact <a href="mailto:marco.hauff@gmail.com" '
            'style="color:#a78bfa;">marco.hauff@gmail.com</a>'
            "</p>",
            unsafe_allow_html=True,
        )

        self.save()
        return False

    @property
    def username(self) -> str:
        return st.session_state["username"]
