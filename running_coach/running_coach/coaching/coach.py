"""
RunningCoach — the main entry point for the running coach application.

Combines:
  - Rule-based analysis (always available)
  - ML-powered next-run prediction (personalises over time)
  - Full ML training pipeline (improves with each new run logged)
"""

import os
from typing import Dict, List, Optional

from ..analysis import RunningAnalyzer
from ..ml.models.next_run_predictor import NextRunPredictor
from ..ml.training.trainer import ModelTrainer
from ..schemas import (
    AnalysisResult,
    ManualFeedback,
    NormalizedRun,
    RunnerProfile,
    WorkoutRecommendation,
)
from .rules import CoachingRules
from .templates import format_workout_message


class RunningCoach:
    """
    High-level coaching interface.

    The coach gets smarter over time:
      - With < 5 runs:   generic easy-run recommendation
      - With 5-9 runs:   EWMA-smoothed personalised prediction
      - With ≥ 10 runs:  full ML pipeline trained on your own data
      - Each new run:    models retrain → predictions improve

    Usage::

        profile = RunnerProfile(max_hr=185, resting_hr=55, runs_per_week=4)
        coach   = RunningCoach(profile, model_dir="./my_models")

        # Log a new run (triggers retraining):
        coach.log_run(runs, feedback)

        # Get advice:
        print(coach.get_advice(runs, feedback))

        # Just the prediction:
        rec = coach.predict_next_run(runs, feedback)
    """

    def __init__(
        self,
        profile:    RunnerProfile,
        model_dir:  str = "./models",
    ):
        self.profile   = profile
        self.analyzer  = RunningAnalyzer(profile)
        self.rules     = CoachingRules(profile)
        self.predictor = NextRunPredictor(profile)
        self.trainer   = ModelTrainer(profile, model_dir=model_dir)

        # Try to load previously trained models
        self._load_ml_models()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        runs:     List[NormalizedRun],
        feedback: Optional[Dict[str, ManualFeedback]] = None,
    ) -> AnalysisResult:
        """Run the full rule-based analysis."""
        return self.analyzer.analyze(runs, feedback or {})

    def detect_and_update_fitness_level(
        self,
        runs: List[NormalizedRun],
    ) -> dict:
        """Auto-detect fitness level from run history and update profile if changed."""
        from ..analysis.fitness_level import detect_fitness_level
        result = detect_fitness_level(runs, self.profile)
        if result["changed"]:
            self.profile.fitness_level = result["level"]
            # Update rules engine with new level
            self.rules = CoachingRules(self.profile)
        return result

    def recommend(
        self,
        analysis: AnalysisResult,
        runs: Optional[List[NormalizedRun]] = None,
    ) -> WorkoutRecommendation:
        """Rule-based recommendation from an AnalysisResult."""
        return self.rules.recommend(analysis, runs or [])

    def predict_next_run(
        self,
        runs:     List[NormalizedRun],
        feedback: Optional[Dict[str, ManualFeedback]] = None,
    ) -> WorkoutRecommendation:
        """
        Predict the optimal next run.

        If ML models are trained, uses them for pace and workout-type.
        Otherwise falls back to EWMA smoothing.
        """
        feedback = feedback or {}
        analysis = self.analyze(runs, feedback)

        # Inject ML models into predictor if trained
        self._inject_ml_models()

        rec = self.predictor.predict(runs, analysis, feedback)
        # Inject run steps from rules if predictor didn't set them
        if not rec.steps:
            rec.steps = self.rules.recommend(analysis, runs).steps
            rec.terrain = self.rules._terrain_label(runs)
        return rec

    def log_run(
        self,
        all_runs: List[NormalizedRun],
        feedback: Optional[Dict[str, ManualFeedback]] = None,
    ) -> Dict:
        """
        Call this after logging a new run.
        Triggers retraining of all ML models on the updated history.
        Returns training metrics.
        """
        metrics = self.trainer.retrain(all_runs, feedback or {})
        self._inject_ml_models()
        return metrics

    def train_models(
        self,
        runs:     List[NormalizedRun],
        feedback: Optional[Dict[str, ManualFeedback]] = None,
    ) -> Dict:
        """Explicitly train all ML models on the provided history."""
        metrics = self.trainer.train_all(runs, feedback or {})
        self._inject_ml_models()
        return metrics

    def get_advice(
        self,
        runs:          List[NormalizedRun],
        feedback:      Optional[Dict[str, ManualFeedback]] = None,
        use_predictor: bool = True,
    ) -> str:
        """
        Full coaching message including analysis and next-run recommendation.

        Args:
            runs:          Training history.
            feedback:      Optional manual feedback keyed by ISO date.
            use_predictor: Use ML/EWMA predictor when enough data exists.
        """
        feedback = feedback or {}
        analysis = self.analyze(runs, feedback)

        if use_predictor and len(runs) >= NextRunPredictor.MIN_RUNS_FOR_PERSONALISATION:
            self._inject_ml_models()
            recommendation = self.predictor.predict(runs, analysis, feedback)
        else:
            recommendation = self.rules.recommend(analysis)

        return format_workout_message(recommendation, analysis)

    def model_status(self) -> str:
        """Return a summary of which ML models are trained."""
        return self.trainer.training_summary()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load_ml_models(self) -> None:
        """Load previously saved models from disk (silent on failure)."""
        self.trainer.load_all()
        self._inject_ml_models()

    def _inject_ml_models(self) -> None:
        """Pass trained ML models into the predictor."""
        self.predictor.set_trained_models(
            fatigue_predictor   = self.trainer.fatigue_predictor,
            pace_predictor      = self.trainer.pace_predictor,
            workout_recommender = self.trainer.workout_recommender,
        )
