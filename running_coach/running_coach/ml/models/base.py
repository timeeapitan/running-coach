"""
Base class for all ML models in this package.

Uses only Python stdlib — no external dependencies required.
Models persist themselves as JSON so predictions survive restarts.
"""

import json
import os
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class BaseMLModel(ABC):
    """
    Abstract base for all trainable models.

    Subclasses must implement:
      - train(features, labels) -> metrics dict
      - predict(features) -> float

    Persistence (save/load) is handled here via JSON.
    """

    def __init__(self, model_path: Optional[str] = None):
        self.model_path   = model_path
        self.is_trained   = False
        self._state: Dict = {}   # subclasses store learned params here

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------

    @abstractmethod
    def train(self, features: List[Dict[str, float]], labels: List[float]) -> Dict[str, float]:
        """
        Fit the model on feature dicts and scalar labels.
        Returns evaluation metrics (e.g. {'mae': 3.2, 'r2': 0.87}).
        """

    @abstractmethod
    def predict(self, features: Dict[str, float]) -> float:
        """Return a single scalar prediction for a feature dict."""

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: Optional[str] = None) -> str:
        """Serialise model state to JSON. Returns the path written."""
        path = path or self.model_path
        if not path:
            raise ValueError("No path specified for save()")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        payload = {
            "class":      type(self).__name__,
            "is_trained": self.is_trained,
            "state":      self._state,
        }
        with open(path, "w") as f:
            json.dump(payload, f, indent=2)
        return path

    def load(self, path: Optional[str] = None) -> None:
        """Restore model state from JSON."""
        path = path or self.model_path
        if not path or not os.path.exists(path):
            raise FileNotFoundError(f"Model file not found: {path}")
        with open(path) as f:
            payload = json.load(f)
        self.is_trained = payload.get("is_trained", False)
        self._state     = payload.get("state", {})
        self._restore_from_state()

    def _restore_from_state(self) -> None:
        """Called after load() — subclasses override to rebuild internal objects."""

    # ------------------------------------------------------------------
    # Shared maths helpers (stdlib only)
    # ------------------------------------------------------------------

    @staticmethod
    def _mean(values: List[float]) -> float:
        return sum(values) / len(values) if values else 0.0

    @staticmethod
    def _variance(values: List[float]) -> float:
        if len(values) < 2:
            return 0.0
        m = BaseMLModel._mean(values)
        return sum((x - m) ** 2 for x in values) / len(values)

    @staticmethod
    def _std(values: List[float]) -> float:
        return BaseMLModel._variance(values) ** 0.5

    @staticmethod
    def _dot(a: List[float], b: List[float]) -> float:
        return sum(x * y for x, y in zip(a, b))

    @staticmethod
    def _mae(predictions: List[float], labels: List[float]) -> float:
        return BaseMLModel._mean([abs(p - l) for p, l in zip(predictions, labels)])

    @staticmethod
    def _r2(predictions: List[float], labels: List[float]) -> float:
        mean_y  = BaseMLModel._mean(labels)
        ss_tot  = sum((y - mean_y) ** 2 for y in labels)
        ss_res  = sum((y - p) ** 2 for y, p in zip(labels, predictions))
        return 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    @staticmethod
    def _normalize_features(
        features_list: List[Dict[str, float]],
    ) -> Tuple[List[List[float]], List[str], Dict[str, float], Dict[str, float]]:
        """
        Normalise feature dicts to z-scores.
        Returns (matrix, feature_names, means, stds).
        """
        if not features_list:
            return [], [], {}, {}
        keys   = sorted(features_list[0].keys())
        cols   = {k: [row.get(k, 0.0) for row in features_list] for k in keys}
        means  = {k: BaseMLModel._mean(cols[k])  for k in keys}
        stds   = {k: BaseMLModel._std(cols[k]) or 1.0 for k in keys}
        matrix = [
            [(row.get(k, 0.0) - means[k]) / stds[k] for k in keys]
            for row in features_list
        ]
        return matrix, keys, means, stds
