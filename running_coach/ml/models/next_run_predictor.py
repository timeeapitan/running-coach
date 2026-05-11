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

        # --- Step 2: distance — fatigue-aware scaling ---
        scale       = self._load_scale_factor(analysis)
        target_dist = baseline_dist * scale

        recent_max = self._recent_max_distance(runs, days=14)
        if recent_max and target_dist > recent_max * 1.10:
            target_dist = recent_max * 1.10
        target_dist = round(max(3.0, min(target_dist, 35.0)) * 2) / 2

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

        target_dur = round(target_dist * target_pace, 1) if target_pace else None

        # --- Step 4: intensity / workout type — try ML first ---
        ml_intensity = self._get_ml_intensity(features)

        if ml_intensity is not None:
            intensity, zone, wtype = ml_intensity
            ml_source.append("type:ML")
        else:
            intensity, zone, wtype = self._choose_intensity(analysis, feedback)
            ml_source.append("type:rules")

        # --- Step 5: format output ---
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

        # Sleep/mood adjustment from most recent feedback entry
        recent_fb = self._most_recent_feedback(feedback, days=2)
        if recent_fb:
            if recent_fb.sleep_quality and recent_fb.sleep_quality >= 4:
                pace *= 0.98  # well rested → 2% faster
            if recent_fb.mood and recent_fb.mood <= 2:
                pace *= 1.02  # poor mood → 2% slower

        return round(pace, 2)

    # ------------------------------------------------------------------
    # Intensity selection
    # ------------------------------------------------------------------

    def _choose_intensity(
        self,
        analysis: AnalysisResult,
        feedback: Dict[str, ManualFeedback],
    ) -> Tuple[Intensity, str, WorkoutType]:
        """
        Returns (Intensity, hr_zone_name, WorkoutType).
        Decision tree based on readiness, fatigue, and recent hard days.
        """
        r = analysis.readiness_score
        f = analysis.fatigue_score

        if f >= 70 or r < 35:
            return Intensity.VERY_EASY, "recovery", WorkoutType.RECOVERY

        if f >= 50 or r < 55:
            return Intensity.EASY, "easy", WorkoutType.EASY

        if r >= 75 and f < 40:
            if self.profile.fitness_level.value in ("advanced", "elite"):
                return Intensity.VERY_HARD, "max", WorkoutType.INTERVAL
            return Intensity.HARD, "threshold", WorkoutType.TEMPO

        return Intensity.MODERATE, "aerobic", WorkoutType.MODERATE

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
        return type_labels.get(wtype, "Run")

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
            using_ml = any("ML" in s for s in ml_source)
            source_str = " [ML personalised]" if using_ml else " [rule-based]"
        return (
            f"Based on your last {len(runs)} runs: {direction}.{source_str} "
            f"Readiness {analysis.readiness_score:.0f}/100, "
            f"fatigue {analysis.fatigue_score:.0f}/100 "
            f"(load scale {scale:.2f}×)."
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
