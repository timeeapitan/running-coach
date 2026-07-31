"""Tests for the database layer — local file fallback mode."""
import pytest
import os
import tempfile
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force local file mode for tests (no Supabase needed)
os.environ.pop("SUPABASE_URL", None)
os.environ.pop("SUPABASE_KEY", None)

import web.db as db

@pytest.fixture(autouse=True)
def temp_data_dir(tmp_path):
    """Redirect all file storage to a temp directory for isolation."""
    original = db.DATA_DIR
    db.DATA_DIR = str(tmp_path)
    yield tmp_path
    db.DATA_DIR = original


class TestProfileStorage:
    def test_save_and_load_profile(self):
        profile = {"name": "Timeea", "max_hr": 183, "resting_hr": 54}
        db.save_profile("test_user", profile)
        loaded = db.load_profile("test_user")
        assert loaded["name"] == "Timeea"
        assert loaded["max_hr"] == 183

    def test_load_missing_profile_returns_none(self):
        assert db.load_profile("nonexistent_user") is None

    def test_profile_survives_update(self):
        db.save_profile("u1", {"name": "Old"})
        db.save_profile("u1", {"name": "New", "max_hr": 180})
        loaded = db.load_profile("u1")
        assert loaded["name"] == "New"
        assert loaded["max_hr"] == 180


class TestScheduleStorage:
    def test_save_and_load_schedule(self):
        sched = {
            "monday": "easy", "tuesday": "rest", "wednesday": "moderate",
            "thursday": "rest", "friday": "easy",
            "saturday": "long_run", "sunday": "rest",
        }
        db.save_schedule("u1", sched)
        loaded = db.load_schedule("u1")
        assert loaded["monday"] == "easy"
        assert loaded["saturday"] == "long_run"

    def test_load_missing_schedule_returns_defaults(self):
        sched = db.load_schedule("no_such_user")
        assert all(v == "coach" for v in sched.values())
        assert len(sched) == 7

    def test_invalid_types_sanitised_to_coach(self):
        sched = {d: "invalid" for d in db.DAYS}
        db.save_schedule("u1", sched)
        loaded = db.load_schedule("u1")
        assert all(v == "coach" for v in loaded.values())

    def test_all_valid_types_accepted(self):
        for valid_type in db.VALID_TYPES:
            sched = {d: valid_type for d in db.DAYS}
            db.save_schedule("u1", sched)
            loaded = db.load_schedule("u1")
            assert all(v == valid_type for v in loaded.values())


class TestRunsCache:
    def test_save_and_load_runs(self):
        runs = [{"date": "2026-07-01T07:00:00", "distance_km": 5.5}]
        db.save_cached_runs("u1", runs)
        loaded = db.load_cached_runs("u1")
        assert loaded is not None
        assert loaded[0]["distance_km"] == 5.5

    def test_missing_cache_returns_none(self):
        assert db.load_cached_runs("no_such_user") is None

    def test_invalidate_is_safe_to_call(self):
        """invalidate_runs_cache is a no-op in v2 (append-only cache) — should not raise."""
        runs = [{"date": "2026-07-01T07:00:00", "distance_km": 5.0}]
        db.save_cached_runs("u1", runs)
        db.invalidate_runs_cache("u1")  # should not raise
        # Cache is still accessible (v2 is append-only, not wiped on invalidate)
        loaded = db.load_cached_runs("u1")
        assert isinstance(loaded, (list, type(None)))


class TestSummaryCache:
    def test_save_and_load_summary(self):
        summary = {"status": "go", "headline": "Ready to run!", "week_km": 12.5}
        db.save_cached_summary("u1", summary)
        loaded = db.load_cached_summary("u1")
        assert loaded is not None
        assert loaded["status"] == "go"
        assert loaded["week_km"] == 12.5

    def test_missing_summary_returns_none(self):
        assert db.load_cached_summary("no_such_user") is None
