"""
Unit tests for reporting.py's pricing/tier logic and usage reporting.
Each test gets its own throwaway SQLite file via the db fixture below,
with reporting.DB_PATH monkeypatched to point at it (reporting.py does
`from auth import DB_PATH`, which is its own name binding, separate
from auth.DB_PATH - both must be patched independently for full
isolation) - never touches the real data/auth.db. get_report() also
queries the `users` table, which lives in auth.py's schema rather than
reporting.py's own, so the fixture applies both.
"""

import sqlite3

import pytest

import auth
import reporting


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A fresh SQLite file with both auth.py's and reporting.py's
    schemas applied (get_report() joins across both), with
    reporting.DB_PATH pointed at it for the duration of the test."""
    db_path = tmp_path / "test_auth.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(auth.SCHEMA)
    conn.executescript(reporting.SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setattr(reporting, "DB_PATH", db_path)
    return db_path


def _insert_user(db_path, username: str) -> None:
    conn = sqlite3.connect(db_path)
    conn.execute("INSERT INTO users (username, email) VALUES (?, ?)", (username, username))
    conn.commit()
    conn.close()


class TestGetUserTier:
    def test_test_account_domain_always_free_regardless_of_stored_tier(self, db):
        reporting.set_user_tier("someone@example.com", reporting.TIER_POWER)

        assert reporting.get_user_tier("someone@example.com") == reporting.TIER_FREE

    def test_defaults_to_free_when_never_set(self, db):
        assert reporting.get_user_tier("new-user@realdomain.com") == reporting.TIER_FREE

    def test_returns_stored_tier_for_real_account(self, db):
        reporting.set_user_tier("marco@realdomain.com", reporting.TIER_PAID)

        assert reporting.get_user_tier("marco@realdomain.com") == reporting.TIER_PAID


class TestSetUserTier:
    def test_raises_on_unknown_tier(self, db):
        with pytest.raises(ValueError):
            reporting.set_user_tier("marco@realdomain.com", "not-a-real-tier")

    def test_upserts_on_repeated_calls(self, db):
        reporting.set_user_tier("marco@realdomain.com", reporting.TIER_FREE)
        reporting.set_user_tier("marco@realdomain.com", reporting.TIER_POWER)

        assert reporting.get_user_tier("marco@realdomain.com") == reporting.TIER_POWER


class TestGetEstimatedAnthropicCost:
    def test_zero_when_no_usage_recorded(self, db):
        assert reporting.get_estimated_anthropic_cost() == 0.0

    def test_computes_cost_from_published_pricing(self, db):
        # 1,000,000 prompt tokens + 1,000,000 completion tokens, no
        # cache - should be exactly PRICE_PER_MILLION_INPUT +
        # PRICE_PER_MILLION_OUTPUT.
        reporting.record_token_usage(
            prompt_tokens=1_000_000, completion_tokens=1_000_000
        )

        cost = reporting.get_estimated_anthropic_cost()

        expected = reporting.PRICE_PER_MILLION_INPUT + reporting.PRICE_PER_MILLION_OUTPUT
        assert cost == pytest.approx(expected)

    def test_sums_across_multiple_recorded_runs(self, db):
        reporting.record_token_usage(prompt_tokens=500_000, completion_tokens=0)
        reporting.record_token_usage(prompt_tokens=500_000, completion_tokens=0)

        cost = reporting.get_estimated_anthropic_cost()

        expected = reporting.PRICE_PER_MILLION_INPUT  # 1M total prompt tokens
        assert cost == pytest.approx(expected)

    def test_includes_cache_read_and_write_pricing(self, db):
        reporting.record_token_usage(
            prompt_tokens=0,
            completion_tokens=0,
            cached_prompt_tokens=1_000_000,
            cache_creation_tokens=1_000_000,
        )

        cost = reporting.get_estimated_anthropic_cost()

        expected = (
            reporting.PRICE_PER_MILLION_CACHE_READ + reporting.PRICE_PER_MILLION_CACHE_WRITE
        )
        assert cost == pytest.approx(expected)


class TestRecordCvGeneratedAndGetReport:
    def test_counts_tailored_and_format_events_separately(self, db):
        _insert_user(db, "marco@realdomain.com")
        reporting.record_cv_generated("marco@realdomain.com", "tailored")
        reporting.record_cv_generated("marco@realdomain.com", "tailored")
        reporting.record_cv_generated("marco@realdomain.com", "format")

        report = reporting.get_report()

        assert report["cvs_tailored"] == 2
        assert report["cvs_format"] == 1
        assert report["cvs_total"] == 3

    def test_report_includes_registered_user_count(self, db):
        _insert_user(db, "a@realdomain.com")
        _insert_user(db, "b@realdomain.com")

        report = reporting.get_report()

        assert report["registered_users"] == 2

    def test_per_user_breakdown_matches_each_users_own_events(self, db):
        _insert_user(db, "a@realdomain.com")
        _insert_user(db, "b@realdomain.com")
        reporting.record_cv_generated("a@realdomain.com", "tailored")
        reporting.record_cv_generated("b@realdomain.com", "format")
        reporting.record_cv_generated("b@realdomain.com", "format")

        report = reporting.get_report()

        by_email = {row["email"]: row for row in report["per_user"]}
        assert by_email["a@realdomain.com"]["total"] == 1
        assert by_email["b@realdomain.com"]["total"] == 2


class TestDeleteUserData:
    def test_removes_cv_events_and_tier_for_user(self, db):
        _insert_user(db, "marco@realdomain.com")
        reporting.record_cv_generated("marco@realdomain.com", "tailored")
        reporting.set_user_tier("marco@realdomain.com", reporting.TIER_PAID)

        reporting.delete_user_data("marco@realdomain.com")

        report = reporting.get_report()
        assert report["cvs_total"] == 0
        assert reporting.get_user_tier("marco@realdomain.com") == reporting.TIER_FREE

    def test_does_not_affect_other_users(self, db):
        _insert_user(db, "marco@realdomain.com")
        _insert_user(db, "other@realdomain.com")
        reporting.record_cv_generated("marco@realdomain.com", "tailored")
        reporting.record_cv_generated("other@realdomain.com", "tailored")

        reporting.delete_user_data("marco@realdomain.com")

        report = reporting.get_report()
        assert report["cvs_total"] == 1
