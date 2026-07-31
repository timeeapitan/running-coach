"""Tests for core data schemas."""
import pytest
from datetime import datetime
from running_coach.schemas import NormalizedRun, RunnerProfile
from running_coach.schemas.enums import ActivityType, FitnessLevel


class TestRunnerProfile:
    def test_hr_zones_with_resting_hr(self):
        p = RunnerProfile(max_hr=183, resting_hr=54)
        zones = p.get_hr_zones()
        assert "easy" in zones
        assert "threshold" in zones
        lo, hi = zones["easy"]
        assert lo < hi
        assert lo > 54  # must be above resting HR
        assert hi < 183  # must be below max HR

    def test_hr_zones_without_resting_hr(self):
        p = RunnerProfile(max_hr=183)
        zones = p.get_hr_zones()
        lo, hi = zones["easy"]
        assert lo < hi
        assert hi <= 183

    def test_hr_zones_fallback_age(self):
        """No max_hr set — should estimate from age."""
        p = RunnerProfile(age=27)
        mhr = p.get_effective_max_hr()
        assert mhr == int(208 - 0.7 * 27)
        zones = p.get_hr_zones()
        assert all(lo < hi for lo, hi in zones.values())

    def test_weeks_to_race(self):
        from datetime import date, timedelta
        future = (date.today() + timedelta(weeks=8)).isoformat()
        p = RunnerProfile(race_date=future, race_distance_km=10.0)
        assert p.weeks_to_race() == 8

    def test_weeks_to_race_none(self):
        p = RunnerProfile()
        assert p.weeks_to_race() is None

    def test_race_goal_pace(self):
        p = RunnerProfile(race_goal_time_minutes=60, race_distance_km=10.0)
        assert p.race_goal_pace() == 6.0

    def test_zone_ordering(self):
        """Zones must be ordered: recovery < easy < aerobic < threshold < max."""
        p = RunnerProfile(max_hr=183, resting_hr=54)
        zones = p.get_hr_zones()
        order = ["recovery", "easy", "aerobic", "threshold", "max"]
        prev_hi = 0
        for name in order:
            lo, hi = zones[name]
            assert lo >= prev_hi - 2  # allow small overlap at boundaries
            prev_hi = hi


class TestNormalizedRun:
    def test_valid_run(self):
        r = NormalizedRun(
            date=datetime.now(),
            activity_type=ActivityType.OUTDOOR_RUN,
            distance_km=5.0,
            duration_minutes=40.0,
            avg_hr=155,
            avg_pace_min_per_km=8.0,
        )
        assert r.distance_km == 5.0
        assert r.avg_hr == 155

    def test_negative_distance_raises(self):
        with pytest.raises(ValueError):
            NormalizedRun(
                date=datetime.now(),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=-1.0,
                duration_minutes=40.0,
            )

    def test_zero_duration_raises(self):
        with pytest.raises(ValueError):
            NormalizedRun(
                date=datetime.now(),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=5.0,
                duration_minutes=0,
            )
