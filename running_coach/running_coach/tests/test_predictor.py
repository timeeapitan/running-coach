"""
Tests for the NextRunPredictor.
"""

from datetime import datetime, timedelta

from running_coach.ml.models.next_run_predictor import NextRunPredictor
from running_coach.schemas import (
    NormalizedRun,
    ManualFeedback,
    RunnerProfile,
    AnalysisResult,
    ActivityType,
    FitnessLevel,
    WorkoutType,
    Intensity,
)


def make_profile(**kw):
    defaults = dict(max_hr=185, resting_hr=55, runs_per_week=4,
                    fitness_level=FitnessLevel.INTERMEDIATE)
    defaults.update(kw)
    return RunnerProfile(**defaults)


def make_analysis(**kw):
    defaults = dict(
        fatigue_score=30.0, consistency_score=70.0, readiness_score=75.0,
        recent_volume_km=25.0, average_weekly_volume_km=30.0,
        trend="stable", warnings=[],
    )
    defaults.update(kw)
    return AnalysisResult(**defaults)


def make_runs(n=10, base_distance=6.0, base_pace=6.5, base_hr=148):
    base = datetime.now() - timedelta(days=n * 2)
    return [
        NormalizedRun(
            date=base + timedelta(days=i * 2),
            activity_type=ActivityType.OUTDOOR_RUN,
            distance_km=base_distance + (i % 3) * 0.5,
            duration_minutes=(base_distance + (i % 3) * 0.5) * base_pace,
            avg_pace_min_per_km=base_pace,
            avg_hr=base_hr,
        )
        for i in range(n)
    ]


class TestNextRunPredictorFallback:
    def test_returns_fallback_with_insufficient_history(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=3)
        analysis = make_analysis()
        rec = pred.predict(runs, analysis)
        assert rec.target_distance_km == 5.0
        assert "Not enough history" in rec.rationale

    def test_fallback_workout_type_is_easy(self):
        pred = NextRunPredictor(make_profile())
        rec = pred.predict(make_runs(n=2), make_analysis())
        assert rec.workout_type == WorkoutType.EASY


class TestNextRunPredictorPersonalised:
    def test_predicts_distance_close_to_history(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12, base_distance=8.0)
        rec = pred.predict(runs, make_analysis(readiness_score=75))
        # Should be in the same ballpark as 8 km
        assert 5.0 <= rec.target_distance_km <= 14.0

    def test_high_fatigue_reduces_distance(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12)
        low_fatigue  = make_analysis(fatigue_score=20, readiness_score=80)
        high_fatigue = make_analysis(fatigue_score=75, readiness_score=30)
        rec_easy = pred.predict(runs, low_fatigue)
        rec_hard = pred.predict(runs, high_fatigue)
        assert rec_hard.target_distance_km <= rec_easy.target_distance_km

    def test_high_readiness_gives_harder_intensity(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12)
        high_ready = make_analysis(readiness_score=85, fatigue_score=20)
        rec = pred.predict(runs, high_ready)
        assert rec.intensity in (Intensity.HARD, Intensity.VERY_HARD, Intensity.MODERATE)

    def test_low_readiness_gives_easy_intensity(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12)
        low_ready = make_analysis(readiness_score=25, fatigue_score=72)
        rec = pred.predict(runs, low_ready)
        assert rec.intensity in (Intensity.VERY_EASY, Intensity.EASY)

    def test_pace_present_when_history_has_pace(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12, base_pace=6.2)
        rec = pred.predict(runs, make_analysis())
        assert rec.target_duration_minutes is not None

    def test_no_pace_when_history_lacks_pace(self):
        pred = NextRunPredictor(make_profile())
        base = datetime.now() - timedelta(days=30)
        runs = [
            NormalizedRun(
                date=base + timedelta(days=i * 2),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=6.0,
                duration_minutes=40.0,
                avg_pace_min_per_km=None,  # no pace
            )
            for i in range(10)
        ]
        rec = pred.predict(runs, make_analysis())
        # duration should fall back to None or distance-based estimate
        # Just check it doesn't crash
        assert rec is not None

    def test_10_percent_rule_respected(self):
        pred = NextRunPredictor(make_profile())
        # Consistent 10 km runs
        runs = make_runs(n=14, base_distance=10.0)
        # Very high readiness — predictor might want to push distance up
        rec = pred.predict(runs, make_analysis(readiness_score=95, fatigue_score=10))
        # Should not jump more than 10% above the max recent run (~10 km)
        assert rec.target_distance_km <= 10.0 * 1.10 + 0.5  # 0.5 tolerance for rounding

    def test_advanced_runner_gets_harder_workout(self):
        pred_int  = NextRunPredictor(make_profile(fitness_level=FitnessLevel.INTERMEDIATE))
        pred_adv  = NextRunPredictor(make_profile(fitness_level=FitnessLevel.ADVANCED))
        runs      = make_runs(n=12)
        analysis  = make_analysis(readiness_score=85, fatigue_score=20)
        rec_int   = pred_int.predict(runs, analysis)
        rec_adv   = pred_adv.predict(runs, analysis)
        # Advanced should get intervals, intermediate gets tempo
        assert rec_adv.intensity.value >= rec_int.intensity.value

    def test_good_sleep_feedback_adjusts_pace(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12, base_pace=6.5)
        analysis = make_analysis(readiness_score=75)

        # With good sleep
        good_feedback = {
            runs[-1].date.date().isoformat(): ManualFeedback(
                date=runs[-1].date, sleep_quality=5, mood=5
            )
        }
        # With poor sleep
        bad_feedback = {
            runs[-1].date.date().isoformat(): ManualFeedback(
                date=runs[-1].date, sleep_quality=1, mood=2
            )
        }
        rec_good = pred.predict(runs, analysis, good_feedback)
        rec_bad  = pred.predict(runs, analysis, bad_feedback)

        # Good sleep → faster pace (lower min/km)
        if rec_good.target_duration_minutes and rec_bad.target_duration_minutes:
            good_pace = rec_good.target_duration_minutes / rec_good.target_distance_km
            bad_pace  = rec_bad.target_duration_minutes  / rec_bad.target_distance_km
            assert good_pace <= bad_pace

    def test_description_contains_distance(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12)
        rec  = pred.predict(runs, make_analysis())
        assert str(rec.target_distance_km) in rec.description or "km" in rec.description

    def test_rationale_mentions_run_count(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12)
        rec  = pred.predict(runs, make_analysis())
        assert "12" in rec.rationale


class TestNextRunPredictorEdgeCases:
    def test_all_runs_same_distance_predicts_similar(self):
        pred = NextRunPredictor(make_profile())
        runs = make_runs(n=12, base_distance=7.0)
        rec  = pred.predict(runs, make_analysis(readiness_score=70, fatigue_score=30))
        assert 5.0 <= rec.target_distance_km <= 10.0

    def test_returns_valid_workout_type(self):
        pred    = NextRunPredictor(make_profile())
        runs    = make_runs(n=12)
        rec     = pred.predict(runs, make_analysis())
        valid   = set(WorkoutType)
        assert rec.workout_type in valid

    def test_distance_always_positive(self):
        pred = NextRunPredictor(make_profile())
        for readiness in [10, 30, 50, 70, 90]:
            rec = pred.predict(
                make_runs(n=10),
                make_analysis(readiness_score=float(readiness))
            )
            assert rec.target_distance_km > 0
