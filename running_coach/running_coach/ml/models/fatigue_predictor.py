"""
FatiguePredictor — predicts fatigue score from training features.

Uses GradientBoostingRegressor as the default backend.
Can fall back to RidgeRegression when sample count is low.

The model is trained on historical (features, fatigue_score) pairs produced
by the rule-based FatigueCalculator, then learns to generalise to new data.
Once trained it can replace or blend with the rule-based score.
"""

from typing import Dict, List, Optional, Tuple

from .base import BaseMLModel
from .gradient_boosting import GradientBoostingRegressor
from .linear_regression import RidgeRegression


# Minimum samples to use GBM; below this, fall back to Ridge (less overfitting)
_GBM_MIN_SAMPLES = 20


class FatiguePredictor(BaseMLModel):
    """
    Predicts runner fatigue (0-100) from training load features.

    Automatically selects the best model backend based on dataset size:
      - < 20 samples → RidgeRegression (avoids overfitting)
      - ≥ 20 samples → GradientBoostingRegressor (captures non-linearities)

    Usage::

        predictor = FatiguePredictor(model_path="models/fatigue.json")
        metrics   = predictor.train(features_list, fatigue_scores)
        score     = predictor.predict(today_features)
        predictor.save()
    """

    def __init__(self, model_path: Optional[str] = None):
        super().__init__(model_path)
        self._backend: Optional[BaseMLModel] = None
        self._backend_name: str = "none"

    # ------------------------------------------------------------------

    def train(
        self,
        features: List[Dict[str, float]],
        labels:   List[float],
    ) -> Dict[str, float]:
        """
        Train fatigue prediction model.

        Args:
            features: List of feature dicts (from ml.features.extract_features)
            labels:   Fatigue scores 0-100 (from FatigueCalculator.calculate)

        Returns:
            Dict of evaluation metrics: mae, r2, n_samples, backend
        """
        n = len(features)
        if n < 2:
            raise ValueError(f"Need at least 2 training samples, got {n}")

        if n >= _GBM_MIN_SAMPLES:
            self._backend      = GradientBoostingRegressor(n_estimators=80, max_depth=3)
            self._backend_name = "gbm"
        else:
            self._backend      = RidgeRegression(alpha=0.5)
            self._backend_name = "ridge"

        metrics = self._backend.train(features, labels)
        self.is_trained = True

        # Mirror state for persistence
        self._state = {
            "backend":      self._backend_name,
            "backend_state":self._backend._state,
            "backend_params": {
                "n_estimators":  getattr(self._backend, "n_estimators",  None),
                "max_depth":     getattr(self._backend, "max_depth",     None),
                "learning_rate": getattr(self._backend, "learning_rate", None),
                "alpha":         getattr(self._backend, "alpha",         None),
            },
        }
        metrics["backend"] = self._backend_name
        return metrics

    def predict(self, features: Dict[str, float]) -> float:
        """Return predicted fatigue score (clamped to 0-100)."""
        if not self.is_trained or self._backend is None:
            raise RuntimeError("FatiguePredictor not trained. Call train() first.")
        raw = self._backend.predict(features)
        return max(0.0, min(100.0, raw))

    def predict_with_confidence(
        self, features: Dict[str, float]
    ) -> Tuple[float, str]:
        """
        Returns (predicted_fatigue, confidence_label).
        Confidence is derived from how far the sample is from the training
        distribution (using feature z-scores).
        """
        pred = self.predict(features)
        conf = self._estimate_confidence(features)
        return pred, conf

    def feature_importance(self) -> Dict[str, float]:
        """Return per-feature importance (Ridge: |weight|, GBM: not supported)."""
        if not self.is_trained:
            return {}
        if isinstance(self._backend, RidgeRegression):
            return self._backend.feature_importance()
        # GBM doesn't expose per-feature importance in this implementation
        return {"note": "feature importance not available for GBM backend"}

    # ------------------------------------------------------------------

    def _estimate_confidence(self, features: Dict[str, float]) -> str:
        """
        Simple confidence estimate based on whether the feature values
        are within the range seen during training.
        """
        if self._backend is None:
            return "unknown"
        means = self._backend._means
        stds  = self._backend._stds
        if not means:
            return "unknown"

        z_scores = []
        for k, v in features.items():
            if k in means and stds.get(k, 0) > 0:
                z_scores.append(abs(v - means[k]) / stds[k])

        if not z_scores:
            return "unknown"
        avg_z = sum(z_scores) / len(z_scores)

        if avg_z < 1.0:
            return "high"
        elif avg_z < 2.0:
            return "medium"
        else:
            return "low"

    def _restore_from_state(self) -> None:
        backend_name = self._state.get("backend", "ridge")
        params       = self._state.get("backend_params", {})
        inner_state  = self._state.get("backend_state", {})

        if backend_name == "gbm":
            self._backend = GradientBoostingRegressor(
                n_estimators  = params.get("n_estimators",  80),
                max_depth     = params.get("max_depth",      3),
                learning_rate = params.get("learning_rate",  0.1),
            )
        else:
            self._backend = RidgeRegression(
                alpha = params.get("alpha", 0.5),
            )

        self._backend._state      = inner_state
        self._backend.is_trained  = True
        self._backend._restore_from_state()
        self._backend_name = backend_name
