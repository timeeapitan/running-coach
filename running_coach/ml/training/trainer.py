"""
ModelTrainer — orchestrates the full ML training pipeline.

What each model learns from your data:

  FatiguePredictor:   features(history) → actual fatigue score
                      Labels come from FatigueCalculator on a sliding window.
                      Meaningful once load varies (e.g. hard week followed by easy week).

  PacePredictor:      features(history) → actual pace of the NEXT run
                      Labels are your real Garmin pace values.
                      Learns: "when you've run X km this week at Y HR, you tend to run at Z pace."

  WorkoutRecommender: features(history) → workout type of the NEXT run
                      Labels come from your actual run types in history (easy/long/tempo etc.)
                      OR from the rule engine when runs don't vary enough.
                      Only trains when at least 3 different workout types appear in your history.
"""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..features import extract_features
from ..models.fatigue_predictor import FatiguePredictor
from ..models.pace_predictor import PacePredictor
from ..models.workout_recommender import KNNWorkoutRecommender
from ...analysis.fatigue import FatigueCalculator
from ...analysis.consistency import ConsistencyCalculator
from ...analysis.readiness import ReadinessCalculator
from ...coaching.rules import CoachingRules
from ...config import ANALYSIS_CONFIG
from ...schemas import NormalizedRun, ManualFeedback, RunnerProfile, AnalysisResult
from ...schemas.enums import ActivityType


# Map activity type → workout label used for KNN training
_ACTIVITY_TO_WORKOUT = {
    ActivityType.OUTDOOR_RUN:    None,   # determined by distance vs weekly avg
    ActivityType.TRAIL_RUN:      "easy",
    ActivityType.TREADMILL_RUN:  "easy",
}


class ModelTrainer:
    """Full training pipeline for all ML models."""

    MIN_RUNS_TO_TRAIN = 10
    MIN_WORKOUT_TYPES = 3   # need variety before KNN is worth training

    def __init__(self, profile: RunnerProfile, model_dir: str = "./models"):
        self.profile   = profile
        self.model_dir = model_dir
        os.makedirs(model_dir, exist_ok=True)

        self.fatigue_predictor = FatiguePredictor(
            model_path=os.path.join(model_dir, "fatigue.json"))
        self.pace_predictor = PacePredictor(
            model_path=os.path.join(model_dir, "pace.json"))
        self.workout_recommender = KNNWorkoutRecommender(
            k=5, model_path=os.path.join(model_dir, "workout_knn.json"))

        self._fatigue_calc     = FatigueCalculator(profile, ANALYSIS_CONFIG)
        self._consistency_calc = ConsistencyCalculator(profile, ANALYSIS_CONFIG)
        self._readiness_calc   = ReadinessCalculator(profile, ANALYSIS_CONFIG)
        self._rules            = CoachingRules(profile)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def train_all(
        self,
        runs:     List[NormalizedRun],
        feedback: Optional[Dict[str, ManualFeedback]] = None,
    ) -> Dict[str, Dict]:
        feedback = feedback or {}

        if len(runs) < self.MIN_RUNS_TO_TRAIN:
            return {"error": f"Need at least {self.MIN_RUNS_TO_TRAIN} runs (have {len(runs)})"}

        dataset = self._build_dataset(runs, feedback)
        if len(dataset) < 3:
            return {"error": "Not enough labelled examples"}

        results: Dict[str, Dict] = {}

        # --- Fatigue ---
        try:
            feats  = [ex["features"]       for ex in dataset]
            labels = [ex["fatigue_score"]  for ex in dataset]
            # Only train if labels have meaningful variance
            label_range = max(labels) - min(labels)
            if label_range < 2.0:
                results["fatigue"] = {"skipped": f"labels too uniform (range {label_range:.1f}) — need runs with varying load"}
            else:
                results["fatigue"] = self.fatigue_predictor.train(feats, labels)
                self.fatigue_predictor.save()
        except Exception as e:
            results["fatigue"] = {"error": str(e)}

        # --- Pace (trained on your actual run paces) ---
        try:
            pace_ex = [ex for ex in dataset if ex["pace"] is not None]
            if len(pace_ex) < 5:
                results["pace"] = {"skipped": "not enough runs with pace data"}
            else:
                pf = [ex["features"] for ex in pace_ex]
                pl = [ex["pace"]     for ex in pace_ex]
                pace_range = max(pl) - min(pl)
                if pace_range < 0.3:
                    results["pace"] = {"skipped": f"pace too uniform (range {pace_range:.2f} min/km) — model would not add value over EWMA"}
                else:
                    results["pace"] = self.pace_predictor.train(pf, pl)
                    self.pace_predictor.save()
        except Exception as e:
            results["pace"] = {"error": str(e)}

        # --- Workout type (only when enough variety in your actual runs) ---
        try:
            wf = [ex["features"]      for ex in dataset]
            wl = [ex["workout_label"] for ex in dataset]
            n_types = len(set(wl))
            if n_types < self.MIN_WORKOUT_TYPES:
                results["workout"] = {
                    "skipped": f"only {n_types} workout type(s) in history — "
                               f"need {self.MIN_WORKOUT_TYPES}+ (easy/tempo/long/intervals) "
                               f"before KNN adds value"
                }
            else:
                k = min(5, len(wf) // 3)
                self.workout_recommender.k = k
                results["workout"] = self.workout_recommender.train(wf, wl)
                self.workout_recommender.save()
        except Exception as e:
            results["workout"] = {"error": str(e)}

        return results

    def retrain(self, all_runs, feedback=None):
        return self.train_all(all_runs, feedback or {})

    def load_all(self) -> Dict[str, bool]:
        results = {}
        for name, model in [
            ("fatigue",  self.fatigue_predictor),
            ("pace",     self.pace_predictor),
            ("workout",  self.workout_recommender),
        ]:
            try:
                model.load()
                results[name] = True
            except FileNotFoundError:
                results[name] = False
        return results

    def training_summary(self) -> str:
        lines = ["ML Model Status", "─" * 30]
        for name, model in [
            ("Fatigue predictor",     self.fatigue_predictor),
            ("Pace predictor",        self.pace_predictor),
            ("Workout recommender",   self.workout_recommender),
        ]:
            status = "Trained" if model.is_trained else "Not trained yet"
            lines.append(f"  {name:25s}: {status}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Dataset construction
    # ------------------------------------------------------------------

    def _build_dataset(
        self,
        runs:     List[NormalizedRun],
        feedback: Dict[str, ManualFeedback],
    ) -> List[Dict]:
        """
        For each run at position i (with at least 5 prior runs):
          features     = extract_features(runs[:i], feedback)
          fatigue      = FatigueCalculator on runs[:i]
          pace         = runs[i].avg_pace_min_per_km  (what actually happened)
          workout_label = inferred from runs[i] distance vs weekly average
        """
        sorted_runs = sorted(runs, key=lambda r: r.date)
        dataset = []
        min_window = 5

        # Pre-compute weekly averages for workout labelling
        avg_weekly_km = self._avg_weekly_km(sorted_runs)

        for i in range(min_window, len(sorted_runs)):
            history  = sorted_runs[:i]
            next_run = sorted_runs[i]

            features = extract_features(history, feedback, self.profile)

            fatigue_score, _ = self._fatigue_calc.calculate(history, feedback)
            consistency_score, _ = self._consistency_calc.calculate(history, feedback)
            readiness_score, _ = self._readiness_calc.calculate(
                history, feedback,
                fatigue_score=fatigue_score,
                consistency_score=consistency_score,
            )

            # Workout label: inferred from the actual distance of the next run
            workout_label = self._infer_workout_label(next_run, avg_weekly_km)

            dataset.append({
                "features":       features,
                "fatigue_score":  fatigue_score,
                "readiness_score":readiness_score,
                "pace":           next_run.avg_pace_min_per_km,
                "workout_label":  workout_label,
                "date":           next_run.date.isoformat(),
            })

        return dataset

    def _infer_workout_label(self, run: NormalizedRun, avg_weekly_km: float) -> str:
        """
        Infer workout type from the actual run distance relative to your typical week.
        Long run = significantly longer than your per-run average.
        Easy = shorter or typical.
        """
        per_run_avg = avg_weekly_km / max(1, self.profile.runs_per_week)

        if run.distance_km >= per_run_avg * 1.6:
            return "long_run"
        elif run.distance_km <= per_run_avg * 0.7:
            return "easy"
        else:
            return "moderate"

    def _avg_weekly_km(self, runs: List[NormalizedRun]) -> float:
        if not runs:
            return 20.0
        total_days = max(1, (runs[-1].date - runs[0].date).days)
        total_km   = sum(r.distance_km for r in runs)
        return (total_km / total_days) * 7
