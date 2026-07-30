"""
PacePredictor — predicts the runner's optimal pace for the next run.

Trained on (features, actual_pace) pairs from historical runs.
Uses RidgeRegression (pace is fairly linear with load/fatigue/fitness).

Once trained, this replaces the EWMA smoothing in NextRunPredictor
with a personalised model that accounts for all features simultaneously.
"""

from typing import Dict, List, Optional, Tuple

from .base import BaseMLModel
from .linear_regression import RidgeRegression


class PacePredictor(BaseMLModel):
    """
    Predicts next-run target pace (min/km) from training features.

    Labels should be actual pace values observed after each training session.
    The model learns how fatigue, sleep, HR, and load affect achievable pace.

    Usage::

        predictor = PacePredictor(model_path="models/pace.json")
        metrics   = predictor.train(features_list, pace_labels)
        pace      = predictor.predict(today_features)  # min/km
        predictor.save()
    """

    # Sensible bounds for clamping predictions
    MIN_PACE = 3.0   # elite 5k pace (min/km)
    MAX_PACE = 12.0  # very slow jogger (min/km)

    def __init__(self, model_path: Optional[str] = None):
        super().__init__(model_path)
        self._backend = RidgeRegression(alpha=0.3, learning_rate=0.005, max_iter=3000)

    def train(
        self,
        features: List[Dict[str, float]],
        labels:   List[float],
    ) -> Dict[str, float]:
        """
        Train pace predictor.

        Args:
            features: Feature dicts (same format as FatiguePredictor).
            labels:   Actual pace in min/km for each training example.

        Returns:
            Evaluation metrics dict.
        """
        if len(features) < 3:
            raise ValueError("Need at least 3 pace samples to train")

        metrics = self._backend.train(features, labels)
        self.is_trained = True

        self._state = {
            "backend_state": self._backend._state,
        }
        return metrics

    def predict(self, features: Dict[str, float]) -> float:
        """Return predicted pace (min/km), clamped to [3.0, 12.0]."""
        if not self.is_trained:
            raise RuntimeError("PacePredictor not trained. Call train() first.")
        raw = self._backend.predict(features)
        return max(self.MIN_PACE, min(self.MAX_PACE, raw))

    def predict_with_range(
        self,
        features: Dict[str, float],
        tolerance: float = 0.15,
    ) -> Tuple[float, float, float]:
        """
        Returns (low, target, high) pace range for a training zone.
        Tolerance of 0.15 min/km = ~9 seconds per km.
        """
        target = self.predict(features)
        return (
            max(self.MIN_PACE, target - tolerance),
            target,
            min(self.MAX_PACE, target + tolerance),
        )

    def feature_importance(self) -> Dict[str, float]:
        return self._backend.feature_importance()

    def _restore_from_state(self) -> None:
        self._backend._state = self._state.get("backend_state", {})
        self._backend.is_trained = True
        self._backend._restore_from_state()
