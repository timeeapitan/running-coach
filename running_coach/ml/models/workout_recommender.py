"""
MLWorkoutRecommender — classifies the best workout type for the next session.

Uses a k-Nearest Neighbours classifier (pure Python).
Trained on historical (features, workout_type) pairs — either manually labelled
or auto-labelled from the rule-based CoachingRules.

KNN is ideal here because:
  - No assumptions about feature distributions
  - Naturally handles the sparse early-data case
  - Easily interpretable ("your situation matches X past sessions")
"""

import math
from typing import Dict, List, Optional, Tuple

from .base import BaseMLModel
from ...schemas import WorkoutType


class KNNWorkoutRecommender(BaseMLModel):
    """
    k-Nearest Neighbours classifier for workout type recommendation.

    Labels should be WorkoutType string values (e.g. "easy", "tempo").

    Usage::

        rec = KNNWorkoutRecommender(k=5, model_path="models/workout_knn.json")
        metrics = rec.train(features_list, workout_type_labels)
        wtype   = rec.predict_type(today_features)
        rec.save()
    """

    def __init__(
        self,
        k:          int            = 5,
        model_path: Optional[str] = None,
    ):
        super().__init__(model_path)
        self.k               = k
        self._X_train:       List[List[float]] = []
        self._y_train:       List[str]         = []
        self._feature_keys:  List[str]         = []
        self._means:         Dict[str, float]  = {}
        self._stds:          Dict[str, float]  = {}

    def train(
        self,
        features: List[Dict[str, float]],
        labels:   List[str],            # WorkoutType.value strings
    ) -> Dict[str, float]:
        """
        Fit KNN on training examples.
        KNN doesn't have a fit step per se — it memorises training data.
        Returns accuracy on the training set (optimistic but useful for sanity check).
        """
        if len(features) < self.k:
            raise ValueError(f"Need at least k={self.k} samples to train KNN")

        X, keys, means, stds = self._normalize_features(features)

        self._X_train      = X
        self._y_train      = list(labels)
        self._feature_keys = keys
        self._means        = means
        self._stds         = stds

        self.is_trained    = True

        # Train-set accuracy (must be after _y_train is set)
        preds   = [self._knn_vote(x) for x in X]
        correct = sum(p == l for p, l in zip(preds, labels))
        acc     = correct / len(labels)

        self._state = {
            "k":            self.k,
            "X_train":      X,
            "y_train":      list(labels),
            "feature_keys": keys,
            "means":        means,
            "stds":         stds,
        }
        return {
            "accuracy":   round(acc, 3),
            "n_samples":  len(labels),
            "n_classes":  len(set(labels)),
        }

    def predict(self, features: Dict[str, float]) -> float:
        """
        Returns a numeric encoding of the predicted workout type.
        (Satisfies BaseMLModel interface; prefer predict_type() for real use.)
        """
        label = self.predict_type(features)
        _type_to_int = {w.value: i for i, w in enumerate(WorkoutType)}
        return float(_type_to_int.get(label, 0))

    def predict_type(self, features: Dict[str, float]) -> str:
        """Return the predicted WorkoutType value string."""
        if not self.is_trained:
            raise RuntimeError("Model not trained. Call train() first.")
        x = self._encode(features)
        return self._knn_vote(x)

    def predict_with_neighbours(
        self,
        features: Dict[str, float],
    ) -> Tuple[str, List[Tuple[str, float]]]:
        """
        Returns (predicted_type, [(neighbour_label, distance), ...]).
        Useful for explaining why a workout was recommended.
        """
        x         = self._encode(features)
        distances = self._sorted_distances(x)
        top_k     = distances[:self.k]
        vote      = self._knn_vote(x)
        return vote, [(self._y_train[i], d) for i, d in top_k]

    # ------------------------------------------------------------------

    def _encode(self, features: Dict[str, float]) -> List[float]:
        return [
            (features.get(k, 0.0) - self._means.get(k, 0.0))
            / (self._stds.get(k, 1.0) or 1.0)
            for k in self._feature_keys
        ]

    def _euclidean(self, a: List[float], b: List[float]) -> float:
        return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))

    def _sorted_distances(self, x: List[float]) -> List[Tuple[int, float]]:
        dists = [(i, self._euclidean(x, xi)) for i, xi in enumerate(self._X_train)]
        return sorted(dists, key=lambda t: t[1])

    def _knn_vote(self, x: List[float]) -> str:
        """Majority vote among k nearest neighbours."""
        top_k  = self._sorted_distances(x)[:self.k]
        votes: Dict[str, float] = {}
        for i, dist in top_k:
            label  = self._y_train[i]
            weight = 1.0 / (dist + 1e-9)     # distance-weighted voting
            votes[label] = votes.get(label, 0.0) + weight
        return max(votes, key=lambda lbl: votes[lbl])

    def _restore_from_state(self) -> None:
        self.k             = self._state.get("k", self.k)
        self._X_train      = self._state.get("X_train",      [])
        self._y_train      = self._state.get("y_train",      [])
        self._feature_keys = self._state.get("feature_keys", [])
        self._means        = self._state.get("means",        {})
        self._stds         = self._state.get("stds",         {})


# Keep old name as alias for backward compatibility
MLWorkoutRecommender = KNNWorkoutRecommender
