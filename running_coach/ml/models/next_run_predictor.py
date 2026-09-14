"""
Next Run Predictor — predicts concrete targets for the runner's next session.

Uses a heuristic model (no external ML library required) that combines:
  - Exponential smoothing of recent pace, HR, and distance
  - ATL/CTL ratio adjustments (fatigue-aware scaling)
  - Progressive overload logic (10% rule)
  - Feedback signals (RPE, sleep, mood)

When the runner has enough history (≥5 runs), predictions become personalised.
"""

from datetime import datetime, timedelta
from math import exp
from typing import Dict, List, Optional, Tuple

from ...schemas import (
    AnalysisResult,
    ManualFeedback,
    NormalizedRun,
    RunnerProfile,
    WorkoutRecommendation,
    WorkoutType,
    Intensity,
)


class NextRunPredictor:
    """
    Predicts the optimal targets for the next run based on training history.

    Outputs a WorkoutRecommendation with data-driven targets for:
      - Distance (km)
      - Duration (min)
      - Target pace (min/km)
      - Target HR zone
      - Intensity label
    """

    # Minimum runs before predictions become personalised
    MIN_RUNS_FOR_PERSONALISATION = 5

    def __init__(self, profile: RunnerProfile):
        self.profile = profile

    def predict(
        self,
        runs: List[NormalizedRun],
        analysis: AnalysisResult,
        feedback: Optional[Dict[str, ManualFeedback]] = None,
    ) -> WorkoutRecommendation:
        """
        Return a personalised next-run recommendation.

        Decision hierarchy (each step falls back to the one below it):
          Pace      ML PacePredictor  →  EWMA smoothing  →  None
          Intensity ML KNN classifier →  rule-based tree
          Distance  fatigue-scaled EWMA baseline (always used)
        """
        feedback = feedback or {}

        if len(runs) < self.MIN_RUNS_FOR_PERSONALISATION:
            return self._fallback_recommendation(analysis)

        # --- Step 1: EWMA baselines (always computed) ---
        baseline_dist = self._smoothed_distance(runs)
        baseline_pace = self._smoothed_pace(runs)
        ml_source     = []   # tracks which ML models contributed

        # --- Step 2: load scaling (distance is finalized after workout type) ---
        scale = self._load_scale_factor(analysis)

        # --- Step 3: pace — try ML first, fall back to EWMA ---
        from ...ml.features import extract_features
        features    = extract_features(runs, feedback, self.profile)
        ml_pace     = self._get_ml_pace(features)

        if ml_pace is not None:
            target_pace = ml_pace
            ml_source.append("pace:ML")
        else:
            target_pace = self._adjust_pace(baseline_pace, analysis, feedback)
            ml_source.append("pace:EWMA")

        # --- Step 4: workout type — schedule-aware and history-aware ---
        # ML may suggest a type, but coaching constraints always win. In particular,
        # long runs are never scheduled on weekdays and quality sessions are spaced out.
        ml_intensity = self._get_ml_intensity(features)
        intensity, zone, wtype = self._choose_intensity(
            analysis, feedback, runs=runs, ml_intensity=ml_intensity
        )
        ml_source.append("type:ML+rules" if ml_intensity is not None else "type:rules")

        # --- Step 5: distance — depends on the selected workout type ---
        if wtype == WorkoutType.LONG_RUN:
            weekly = analysis.average_weekly_volume_km or (baseline_dist * max(1, self.profile.runs_per_week))
            target_dist = max(8.0, weekly * 0.30)
        elif wtype in (WorkoutType.TEMPO, WorkoutType.INTERVAL):
            target_dist = baseline_dist * min(scale, 1.0) * 0.80
        elif wtype in (WorkoutType.EASY, WorkoutType.RECOVERY):
            target_dist = baseline_dist * min(scale, 0.85)
        else:
            target_dist = baseline_dist * scale

        recent_max = self._recent_max_distance(runs, days=14)
        if recent_max and target_dist > recent_max * 1.10:
            target_dist = recent_max * 1.10

        # Weekday morning constraint: keep the whole run within the configured time budget.
        if datetime.now().weekday() not in self.profile.long_run_days and target_pace:
            max_minutes = max(20, int(self.profile.weekday_max_duration_minutes))
            target_dist = min(target_dist, max_minutes / target_pace)

        target_dist = round(max(3.0, min(target_dist, 35.0)) * 2) / 2
        target_dur = round(target_dist * target_pace, 1) if target_pace else None

        # --- Step 6: format output ---
        pace_str = self._format_pace(target_pace) if target_pace else None
        hr_range = self._hr_range_for_zone(zone)

        description = self._build_description(
            wtype, target_dist, target_dur, pace_str, hr_range, zone
        )
        rationale = self._build_rationale(
            analysis, baseline_dist, target_dist, scale, runs, ml_source
        )

        return WorkoutRecommendation(
            workout_type=wtype,
            intensity=intensity,
            target_distance_km=target_dist,
            target_duration_minutes=target_dur,
            description=description,
            rationale=rationale,
            target_hr_zone=zone,
        )

    # ------------------------------------------------------------------
    # Smoothing / baseline methods
    # ------------------------------------------------------------------

    def _smoothed_distance(self, runs: List[NormalizedRun], alpha: float = 0.25) -> float:
        """
        Exponentially weighted moving average of distance.
        More recent runs have higher weight.
        alpha=0.25 → ~4-run memory.
        """
        sorted_runs = sorted(runs, key=lambda r: r.date)
        smoothed = sorted_runs[0].distance_km
        for r in sorted_runs[1:]:
            smoothed = alpha * r.distance_km + (1 - alpha) * smoothed
        return smoothed

    def _smoothed_pace(self, runs: List[NormalizedRun], alpha: float = 0.20) -> Optional[float]:
        """EWMA of pace. Returns None if no pace data available."""
        pace_runs = [r for r in runs if r.avg_pace_min_per_km]
        if not pace_runs:
            return None
        sorted_runs = sorted(pace_runs, key=lambda r: r.date)
        smoothed = sorted_runs[0].avg_pace_min_per_km
        for r in sorted_runs[1:]:
            smoothed = alpha * r.avg_pace_min_per_km + (1 - alpha) * smoothed
        return smoothed

    def _smoothed_hr(self, runs: List[NormalizedRun], alpha: float = 0.20) -> Optional[float]:
        """EWMA of average HR."""
        hr_runs = [r for r in runs if r.avg_hr]
        if not hr_runs:
            return None
        sorted_runs = sorted(hr_runs, key=lambda r: r.date)
        smoothed = sorted_runs[0].avg_hr
        for r in sorted_runs[1:]:
            smoothed = alpha * r.avg_hr + (1 - alpha) * smoothed
        return smoothed

    def _recent_max_distance(self, runs: List[NormalizedRun], days: int) -> Optional[float]:
        """Longest run in the last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        recent = [r.distance_km for r in runs if r.date >= cutoff]
        return max(recent) if recent else None

    # ------------------------------------------------------------------
    # Scaling
    # ------------------------------------------------------------------

    def _load_scale_factor(self, analysis: AnalysisResult) -> float:
        """
        Map readiness → distance scale factor.

        readiness 80-100 → scale 1.05  (gentle progression)
        readiness 50-80  → scale 1.00  (maintain)
        readiness 30-50  → scale 0.85  (back off slightly)
        readiness <30    → scale 0.70  (recovery run)
        """
        r = analysis.readiness_score
        if r >= 80:
            return 1.05
        elif r >= 50:
            return 1.00
        elif r >= 30:
            return 0.85
        else:
            return 0.70

    def _adjust_pace(
        self,
        baseline_pace: Optional[float],
        analysis: AnalysisResult,
        feedback: Dict[str, ManualFeedback],
    ) -> Optional[float]:
        """
        Adjust target pace based on fatigue and recent RPE.

        Higher fatigue → slower pace (larger min/km value).
        Better sleep/mood → slightly faster target.
        """
        if baseline_pace is None:
            return None

        pace = baseline_pace

        # Fatigue adjustment: +3% per 10 pts of fatigue above 30
        fatigue_excess = max(0.0, analysis.fatigue_score - 30.0)
        fatigue_adj = 1.0 + (fatigue_excess / 10.0) * 0.03
        pace *= fatigue_adj

        # Recovery adjustment from the most recent DB-cached Garmin/manual data.
        # Poor recovery only slows the target; we do not make the runner faster
        # solely because a wearable metric looks good.
        recent_fb = self._most_recent_feedback(feedback, days=2)
        if recent_fb:
            if recent_fb.sleep_hours is not None and recent_fb.sleep_hours < 6.5:
                pace *= 1.03
            elif recent_fb.sleep_quality and recent_fb.sleep_quality <= 2:
                pace *= 1.03
            if recent_fb.body_battery is not None and recent_fb.body_battery < 30:
                pace *= 1.03
            if recent_fb.stress is not None and recent_fb.stress >= 60:
                pace *= 1.02
            if recent_fb.mood and recent_fb.mood <= 2:
                pace *= 1.02

        return round(pace, 2)

    # ------------------------------------------------------------------
    # Intensity selection
    # ------------------------------------------------------------------

    def _choose_intensity(
        self,
        analysis: AnalysisResult,
        feedback: Dict[str, ManualFeedback],
        runs: Optional[List[NormalizedRun]] = None,
        ml_intensity=None,
    ) -> Tuple[Intensity, str, WorkoutType]:
        """Choose a safe, schedule-aware workout type using cached run history.

        Garmin is not consulted here: ``runs`` is the already-loaded history supplied
        by the application (normally from ``runs_cache`` in the database).
        """
        runs = runs or []
        r = analysis.readiness_score
        f = analysis.fatigue_score

        # Safety always overrides variety or ML.
        if f >= 70 or r < 35:
            return Intensity.VERY_EASY, "recovery", WorkoutType.RECOVERY
        if f >= 50 or r < 55:
            return Intensity.EASY, "easy", WorkoutType.EASY

        # Today's Garmin recovery metrics are already in the DB-backed feedback
        # object. They can veto a hard/long workout even if training load alone
        # would otherwise permit it.
        recovery_guard = self._recovery_guard(feedback)
        if recovery_guard == "recovery":
            return Intensity.VERY_EASY, "recovery", WorkoutType.RECOVERY
        if recovery_guard == "easy":
            return Intensity.EASY, "easy", WorkoutType.EASY

        today = datetime.now().weekday()
        is_long_run_day = today in tuple(self.profile.long_run_days)
        days_since_long = self._days_since_long_run(runs)
        days_since_hard = self._days_since_hard_run(runs)

        # Long runs are weekend-only (or whatever days the profile explicitly allows).
        if (is_long_run_day and r >= 65 and f < 55 and
                (days_since_long is None or days_since_long >= 6)):
            return Intensity.EASY, "easy", WorkoutType.LONG_RUN

        # Avoid weeks of identical aerobic runs: if recovery is good and there has
        # been no threshold/hard session for 4+ days, prescribe quality work.
        quality_due = days_since_hard is None or days_since_hard >= 4
        if quality_due and r >= 70 and f < 45:
            if self.profile.fitness_level.value in ("advanced", "elite"):
                return Intensity.VERY_HARD, "max", WorkoutType.INTERVAL
            if self.profile.fitness_level.value != "beginner":
                return Intensity.HARD, "threshold", WorkoutType.TEMPO

        # Accept ML only after enforcing the schedule/safety constraints.
        if ml_intensity is not None:
            intensity, zone, wtype = ml_intensity
            if wtype == WorkoutType.LONG_RUN and not is_long_run_day:
                return Intensity.MODERATE, "aerobic", WorkoutType.MODERATE
            if wtype in (WorkoutType.TEMPO, WorkoutType.INTERVAL) and not quality_due:
                return Intensity.EASY, "easy", WorkoutType.EASY
            return intensity, zone, wtype

        if r >= 75 and f < 40:
            if self.profile.fitness_level.value in ("advanced", "elite"):
                return Intensity.VERY_HARD, "max", WorkoutType.INTERVAL
            return Intensity.HARD, "threshold", WorkoutType.TEMPO

        return Intensity.MODERATE, "aerobic", WorkoutType.MODERATE


    def _recovery_guard(self, feedback: Dict[str, ManualFeedback]) -> Optional[str]:
        """Return ``recovery``/``easy`` when today's cached recovery data is poor.

        This is deliberately conservative and uses only already-loaded data. It
        never calls Garmin. Missing values do not count against the runner.
        """
        fb = self._most_recent_feedback(feedback, days=2)
        if not fb:
            return None

        severe = (
            (fb.sleep_hours is not None and fb.sleep_hours < 5.5)
            or (fb.body_battery is not None and fb.body_battery <= 20)
            or (fb.stress is not None and fb.stress >= 75)
        )
        if severe:
            return "recovery"

        caution = (
            (fb.sleep_hours is not None and fb.sleep_hours < 6.5)
            or (fb.sleep_quality is not None and fb.sleep_quality <= 2)
            or (fb.body_battery is not None and fb.body_battery < 40)
            or (fb.stress is not None and fb.stress >= 60)
        )
        return "easy" if caution else None

    def _days_since_long_run(self, runs: List[NormalizedRun]) -> Optional[int]:
        """Infer the most recent long run from DB-backed run history."""
        if not runs:
            return None
        avg_dist = sum(r.distance_km for r in runs) / len(runs)
        threshold = max(8.0, avg_dist * 1.35)
        candidates = [r for r in runs if r.distance_km >= threshold]
        if not candidates:
            return None
        latest = max(candidates, key=lambda r: r.date)
        return max(0, (datetime.now() - latest.date).days)

    def _days_since_hard_run(self, runs: List[NormalizedRun]) -> Optional[int]:
        """Infer recent hard work using stored HR/RPE, without external API calls."""
        if not runs:
            return None
        threshold_hr = self.profile.get_hr_zones()["threshold"][0]
        candidates = [
            r for r in runs
            if (r.rpe is not None and r.rpe >= 7)
            or (r.avg_hr is not None and r.avg_hr >= threshold_hr)
        ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda r: r.date)
        return max(0, (datetime.now() - latest.date).days)

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_pace(self, pace_min_per_km: float) -> str:
        mins = int(pace_min_per_km)
        secs = int(round((pace_min_per_km - mins) * 60))
        if secs == 60:
            mins += 1
            secs = 0
        return f"{mins}:{secs:02d}/km"

    def _hr_range_for_zone(self, zone: str) -> Optional[Tuple[int, int]]:
        zones = self.profile.get_hr_zones()
        return zones.get(zone)

    def _most_recent_feedback(
        self,
        feedback: Dict[str, ManualFeedback],
        days: int = 2,
    ) -> Optional[ManualFeedback]:
        cutoff = datetime.now() - timedelta(days=days)
        recent = [fb for fb in feedback.values() if fb.date >= cutoff]
        if not recent:
            return None
        return sorted(recent, key=lambda fb: fb.date)[-1]

    def _build_description(
        self,
        wtype: WorkoutType,
        dist: float,
        dur: Optional[float],
        pace_str: Optional[str],
        hr_range: Optional[Tuple[int, int]],
        zone: str,
    ) -> str:
        # Just the workout type name — details live in the targets grid
        type_labels = {
            WorkoutType.EASY:     "Easy run",
            WorkoutType.MODERATE: "Aerobic run",
            WorkoutType.TEMPO:    "Tempo run",
            WorkoutType.INTERVAL: "Interval session",
            WorkoutType.LONG_RUN: "Long run",
            WorkoutType.RECOVERY: "Recovery run",
            WorkoutType.REST:     "Rest day",
        }
        label = type_labels.get(wtype, "Run")
        details = [f"{dist:.1f} km"]
        if dur is not None:
            details.append(f"~{dur:.0f} min")
        if pace_str:
            details.append(pace_str)
        return f"{label} — " + " · ".join(details)

    def _build_rationale(
        self,
        analysis: AnalysisResult,
        baseline_dist: float,
        target_dist: float,
        scale: float,
        runs: List[NormalizedRun],
        ml_source: Optional[List[str]] = None,
    ) -> str:
        change = target_dist - baseline_dist
        direction = (
            f"+{change:.1f} km above your recent average"
            if change > 0.3
            else f"{abs(change):.1f} km below your recent average"
            if change < -0.3
            else "matching your recent average distance"
        )
        source_str = ""
        if ml_source:
            source_str = ""  # ML status shown as badge in UI, not in text
        recovery_bits = []
        labels = {
            "hrv_score": "HRV",
            "body_battery_score": "Body Battery",
            "stress_score": "stress/recovery",
            "resting_hr_score": "resting HR",
            "recovery_quality": "sleep/recovery",
        }
        for key, label in labels.items():
            if key in analysis.readiness_factors:
                recovery_bits.append(f"{label} {analysis.readiness_factors[key]:.0f}/100")
        recovery_text = (" Recovery inputs: " + ", ".join(recovery_bits[:3]) + ".") if recovery_bits else ""
        return (
            f"Based on your last {len(runs)} runs: {direction}.{source_str} "
            f"Readiness {analysis.readiness_score:.0f}/100, "
            f"fatigue {analysis.fatigue_score:.0f}/100 "
            f"(load scale {scale:.2f}×)." + recovery_text
        )

    def _fallback_recommendation(self, analysis: AnalysisResult) -> WorkoutRecommendation:
        """Generic recommendation when there is insufficient history."""
        return WorkoutRecommendation(
            workout_type=WorkoutType.EASY,
            intensity=Intensity.EASY,
            target_distance_km=5.0,
            target_duration_minutes=35.0,
            description="Easy run — 5.0 km (~35 min) at conversational pace",
            rationale=(
                f"Not enough history yet for a personalised prediction "
                f"({self.MIN_RUNS_FOR_PERSONALISATION} runs needed). "
                "Starting with a standard easy run."
            ),
            target_hr_zone="easy",
        )


    # ------------------------------------------------------------------
    # ML-model integration (called by RunningCoach when models are trained)
    # ------------------------------------------------------------------

    def set_trained_models(
        self,
        fatigue_predictor=None,
        pace_predictor=None,
        workout_recommender=None,
    ) -> None:
        """
        Inject trained ML models so predictions use them instead of EWMA.
        Call this after ModelTrainer.train_all() to upgrade predictions.
        """
        self._fatigue_predictor    = fatigue_predictor
        self._pace_predictor       = pace_predictor
        self._workout_recommender  = workout_recommender

    def _get_ml_pace(self, features: Dict) -> Optional[float]:
        """Use trained PacePredictor if available, else return None."""
        pred = getattr(self, "_pace_predictor", None)
        if pred and pred.is_trained:
            try:
                return pred.predict(features)
            except Exception:
                pass
        return None

    def _get_ml_intensity(self, features: Dict):
        """Use trained KNNWorkoutRecommender if available."""
        rec = getattr(self, "_workout_recommender", None)
        if rec and rec.is_trained:
            try:
                from ...schemas import WorkoutType, Intensity
                wtype_str = rec.predict_type(features)
                wtype = WorkoutType(wtype_str)
                # Map WorkoutType → Intensity
                _map = {
                    WorkoutType.RECOVERY: (Intensity.VERY_EASY, "recovery"),
                    WorkoutType.EASY:     (Intensity.EASY,      "easy"),
                    WorkoutType.MODERATE: (Intensity.MODERATE,  "aerobic"),
                    WorkoutType.TEMPO:    (Intensity.HARD,       "threshold"),
                    WorkoutType.INTERVAL: (Intensity.VERY_HARD,  "max"),
                    WorkoutType.LONG_RUN: (Intensity.EASY,       "easy"),
                    WorkoutType.REST:     (Intensity.VERY_EASY,  "recovery"),
                }
                intensity, zone = _map.get(wtype, (Intensity.EASY, "easy"))
                return intensity, zone, wtype
            except Exception:
                pass
        return None
