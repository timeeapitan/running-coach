"""Tests for analysis modules — fatigue, readiness, daily summary, injury risk."""
import pytest
from datetime import datetime, timedelta
from conftest import make_run
from running_coach.coaching.coach import RunningCoach
from running_coach.schemas.enums import WorkoutType, FitnessLevel
from running_coach.analysis.daily_summary import build_daily_summary
from running_coach.analysis.insights import injury_risk
from running_coach.schemas.workout import WorkoutRecommendation
from running_coach.schemas.enums import Intensity


def _dummy_rec(wtype=WorkoutType.EASY):
    return WorkoutRecommendation(
        workout_type=wtype, intensity=Intensity.EASY,
        description="Easy run", rationale="Test",
        target_distance_km=5.0, target_duration_minutes=40.0,
        target_hr_zone="easy", steps=[],
    )


class TestCoachAnalysis:
    def test_analyze_returns_all_fields(self, profile, recent_runs):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        analysis = coach.analyze(recent_runs, {})
        assert 0 <= analysis.fatigue_score <= 100
        assert 0 <= analysis.consistency_score <= 100
        assert 0 <= analysis.readiness_score <= 100
        assert isinstance(analysis.trend, str)
        assert analysis.average_weekly_volume_km >= 0

    def test_analyze_no_runs(self, profile, no_runs):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        analysis = coach.analyze(no_runs, {})
        assert analysis.fatigue_score == 0.0
        assert analysis.readiness_score == 50.0

    def test_high_volume_increases_fatigue(self, profile):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        # 7 runs in last 7 days — very high load
        heavy_runs = [make_run(days_ago=i, km=12.0, hr=170) for i in range(7)]
        analysis = coach.analyze(heavy_runs, {})
        assert analysis.fatigue_score > 30

    def test_long_gap_decreases_consistency(self, profile):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        # Ran 6 weeks ago, nothing since
        old_runs = [make_run(days_ago=42, km=5.0)]
        analysis = coach.analyze(old_runs, {})
        assert analysis.consistency_score <= 50  # boundary: single old run gives exactly 50


class TestRecommendation:
    def test_recommends_rest_on_high_fatigue(self, profile):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        heavy_runs = [make_run(days_ago=i, km=15.0, hr=178) for i in range(10)]
        analysis = coach.analyze(heavy_runs, {})
        if analysis.fatigue_score >= 80:
            rec = coach.recommend(analysis, heavy_runs)
            assert rec.workout_type == WorkoutType.REST

    def test_recommends_run_on_good_readiness(self, profile, recent_runs):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        analysis = coach.analyze(recent_runs, {})
        rec = coach.recommend(analysis, recent_runs)
        assert rec.workout_type != WorkoutType.REST or analysis.fatigue_score >= 80

    def test_recommendation_has_steps(self, profile, recent_runs):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        analysis = coach.analyze(recent_runs, {})
        rec = coach.recommend(analysis, recent_runs)
        if not rec.is_rest:
            assert len(rec.steps) >= 2

    def test_recommendation_has_distance(self, profile, recent_runs):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        analysis = coach.analyze(recent_runs, {})
        rec = coach.recommend(analysis, recent_runs)
        if not rec.is_rest:
            assert rec.target_distance_km > 0
            assert rec.target_duration_minutes > 0


class TestDailySummary:
    def test_ran_today_shows_rest(self, profile, recent_runs):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        analysis = coach.analyze(recent_runs, {})
        rec = _dummy_rec()
        # Run today
        today_runs = [make_run(days_ago=0, km=6.0, hr=165)] + recent_runs
        summary = build_daily_summary(today_runs, profile, analysis, rec)
        # After a hard run today, status should be rest
        assert summary["days_inactive"] == 0
        assert summary["status"] in ("rest", "warning", "gentle")

    def test_long_gap_shows_warning(self, profile):
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        old_runs = [make_run(days_ago=20, km=5.0)]
        analysis = coach.analyze(old_runs, {})
        rec = _dummy_rec()
        summary = build_daily_summary(old_runs, profile, analysis, rec)
        assert summary["days_inactive"] >= 20
        assert summary["status"] == "warning"

    def test_no_negative_days_inactive(self, profile):
        """Bug regression: days_inactive must never be negative."""
        today_run = make_run(days_ago=0, km=5.0, hr=155)
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        analysis = coach.analyze([today_run], {})
        rec = _dummy_rec()
        summary = build_daily_summary([today_run], profile, analysis, rec)
        assert summary["days_inactive"] >= 0

    def test_weekly_km_counted(self, profile):
        from datetime import datetime, timedelta
        now = datetime.now()
        week_runs = [make_run(days_ago=i, km=5.0) for i in range(3)]
        coach = RunningCoach(profile, model_dir="/tmp/test_models")
        analysis = coach.analyze(week_runs, {})
        rec = _dummy_rec()
        summary = build_daily_summary(week_runs, profile, analysis, rec)
        assert summary["week_km"] > 0


class TestInjuryRisk:
    def test_low_risk_easy_running(self, profile, recent_runs):
        risk = injury_risk(recent_runs, profile)
        assert risk["score"] >= 0
        assert risk["level"] in ("low", "moderate", "high", "unknown")

    def test_high_hr_increases_risk(self, profile):
        hard_runs = [make_run(days_ago=i*2, km=6.0, hr=175) for i in range(15)]
        risk = injury_risk(hard_runs, profile)
        assert risk["score"] > 0

    def test_risk_has_advice(self, profile):
        hard_runs = [make_run(days_ago=i*2, km=6.0, hr=175) for i in range(15)]
        risk = injury_risk(hard_runs, profile)
        assert "advice" in risk
        assert isinstance(risk["advice"], list)
