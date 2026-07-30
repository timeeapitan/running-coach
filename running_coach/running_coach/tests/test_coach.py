"""
Tests for the coaching layer (rules, templates, RunningCoach).
"""

import pytest
from datetime import datetime, timedelta

from running_coach.coaching.coach import RunningCoach
from running_coach.coaching.rules import CoachingRules
from running_coach.coaching.templates import format_workout_message
from running_coach.schemas import (
    NormalizedRun,
    ManualFeedback,
    RunnerProfile,
    AnalysisResult,
    WorkoutType,
    Intensity,
    ActivityType,
    FitnessLevel,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_profile(**kwargs):
    defaults = dict(max_hr=185, resting_hr=55, runs_per_week=3,
                    fitness_level=FitnessLevel.INTERMEDIATE)
    defaults.update(kwargs)
    return RunnerProfile(**defaults)


def make_analysis(**kwargs):
    defaults = dict(
        fatigue_score=30.0,
        consistency_score=70.0,
        readiness_score=75.0,
        recent_volume_km=20.0,
        average_weekly_volume_km=25.0,
        trend="stable",
        warnings=[],
    )
    defaults.update(kwargs)
    return AnalysisResult(**defaults)


def make_runs(n=6, days_apart=3, distance_km=6.0, avg_hr=145):
    base = datetime.now() - timedelta(days=n * days_apart)
    return [
        NormalizedRun(
            date=base + timedelta(days=i * days_apart),
            activity_type=ActivityType.OUTDOOR_RUN,
            distance_km=distance_km,
            duration_minutes=distance_km * 6.5,
            avg_hr=avg_hr,
            avg_pace_min_per_km=6.5,
        )
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# CoachingRules
# ---------------------------------------------------------------------------

class TestCoachingRules:

    @pytest.fixture
    def rules(self):
        return CoachingRules(make_profile())

    def test_recommends_rest_for_very_high_fatigue(self, rules):
        analysis = make_analysis(fatigue_score=85.0)
        rec = rules.recommend(analysis)
        assert rec.workout_type == WorkoutType.REST

    def test_recommends_rest_when_pain_reported(self, rules):
        analysis = make_analysis(
            fatigue_score=40.0,
            warnings=["Pain reported on 2024-03-15: left knee"],
        )
        rec = rules.recommend(analysis)
        assert rec.workout_type == WorkoutType.REST

    def test_recommends_easy_for_moderate_fatigue(self, rules):
        analysis = make_analysis(fatigue_score=65.0, readiness_score=40.0)
        rec = rules.recommend(analysis)
        assert rec.workout_type == WorkoutType.EASY
        assert rec.intensity == Intensity.EASY

    def test_recommends_tempo_for_intermediate_with_good_readiness(self, rules):
        analysis = make_analysis(fatigue_score=25.0, readiness_score=80.0,
                                  consistency_score=70.0)
        rec = rules.recommend(analysis)
        assert rec.workout_type == WorkoutType.TEMPO

    def test_recommends_intervals_for_advanced_runner(self):
        rules = CoachingRules(make_profile(fitness_level=FitnessLevel.ADVANCED))
        analysis = make_analysis(fatigue_score=20.0, readiness_score=85.0,
                                  consistency_score=75.0)
        rec = rules.recommend(analysis)
        assert rec.workout_type == WorkoutType.INTERVAL

    def test_recommends_moderate_for_beginner_with_good_readiness(self):
        rules = CoachingRules(make_profile(fitness_level=FitnessLevel.BEGINNER))
        analysis = make_analysis(fatigue_score=20.0, readiness_score=80.0,
                                  consistency_score=65.0)
        rec = rules.recommend(analysis)
        assert rec.workout_type == WorkoutType.MODERATE

    def test_rest_day_has_no_distance(self, rules):
        analysis = make_analysis(fatigue_score=90.0)
        rec = rules.recommend(analysis)
        assert rec.target_distance_km is None
        assert rec.is_rest

    def test_easy_run_has_target_distance(self, rules):
        analysis = make_analysis(fatigue_score=65.0)
        rec = rules.recommend(analysis)
        assert rec.target_distance_km is not None
        assert rec.target_distance_km > 0

    def test_recommendation_has_rationale(self, rules):
        for fatigue in [20, 50, 70, 90]:
            rec = rules.recommend(make_analysis(fatigue_score=float(fatigue)))
            assert rec.rationale, f"No rationale for fatigue={fatigue}"

    def test_easy_run_targets_easy_hr_zone(self, rules):
        analysis = make_analysis(fatigue_score=65.0)
        rec = rules.recommend(analysis)
        assert rec.target_hr_zone == "easy"

    def test_tempo_targets_threshold_hr_zone(self, rules):
        analysis = make_analysis(fatigue_score=25.0, readiness_score=80.0,
                                  consistency_score=70.0)
        rec = rules.recommend(analysis)
        assert rec.target_hr_zone == "threshold"


# ---------------------------------------------------------------------------
# format_workout_message (templates)
# ---------------------------------------------------------------------------

class TestFormatWorkoutMessage:

    def test_message_contains_workout_description(self):
        analysis = make_analysis()
        from running_coach.schemas import WorkoutRecommendation
        rec = WorkoutRecommendation(
            workout_type=WorkoutType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=6.0,
            target_duration_minutes=39.0,
            description="Easy run ~6.0 km at conversational pace",
            rationale="Moderate fatigue — keep it easy.",
            target_hr_zone="easy",
        )
        msg = format_workout_message(rec, analysis)
        assert "Easy run" in msg
        assert "6.0 km" in msg

    def test_message_contains_scores(self):
        analysis = make_analysis(fatigue_score=42.0, consistency_score=68.0,
                                  readiness_score=77.0)
        from running_coach.schemas import WorkoutRecommendation
        rec = WorkoutRecommendation(
            workout_type=WorkoutType.MODERATE,
            intensity=Intensity.MODERATE,
            description="Moderate run",
            rationale="Solid day.",
        )
        msg = format_workout_message(rec, analysis)
        assert "42" in msg
        assert "68" in msg
        assert "77" in msg

    def test_message_includes_warnings(self):
        analysis = make_analysis(warnings=["Volume spike detected", "High fatigue"])
        from running_coach.schemas import WorkoutRecommendation
        rec = WorkoutRecommendation(
            workout_type=WorkoutType.REST,
            intensity=Intensity.VERY_EASY,
            description="Rest day",
            rationale="Too tired.",
        )
        msg = format_workout_message(rec, analysis)
        assert "Volume spike" in msg
        assert "High fatigue" in msg

    def test_rest_day_message(self):
        analysis = make_analysis()
        from running_coach.schemas import WorkoutRecommendation
        rec = WorkoutRecommendation(
            workout_type=WorkoutType.REST,
            intensity=Intensity.VERY_EASY,
            description="Rest day",
            rationale="Full rest today.",
        )
        msg = format_workout_message(rec, analysis)
        assert "REST" in msg.upper()


# ---------------------------------------------------------------------------
# RunningCoach (integration)
# ---------------------------------------------------------------------------

class TestRunningCoach:

    @pytest.fixture
    def coach(self):
        return RunningCoach(make_profile())

    def test_get_advice_returns_string(self, coach):
        runs = make_runs()
        result = coach.get_advice(runs)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_advice_with_feedback(self, coach):
        runs = make_runs()
        feedback = {
            runs[-1].date.date().isoformat(): ManualFeedback(
                date=runs[-1].date, rpe=7, sleep_hours=7.5, mood=4
            )
        }
        result = coach.get_advice(runs, feedback)
        assert isinstance(result, str)

    def test_analyze_returns_analysis_result(self, coach):
        from running_coach.schemas import AnalysisResult
        runs = make_runs()
        result = coach.analyze(runs)
        assert isinstance(result, AnalysisResult)
        assert 0 <= result.fatigue_score <= 100
        assert 0 <= result.readiness_score <= 100

    def test_empty_runs_gives_safe_advice(self, coach):
        result = coach.get_advice([])
        assert isinstance(result, str)

    def test_pain_warning_triggers_rest(self, coach):
        runs = make_runs(n=4)
        feedback = {
            runs[-1].date.date().isoformat(): ManualFeedback(
                date=runs[-1].date, pain_flag=True, pain_location="right shin"
            )
        }
        result = coach.get_advice(runs, feedback)
        assert "REST" in result.upper() or "rest" in result.lower()

    def test_consistent_high_volume_suggests_quality(self):
        coach = RunningCoach(make_profile(fitness_level=FitnessLevel.ADVANCED,
                                          runs_per_week=5))
        base = datetime.now() - timedelta(weeks=6)
        runs = [
            NormalizedRun(
                date=base + timedelta(days=i * 2),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=8.0,
                duration_minutes=52.0,
                avg_hr=148,
                avg_pace_min_per_km=6.5,
            )
            for i in range(20)
        ]
        analysis = coach.analyze(runs)
        rec = coach.recommend(analysis)
        # With high consistency and low fatigue, should not recommend rest
        assert rec.workout_type != WorkoutType.REST

    def test_recommend_accepts_analysis_result(self, coach):
        from running_coach.schemas import WorkoutRecommendation
        analysis = make_analysis()
        rec = coach.recommend(analysis)
        assert isinstance(rec, WorkoutRecommendation)
