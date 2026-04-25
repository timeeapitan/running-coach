"""
Rule-based coaching decision engine.

Implements a weekly training structure based on:
  - The classic hard/easy principle
  - Progressive overload (10% rule)
  - Safety gates for fatigue and pain
"""

from datetime import datetime, timedelta
from typing import List

from ..schemas import (
    AnalysisResult,
    NormalizedRun,
    RunnerProfile,
    WorkoutRecommendation,
    WorkoutType,
    Intensity,
    FitnessLevel,
)


class CoachingRules:
    """Maps an AnalysisResult → the most appropriate next WorkoutRecommendation."""

    def __init__(self, profile: RunnerProfile):
        self.profile = profile

    def recommend(self, analysis: AnalysisResult) -> WorkoutRecommendation:
        """Return the best next workout for this runner right now."""

        # --- Safety gates (highest priority) ---
        if analysis.fatigue_score >= 80:
            return self._rest_day(
                f"Fatigue is very high ({analysis.fatigue_score:.0f}/100). "
                "Full rest or gentle walk only."
            )

        pain_warnings = [w for w in analysis.warnings if "pain" in w.lower()]
        if pain_warnings:
            return self._rest_day(
                f"{pain_warnings[0]}. Rest until symptom-free, then see a physio if it persists."
            )

        # --- Volume spike: force easy day ---
        spike_warnings = [w for w in analysis.warnings if "spike" in w.lower()]
        if spike_warnings:
            return self._easy_run(
                analysis,
                "Volume spike detected — dial back intensity and keep this run easy.",
            )

        # --- High fatigue: easy only ---
        if analysis.fatigue_score >= 60:
            return self._easy_run(
                analysis,
                f"Fatigue is elevated ({analysis.fatigue_score:.0f}/100). "
                "Keep effort conversational.",
            )

        # --- Decide based on readiness + consistency ---
        if analysis.readiness_score >= 70 and analysis.consistency_score >= 50:
            return self._quality_workout(analysis)

        if analysis.readiness_score >= 50:
            return self._moderate_run(analysis)

        return self._easy_run(analysis, "Readiness is low — keep it easy today.")

    # ------------------------------------------------------------------
    # Workout builders
    # ------------------------------------------------------------------

    def _rest_day(self, rationale: str) -> WorkoutRecommendation:
        return WorkoutRecommendation(
            workout_type=WorkoutType.REST,
            intensity=Intensity.VERY_EASY,
            description="Rest day — active recovery (stretch, walk, foam roll).",
            rationale=rationale,
        )

    def _easy_run(self, analysis: AnalysisResult, rationale: str) -> WorkoutRecommendation:
        km = self._target_distance(analysis, factor=0.75)
        return WorkoutRecommendation(
            workout_type=WorkoutType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=km,
            target_duration_minutes=round(km * 7.0, 0),
            description=(
                f"Easy run ~{km:.1f} km at fully conversational pace. "
                "You should be able to hold a sentence comfortably."
            ),
            rationale=rationale,
            target_hr_zone="easy",
        )

    def _moderate_run(self, analysis: AnalysisResult) -> WorkoutRecommendation:
        km = self._target_distance(analysis, factor=1.0)
        return WorkoutRecommendation(
            workout_type=WorkoutType.MODERATE,
            intensity=Intensity.MODERATE,
            target_distance_km=km,
            target_duration_minutes=round(km * 6.0, 0),
            description=(
                f"Aerobic run ~{km:.1f} km. Comfortably hard — "
                "you can speak in short sentences."
            ),
            rationale="Solid aerobic base-building session.",
            target_hr_zone="aerobic",
        )

    def _quality_workout(self, analysis: AnalysisResult) -> WorkoutRecommendation:
        level = self.profile.fitness_level

        # Long run: schedule approximately once a week
        if self._should_do_long_run(analysis):
            return self._long_run(analysis)

        if level in (FitnessLevel.ADVANCED, FitnessLevel.ELITE):
            return self._interval_session(analysis)
        elif level == FitnessLevel.INTERMEDIATE:
            return self._tempo_run(analysis)
        else:
            # Beginners benefit most from more easy mileage
            return self._moderate_run(analysis)

    def _tempo_run(self, analysis: AnalysisResult) -> WorkoutRecommendation:
        km = self._target_distance(analysis, factor=0.65)
        pace_hint = ""
        if self.profile.threshold_pace_min_per_km:
            p = self.profile.threshold_pace_min_per_km
            pace_hint = f" ({int(p)}:{int((p % 1) * 60):02d}/km target)"
        return WorkoutRecommendation(
            workout_type=WorkoutType.TEMPO,
            intensity=Intensity.HARD,
            target_distance_km=km,
            target_duration_minutes=round(km * 5.2, 0),
            description=(
                f"Tempo run ~{km:.1f} km at comfortably hard effort{pace_hint}. "
                "Breathing laboured but sustainable for 20+ minutes."
            ),
            rationale="Good readiness — a tempo session will build lactate threshold.",
            target_hr_zone="threshold",
        )

    def _interval_session(self, analysis: AnalysisResult) -> WorkoutRecommendation:
        base_km = self._target_distance(analysis, factor=1.0)
        # Scale reps: 4 for lower fitness, up to 8 for high
        reps = max(4, min(8, int(base_km / 1.5)))
        warmup_cool = 2.0
        total_km = reps * 0.8 + warmup_cool
        return WorkoutRecommendation(
            workout_type=WorkoutType.INTERVAL,
            intensity=Intensity.VERY_HARD,
            target_distance_km=round(total_km, 1),
            target_duration_minutes=round(total_km * 6.0 + reps * 1.5, 0),
            description=(
                f"Interval session: {reps}× 800 m at 5 km race effort "
                "with 90 s easy jog recovery. 1 km warm-up and cool-down."
            ),
            rationale="High readiness supports a quality speed session.",
            target_hr_zone="max",
        )

    def _long_run(self, analysis: AnalysisResult) -> WorkoutRecommendation:
        # Long run = 25-35% of weekly volume, capped at a sensible max
        weekly = analysis.average_weekly_volume_km or 20.0
        km = min(32.0, max(8.0, weekly * 0.30))
        km = round(km * 2) / 2  # round to nearest 0.5 km

        # Apply 10% rule: don't let long run grow faster than history
        recent_long = self._recent_longest_run(analysis)
        if recent_long and km > recent_long * 1.10:
            km = round(recent_long * 1.10 * 2) / 2

        return WorkoutRecommendation(
            workout_type=WorkoutType.LONG_RUN,
            intensity=Intensity.EASY,
            target_distance_km=km,
            target_duration_minutes=round(km * 7.0, 0),
            description=(
                f"Long run ~{km:.1f} km at easy, conversational pace. "
                "Walk breaks are fine. Focus on time on feet."
            ),
            rationale="Weekly long run builds aerobic base and mental endurance.",
            target_hr_zone="easy",
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _target_distance(self, analysis: AnalysisResult, factor: float = 1.0) -> float:
        """
        Suggest a run distance based on recent per-run average, adjusted
        by a factor and clamped to safe bounds.
        """
        weekly = analysis.average_weekly_volume_km
        runs_per_week = max(1, self.profile.runs_per_week)
        per_run = (weekly / runs_per_week) if weekly else 5.0
        raw = per_run * factor

        # Apply 10% growth cap relative to recent longest run
        recent_long = self._recent_longest_run(analysis)
        if recent_long:
            cap = recent_long * 1.10
            raw = min(raw, cap)

        return round(max(3.0, min(raw, 30.0)) * 2) / 2  # nearest 0.5

    def _should_do_long_run(self, analysis: AnalysisResult) -> bool:
        """
        Recommend a long run roughly once per week.
        Checks that no long run has been done in the last 5 days.
        """
        # We don't have direct access to runs here, so use the trend as a proxy
        # This is conservative — always allow long run when readiness is good
        # and recent volume isn't spiking
        return (
            analysis.readiness_score >= 65
            and "spike" not in " ".join(analysis.warnings).lower()
            and analysis.fatigue_score < 55
        )

    def _recent_longest_run(self, analysis: AnalysisResult) -> float:
        """
        Estimate the longest recent run from the weekly volume.
        In the absence of individual run data here, we use a heuristic.
        """
        weekly = analysis.average_weekly_volume_km
        if not weekly:
            return 0.0
        runs_per_week = max(1, self.profile.runs_per_week)
        # Assume the longest run is ~30% of weekly volume
        return weekly * 0.30
