"""Tests for personal statistics — VO2max, form score, personal bests, streak."""
import pytest
from conftest import make_run
from running_coach.analysis.personal_stats import (
    estimate_vo2max, form_score, personal_bests,
    weekly_streak, pace_trend, compute_personal_stats
)


class TestVO2max:
    def test_with_resting_hr(self, profile):
        vo2 = estimate_vo2max([], profile)
        assert vo2 is not None
        # Uth formula: 15 * (183/54) = 50.8
        assert abs(vo2 - 50.8) < 1.0

    def test_without_resting_hr(self, profile_beginner):
        profile_beginner.resting_hr = None
        runs = [make_run(days_ago=i*3, km=5.0, hr=155, pace=7.5) for i in range(5)]
        vo2 = estimate_vo2max(runs, profile_beginner)
        # May be None if not enough runs, or a float
        assert vo2 is None or (20 < vo2 < 80)

    def test_reasonable_range(self, profile, recent_runs):
        vo2 = estimate_vo2max(recent_runs, profile)
        assert vo2 is not None
        assert 20 < vo2 < 85  # realistic range for runners

    def test_no_runs_uses_hr_formula(self, profile):
        """With resting HR, VO2max can be estimated without runs."""
        vo2 = estimate_vo2max([], profile)
        assert vo2 is not None


class TestFormScore:
    def test_needs_minimum_runs(self, profile):
        """Fewer than 5 runs → None."""
        few_runs = [make_run(days_ago=i*3) for i in range(3)]
        assert form_score(few_runs, profile) is None

    def test_returns_score_and_trend(self, profile, recent_runs):
        result = form_score(recent_runs, profile)
        assert result is not None
        assert 0 <= result["score"] <= 100
        assert result["trend"] in ("improving", "stable", "declining")

    def test_improving_runner(self, profile):
        """Pace improving while HR stays same → improving form."""
        runs = [
            make_run(days_ago=30-i*3, km=5.0, hr=155, pace=9.0 - i*0.1)
            for i in range(10)
        ]
        result = form_score(runs, profile)
        assert result is not None
        assert result["trend"] in ("improving", "stable")

    def test_declining_runner(self, profile):
        """Pace getting slower → declining form."""
        runs = [
            make_run(days_ago=30-i*3, km=5.0, hr=155, pace=7.0 + i*0.15)
            for i in range(10)
        ]
        result = form_score(runs, profile)
        assert result is not None
        # HR same, pace worse → efficiency decreasing
        assert result["trend"] in ("declining", "stable")


class TestPersonalBests:
    def test_empty_runs(self):
        assert personal_bests([]) == {}

    def test_fastest_pace(self):
        runs = [
            make_run(days_ago=0, km=5.0, pace=8.0),
            make_run(days_ago=3, km=5.0, pace=7.5),  # faster
            make_run(days_ago=6, km=5.0, pace=9.0),
        ]
        pb = personal_bests(runs)
        assert pb["fastest_pace"] == "7:30/km"

    def test_longest_run(self):
        runs = [
            make_run(days_ago=0, km=5.0),
            make_run(days_ago=7, km=10.0),  # longest
            make_run(days_ago=14, km=7.0),
        ]
        pb = personal_bests(runs)
        assert pb["longest_km"] == 10.0

    def test_riegel_5k_estimate(self):
        """From a 5km at 8 min/km, estimate 5K time."""
        runs = [make_run(days_ago=0, km=5.0, pace=8.0)]
        pb = personal_bests(runs)
        assert "est_5k" in pb
        # 5km at 8min/km = 40 minutes
        assert pb["est_5k"] == "40:00"

    def test_no_estimate_for_very_short_ref(self):
        """Don't extrapolate from sub-3km runs."""
        runs = [make_run(days_ago=0, km=1.5, pace=6.0)]
        pb = personal_bests(runs)
        assert "est_5k" not in pb

    def test_all_fields_present(self, recent_runs):
        pb = personal_bests(recent_runs)
        assert "longest_km" in pb
        assert "fastest_pace" in pb


class TestWeeklyStreak:
    def test_no_runs(self):
        assert weekly_streak([]) == 0

    def test_consecutive_weeks(self):
        runs = [make_run(days_ago=i*7 + 1) for i in range(4)]
        streak = weekly_streak(runs)
        assert streak >= 4

    def test_gap_breaks_streak(self):
        recent = [make_run(days_ago=1)]
        # Gap — nothing 2-3 weeks ago
        old = [make_run(days_ago=28)]
        streak = weekly_streak(recent + old)
        assert streak == 1  # only this week

    def test_single_run_this_week(self):
        runs = [make_run(days_ago=2)]
        assert weekly_streak(runs) >= 1


class TestPaceTrend:
    def test_returns_8_weeks(self, recent_runs):
        trend = pace_trend(recent_runs)
        assert len(trend) == 8

    def test_empty_weeks_have_none_pace(self):
        runs = [make_run(days_ago=1, pace=8.0)]
        trend = pace_trend(runs)
        none_weeks = [w for w in trend if w["pace"] is None]
        assert len(none_weeks) > 0

    def test_pace_values_are_floats(self, recent_runs):
        trend = pace_trend(recent_runs)
        for week in trend:
            if week["pace"] is not None:
                assert isinstance(week["pace"], float)
                assert 4.0 < week["pace"] < 15.0  # realistic pace range


class TestComputePersonalStats:
    def test_returns_all_keys(self, profile, recent_runs):
        stats = compute_personal_stats(recent_runs, profile)
        assert "form" in stats
        assert "vo2max" in stats
        assert "fitness_age" in stats
        assert "personal_bests" in stats
        assert "weekly_streak" in stats
        assert "pace_trend" in stats
        assert "total_runs" in stats
        assert "total_km" in stats

    def test_total_km_accurate(self, profile, recent_runs):
        stats = compute_personal_stats(recent_runs, profile)
        expected = round(sum(r.distance_km for r in recent_runs), 1)
        assert stats["total_km"] == expected

    def test_total_runs_accurate(self, profile, recent_runs):
        stats = compute_personal_stats(recent_runs, profile)
        assert stats["total_runs"] == len(recent_runs)
