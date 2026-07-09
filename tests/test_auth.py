"""
Unit tests for auth.py's pure/near-pure functions: the standalone admin
actions (set_user_password, delete_user), the row-upsert helper, and
the password validator. Does not cover AuthManager's Streamlit-rendering
methods (render_login_or_register) - those need a running Streamlit
session, not a unit test.

Each test gets its own throwaway SQLite file via the db fixture below,
with auth.DB_PATH monkeypatched to point at it - never touches the
real data/auth.db.
"""

import sqlite3

import pytest

import auth


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh SQLite file with auth.py's schema applied, with
    auth.DB_PATH pointed at it for the duration of the test."""
    db_path = tmp_path / "test_auth.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(auth.SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setattr(auth, "DB_PATH", db_path)
    return db_path


def _insert_user(db_path, username: str, password_hash: str = "irrelevant-hash") -> None:
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
        (username, username, password_hash),
    )
    conn.commit()
    conn.close()


def _fetch_password(db_path, username: str) -> str | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT password FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


class TestSetUserPassword:
    def test_updates_password_for_existing_user(self, db):
        _insert_user(db, "marco@example.com")

        result = auth.set_user_password("marco@example.com", "new-password-123")

        assert result is True
        new_hash = _fetch_password(db, "marco@example.com")
        assert new_hash != "irrelevant-hash"
        # The hash is real bcrypt output, not the plaintext password.
        assert new_hash != "new-password-123"

    def test_returns_false_for_nonexistent_user(self, db):
        result = auth.set_user_password("nobody@example.com", "whatever")

        assert result is False


class TestDeleteUser:
    def test_removes_existing_user_row(self, db):
        _insert_user(db, "marco@example.com")

        result = auth.delete_user("marco@example.com")

        assert result is True
        assert _fetch_password(db, "marco@example.com") is None

    def test_returns_false_for_nonexistent_user(self, db):
        result = auth.delete_user("nobody@example.com")

        assert result is False

    def test_removes_users_directory(self, db, tmp_path, monkeypatch):
        _insert_user(db, "marco@example.com")
        user_dir = tmp_path / "users" / "marco@example.com"
        user_dir.mkdir(parents=True)
        (user_dir / "resume.docx").write_text("fake resume content")
        monkeypatch.setattr(auth, "USERS_DIR", tmp_path / "users")

        auth.delete_user("marco@example.com")

        assert not user_dir.exists()

    def test_does_not_error_if_users_directory_never_existed(self, db, tmp_path, monkeypatch):
        _insert_user(db, "marco@example.com")
        monkeypatch.setattr(auth, "USERS_DIR", tmp_path / "users")

        # Should not raise even though tmp_path/"users"/"marco@example.com" never existed.
        result = auth.delete_user("marco@example.com")

        assert result is True


class TestUpsertUser:
    def test_inserts_new_user(self, db):
        conn = sqlite3.connect(db)
        data = {
            "email": "marco@example.com",
            "first_name": "Marco",
            "last_name": "Hauff",
            "password": "some-hash",
            "password_hint": None,
            "failed_login_attempts": 0,
            "logged_in": False,
            "roles": None,
        }

        auth.AuthManager._upsert_user(conn, "marco@example.com", data)
        conn.commit()

        row = conn.execute(
            "SELECT email, first_name, logged_in FROM users WHERE username = ?",
            ("marco@example.com",),
        ).fetchone()
        conn.close()
        assert row == ("marco@example.com", "Marco", 0)

    def test_updates_existing_user_on_conflict(self, db):
        conn = sqlite3.connect(db)
        first = {"email": "marco@example.com", "first_name": "Marco", "last_name": "H",
                  "password": "old-hash", "password_hint": None,
                  "failed_login_attempts": 0, "logged_in": False, "roles": None}
        auth.AuthManager._upsert_user(conn, "marco@example.com", first)
        conn.commit()

        updated = dict(first, first_name="Marco Updated", failed_login_attempts=3)
        auth.AuthManager._upsert_user(conn, "marco@example.com", updated)
        conn.commit()

        row = conn.execute(
            "SELECT first_name, failed_login_attempts FROM users WHERE username = ?",
            ("marco@example.com",),
        ).fetchone()
        conn.close()
        assert row == ("Marco Updated", 3)

    def test_serializes_roles_as_json(self, db):
        conn = sqlite3.connect(db)
        data = {"email": "a@example.com", "first_name": "A", "last_name": "B",
                 "password": "h", "password_hint": None,
                 "failed_login_attempts": 0, "logged_in": False, "roles": ["admin", "editor"]}

        auth.AuthManager._upsert_user(conn, "a@example.com", data)
        conn.commit()

        roles_json = conn.execute(
            "SELECT roles FROM users WHERE username = ?", ("a@example.com",)
        ).fetchone()[0]
        conn.close()
        assert roles_json == '["admin", "editor"]'


class TestSimpleValidator:
    def test_rejects_password_shorter_than_minimum(self):
        validator = auth.SimpleValidator()

        assert validator.validate_password("abc") is False

    def test_accepts_password_at_minimum_length(self):
        validator = auth.SimpleValidator()

        assert validator.validate_password("abcd") is True

    def test_accepts_long_simple_password(self):
        validator = auth.SimpleValidator()

        assert validator.validate_password("just a normal sentence as a password") is True

    def test_diagnose_message_only_for_short_passwords(self):
        validator = auth.SimpleValidator()

        assert "4 characters" in validator.diagnose_password("ab")
        assert validator.diagnose_password("abcd") == ""
