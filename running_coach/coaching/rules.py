"""
Rule-based coaching decision engine.

Changes:
  - Now receives run history to track last workout types (change 11)
  - Terrain-aware: detects trail vs road, adjusts targets (change 13)
  - Returns structured steps for warm-up/main/cool-down (change 9)
"""

from datetime import datetime, timedelta
from typing import List, Optional

from ..schemas import (
    AnalysisResult,
    NormalizedRun,
    RunnerProfile,
    WorkoutRecommendation,
    WorkoutType,
    Intensity,
    FitnessLevel,
    ActivityType,
)


class CoachingRules:

    def __init__(self, profile: RunnerProfile):
        self.profile = profile

    def recommend(
        self,
        analysis: AnalysisResult,
        runs: Optional[List[NormalizedRun]] = None,
    ) -> WorkoutRecommendation:
        runs = runs or []

        # Safety gates
        if analysis.fatigue_score >= 80:
            return self._rest_day(
                f"Fatigue is very high ({analysis.fatigue_score:.0f}/100). "
                "Full rest or gentle walk only."
            )
        pain_warnings = [w for w in analysis.warnings if "pain" in w.lower()]
        if pain_warnings:
            return self._rest_day(f"{pain_warnings[0]}. Rest until symptom-free.")

        spike_warnings = [w for w in analysis.warnings if "spike" in w.lower()]
        if spike_warnings:
            return self._easy_run(
                analysis, runs,
                "Volume spike detected — keep this run easy.",
            )

        if analysis.fatigue_score >= 60:
            return self._easy_run(
                analysis, runs,
                f"Fatigue is elevated ({analysis.fatigue_score:.0f}/100). "
                "Keep effort conversational.",
            )

        if analysis.readiness_score >= 70 and analysis.consistency_score >= 50:
            return self._quality_workout(analysis, runs)

        if analysis.readiness_score >= 50:
            return self._moderate_run(analysis, runs)

        return self._easy_run(analysis, runs, "Readiness is low — keep it easy today.")

    # ── Terrain helpers ────────────────────────────────────────────────

    def _is_trail_runner(self, runs: List[NormalizedRun]) -> bool:
        """True if >30% of recent runs are trail runs."""
        if not runs:
            return False
        recent = sorted(runs, key=lambda r: r.date, reverse=True)[:20]
        trail_count = sum(1 for r in recent if r.activity_type == ActivityType.TRAIL_RUN)
        return trail_count / len(recent) > 0.3

    def _avg_elevation_per_km(self, runs: List[NormalizedRun]) -> float:
        """Average elevation gain per km over recent runs."""
        recent = [r for r in runs if r.elevation_gain_m and r.distance_km]
        if not recent:
            return 0.0
        return sum(r.elevation_gain_m / r.distance_km for r in recent[-10:]) / min(len(recent), 10)

    def _terrain_pace_factor(self, runs: List[NormalizedRun]) -> float:
        """
        Adjust pace target for terrain difficulty.
        Flat road = 1.0, hilly trail = up to 1.25 (slower target pace).
        """
        elev_per_km = self._avg_elevation_per_km(runs)
        if elev_per_km > 40:   return 1.25
        elif elev_per_km > 20: return 1.12
        elif elev_per_km > 10: return 1.06
        return 1.0

    def _terrain_label(self, runs: List[NormalizedRun]) -> str:
        if self._is_trail_runner(runs):
            elev = self._avg_elevation_per_km(runs)
            if elev > 30:
                return "hilly trail"
            return "trail"
        return "road"

    # ── Workout builders ───────────────────────────────────────────────

    def _rest_day(self, rationale: str) -> WorkoutRecommendation:
        return WorkoutRecommendation(
            workout_type=WorkoutType.REST,
            intensity=Intensity.VERY_EASY,
            description="Rest day",
            rationale=rationale,
            steps=[
                {"label": "Recovery", "detail": "Stretch, foam roll, or gentle walk. No running."},
            ],
        )

    def _easy_run(
        self, analysis: AnalysisResult,
        runs: List[NormalizedRun], rationale: str,
    ) -> WorkoutRecommendation:
        km      = self._target_distance(analysis, factor=0.75)
        terrain = self._terrain_label(runs)
        pace_f  = self._terrain_pace_factor(runs)
        steps   = self._easy_steps(km, terrain)
        return WorkoutRecommendation(
            workout_type=WorkoutType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=km,
            target_duration_minutes=round(km * 7.0 * pace_f, 0),
            description="Easy run",
            rationale=rationale,
            target_hr_zone="easy",
            steps=steps,
            terrain=terrain,
        )

    def _moderate_run(
        self, analysis: AnalysisResult,
        runs: List[NormalizedRun],
    ) -> WorkoutRecommendation:
        km      = self._target_distance(analysis, factor=1.0)
        terrain = self._terrain_label(runs)
        pace_f  = self._terrain_pace_factor(runs)
        steps   = self._moderate_steps(km, terrain)
        return WorkoutRecommendation(
            workout_type=WorkoutType.MODERATE,
            intensity=Intensity.MODERATE,
            target_distance_km=km,
            target_duration_minutes=round(km * 6.0 * pace_f, 0),
            description="Aerobic run",
            rationale="Solid aerobic base-building session.",
            target_hr_zone="aerobic",
            steps=steps,
            terrain=terrain,
        )

    def _quality_workout(
        self, analysis: AnalysisResult,
        runs: List[NormalizedRun],
    ) -> WorkoutRecommendation:
        level = self.profile.fitness_level

        # Check when each quality workout was last done
        days_since_long   = self._days_since_workout_type(runs, WorkoutType.LONG_RUN)
        days_since_tempo  = self._days_since_workout_type(runs, WorkoutType.TEMPO)

        # Long run: aim for once per week, at least 5 days since last one
        if self._should_do_long_run(analysis, days_since_long):
            return self._long_run(analysis, runs)

        # Intervals: advanced/elite only, at least 4 days since last hard session
        if level in (FitnessLevel.ADVANCED, FitnessLevel.ELITE):
            if days_since_tempo is None or days_since_tempo >= 4:
                return self._interval_session(analysis, runs)

        # Tempo: intermediate+, at least 4 days since last tempo
        if level in (FitnessLevel.INTERMEDIATE, FitnessLevel.ADVANCED, FitnessLevel.ELITE):
            if days_since_tempo is None or days_since_tempo >= 4:
                return self._tempo_run(analysis, runs)

        # Default: moderate
        return self._moderate_run(analysis, runs)

    def _tempo_run(
        self, analysis: AnalysisResult,
        runs: List[NormalizedRun],
    ) -> WorkoutRecommendation:
        km      = self._target_distance(analysis, factor=0.65)
        terrain = self._terrain_label(runs)
        pace_f  = self._terrain_pace_factor(runs)
        steps   = self._tempo_steps(km, terrain)
        pace_hint = ""
        if self.profile.threshold_pace_min_per_km:
            p = self.profile.threshold_pace_min_per_km * pace_f
            pace_hint = f" ({int(p)}:{int((p % 1) * 60):02d}/km target)"
        return WorkoutRecommendation(
            workout_type=WorkoutType.TEMPO,
            intensity=Intensity.HARD,
            target_distance_km=km,
            target_duration_minutes=round(km * 5.2 * pace_f, 0),
            description="Tempo run",
            rationale=f"Good readiness — tempo builds lactate threshold{pace_hint}.",
            target_hr_zone="threshold",
            steps=steps,
            terrain=terrain,
        )

    def _interval_session(
        self, analysis: AnalysisResult,
        runs: List[NormalizedRun],
    ) -> WorkoutRecommendation:
        base_km  = self._target_distance(analysis, factor=1.0)
        reps     = max(4, min(8, int(base_km / 1.5)))
        total_km = round(reps * 0.8 + 2.0, 1)
        terrain  = self._terrain_label(runs)
        steps    = self._interval_steps(reps, terrain)
        return WorkoutRecommendation(
            workout_type=WorkoutType.INTERVAL,
            intensity=Intensity.VERY_HARD,
            target_distance_km=total_km,
            target_duration_minutes=round(total_km * 6.0 + reps * 1.5, 0),
            description="Interval session",
            rationale=f"High readiness supports {reps}× 800 m speed work.",
            target_hr_zone="max",
            steps=steps,
            terrain=terrain,
        )

    def _long_run(
        self, analysis: AnalysisResult,
        runs: List[NormalizedRun],
    ) -> WorkoutRecommendation:
        weekly   = analysis.average_weekly_volume_km or 20.0
        km       = min(32.0, max(8.0, weekly * 0.30))
        km       = round(km * 2) / 2
        recent_long = self._recent_longest_run(analysis)
        if recent_long and km > recent_long * 1.10:
            km = round(recent_long * 1.10 * 2) / 2
        terrain  = self._terrain_label(runs)
        pace_f   = self._terrain_pace_factor(runs)
        steps    = self._long_run_steps(km, terrain)
        return WorkoutRecommendation(
            workout_type=WorkoutType.LONG_RUN,
            intensity=Intensity.EASY,
            target_distance_km=km,
            target_duration_minutes=round(km * 7.0 * pace_f, 0),
            description="Long run",
            rationale="Weekly long run builds aerobic base and mental endurance.",
            target_hr_zone="easy",
            steps=steps,
            terrain=terrain,
        )

    # ── Step builders (change 9) ───────────────────────────────────────

    def _easy_steps(self, km: float, terrain: str) -> list:
        main = round(km - 0.5, 1) if km > 1.5 else km
        trail_tip = " Walk uphill sections freely." if "trail" in terrain else ""
        return [
            {"label": "Warm-up",    "detail": f"0.5 km very easy walk/jog to loosen up."},
            {"label": "Main run",   "detail": f"{main:.1f} km easy, fully conversational pace. HR in easy zone.{trail_tip}"},
            {"label": "Cool-down",  "detail": "5 min easy walk + calf and quad stretch."},
        ]

    def _moderate_steps(self, km: float, terrain: str) -> list:
        warmup = 0.5; cooldown = 0.5
        main   = round(km - warmup - cooldown, 1)
        trail_tip = " Effort not pace — HR over distance on climbs." if "trail" in terrain else ""
        return [
            {"label": "Warm-up",   "detail": f"{warmup} km easy jog."},
            {"label": "Main run",  "detail": f"{main:.1f} km at aerobic effort — short sentences OK.{trail_tip}"},
            {"label": "Cool-down", "detail": f"{cooldown} km easy jog + stretch."},
        ]

    def _tempo_steps(self, km: float, terrain: str) -> list:
        warmup = 1.5; cooldown = 1.5
        main   = round(km - warmup - cooldown, 1)
        terrain_note = " Adjust target pace +10–15 sec/km for hills." if "trail" in terrain else ""
        return [
            {"label": "Warm-up",       "detail": f"{warmup} km easy jog + 4× 20 sec strides."},
            {"label": "Tempo effort",  "detail": f"{main:.1f} km at threshold pace. Breathing laboured but sustainable.{terrain_note}"},
            {"label": "Cool-down",     "detail": f"{cooldown} km easy jog + full leg stretch."},
        ]

    def _interval_steps(self, reps: int, terrain: str) -> list:
        terrain_note = " Use flat sections for work intervals." if "trail" in terrain else ""
        return [
            {"label": "Warm-up",       "detail": f"1 km easy jog + 4× 20 sec strides.{terrain_note}"},
            {"label": f"{reps}× 800 m","detail": f"800 m at 5 km race effort / 400 m easy jog recovery. Repeat {reps} times."},
            {"label": "Cool-down",     "detail": "1 km easy jog + full stretch. Eat and hydrate."},
        ]

    def _long_run_steps(self, km: float, terrain: str) -> list:
        trail_tip = " Walk all steep uphills — this is correct technique, not weakness." if "trail" in terrain else ""
        return [
            {"label": "Warm-up",   "detail": "10 min easy walk, then ease into running pace."},
            {"label": "Long run",  "detail": f"{km:.1f} km at fully easy, conversational pace. Walk breaks every 20–30 min are fine.{trail_tip}"},
            {"label": "Cool-down", "detail": "10 min walk + thorough stretch (hip flexors, calves, hamstrings)."},
        ]

    # ── History helpers (change 11) ────────────────────────────────────

    def _days_since_workout_type(
        self,
        runs: List[NormalizedRun],
        wtype: WorkoutType,
    ) -> Optional[float]:
        """
        Returns days since the last run that was classified as this type,
        or None if that workout type has never been done.

        Classification is based on distance relative to weekly average:
          long_run  → distance >= 1.4× per-run average
          tempo     → distance ~0.6-0.8× per-run average (short but hard — inferred)
        """
        if not runs:
            return None

        now = datetime.now()
        weekly_avg = sum(r.distance_km for r in runs) / max(1, len(runs))
        per_run_avg = weekly_avg

        for run in sorted(runs, key=lambda r: r.date, reverse=True):
            days = (now - run.date).days
            if wtype == WorkoutType.LONG_RUN and run.distance_km >= per_run_avg * 1.4:
                return days
            if wtype == WorkoutType.TEMPO and run.distance_km <= per_run_avg * 0.75:
                return days  # short run → likely a quality/tempo session

        return None

    def _should_do_long_run(
        self,
        analysis: AnalysisResult,
        days_since_long: Optional[float],
    ) -> bool:
        if analysis.readiness_score < 65:
            return False
        if "spike" in " ".join(analysis.warnings).lower():
            return False
        if analysis.fatigue_score >= 55:
            return False
        # If we've never done a long run, or it's been 5+ days, recommend one
        return days_since_long is None or days_since_long >= 5

    def _target_distance(self, analysis: AnalysisResult, factor: float = 1.0) -> float:
        weekly      = analysis.average_weekly_volume_km
        runs_pw     = max(1, self.profile.runs_per_week)
        per_run     = (weekly / runs_pw) if weekly else 5.0
        raw         = per_run * factor
        recent_long = self._recent_longest_run(analysis)
        if recent_long:
            raw = min(raw, recent_long * 1.10)
        return round(max(3.0, min(raw, 30.0)) * 2) / 2

    def _recent_longest_run(self, analysis: AnalysisResult) -> float:
        weekly   = analysis.average_weekly_volume_km
        runs_pw  = max(1, self.profile.runs_per_week)
        return (weekly * 0.30) if weekly else 0.0
