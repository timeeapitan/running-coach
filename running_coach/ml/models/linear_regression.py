"""
Regularised Linear Regression (Ridge) — trained with gradient descent.

Used internally by FatiguePredictor and PacePredictor.
No external dependencies; pure Python stdlib.
"""

import math
from typing import Dict, List, Optional, Tuple

from .base import BaseMLModel


class RidgeRegression(BaseMLModel):
    """
    Ridge regression (L2-regularised linear model) trained via batch
    gradient descent with early stopping.

    Suitable for:
      - Fatigue score prediction
      - Pace prediction
      - Any continuous target in a 0-100 range
    """

    def __init__(
        self,
        model_path:   Optional[str] = None,
        alpha:        float = 0.1,    # L2 regularisation strength
        learning_rate:float = 0.01,
        max_iter:     int   = 2000,
        tol:          float = 1e-5,   # early-stop tolerance
    ):
        super().__init__(model_path)
        self.alpha         = alpha
        self.learning_rate = learning_rate
        self.max_iter      = max_iter
        self.tol           = tol

        # Learned parameters (populated by train())
        self._weights:      List[float] = []
        self._bias:         float       = 0.0
        self._feature_keys: List[str]   = []
        self._means:        Dict[str, float] = {}
        self._stds:         Dict[str, float] = {}

    # ------------------------------------------------------------------

    def train(
        self,
        features: List[Dict[str, float]],
        labels:   List[float],
    ) -> Dict[str, float]:
        if len(features) != len(labels) or len(features) < 2:
            raise ValueError("Need at least 2 samples to train")

        X, keys, means, stds = self._normalize_features(features)
        y = labels

        self._feature_keys = keys
        self._means        = means
        self._stds         = stds

        n, d = len(X), len(keys)
        w     = [0.0] * d
        b     = self._mean(y)       # bias initialised to label mean
        lr    = self.learning_rate
        alpha = self.alpha

        prev_loss = float("inf")

        for iteration in range(self.max_iter):
            # Forward pass
            preds = [self._dot(w, x) + b for x in X]

            # Gradients
            errors = [p - t for p, t in zip(preds, y)]
            grad_w = [
                (sum(errors[i] * X[i][j] for i in range(n)) / n)
                + alpha * w[j]          # L2 gradient
                for j in range(d)
            ]
            grad_b = self._mean(errors)

            # Update
            w = [w[j] - lr * grad_w[j] for j in range(d)]
            b = b - lr * grad_b

            # Loss (MSE + L2 penalty)
            mse  = self._mean([e ** 2 for e in errors])
            loss = mse + alpha * sum(wj ** 2 for wj in w)

            if abs(prev_loss - loss) < self.tol:
                break
            prev_loss = loss

        self._weights   = w
        self._bias      = b
        self.is_trained = True

        # Persist learned params into _state for save/load
        self._state = {
            "weights":      w,
            "bias":         b,
            "feature_keys": keys,
            "means":        means,
            "stds":         stds,
        }

        preds_final = [self._dot(w, x) + b for x in X]
        return {
            "mae":        round(self._mae(preds_final, y), 3),
            "r2":         round(self._r2(preds_final, y),  3),
            "iterations": iteration + 1,
            "n_samples":  n,
        }

    def predict(self, features: Dict[str, float]) -> float:
        if not self.is_trained:
            raise RuntimeError("Model is not trained yet. Call train() first.")
        x = self._encode(features)
        return self._dot(self._weights, x) + self._bias

    def predict_batch(self, features_list: List[Dict[str, float]]) -> List[float]:
        return [self.predict(f) for f in features_list]

    def feature_importance(self) -> Dict[str, float]:
        """Return absolute weight per feature (proxy for importance)."""
        if not self.is_trained:
            return {}
        return {
            k: round(abs(w), 4)
            for k, w in zip(self._feature_keys, self._weights)
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _encode(self, features: Dict[str, float]) -> List[float]:
        """Normalise a single feature dict using training statistics."""
        return [
            (features.get(k, 0.0) - self._means.get(k, 0.0))
            / (self._stds.get(k, 1.0) or 1.0)
            for k in self._feature_keys
        ]

    def _restore_from_state(self) -> None:
        self._weights      = self._state.get("weights", [])
        self._bias         = self._state.get("bias",    0.0)
        self._feature_keys = self._state.get("feature_keys", [])
        self._means        = self._state.get("means",   {})
        self._stds         = self._state.get("stds",    {})
