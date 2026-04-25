"""
Gradient Boosted Regression Trees — pure Python, no external dependencies.

Each tree is a depth-limited decision stump (depth=3) fitted on pseudo-residuals.
This is the same core idea as XGBoost / LightGBM but self-contained.

Used by FatiguePredictor (better than linear for non-linear fatigue dynamics).
"""

import math
from typing import Dict, List, Optional, Tuple, Any

from .base import BaseMLModel


# ---------------------------------------------------------------------------
# Decision tree node (used internally)
# ---------------------------------------------------------------------------

class _Node:
    """A single node in a regression tree."""
    __slots__ = ("feature", "threshold", "left", "right", "value")

    def __init__(self):
        self.feature:   Optional[int]   = None
        self.threshold: Optional[float] = None
        self.left:      Optional["_Node"] = None
        self.right:     Optional["_Node"] = None
        self.value:     Optional[float] = None  # leaf value

    def is_leaf(self) -> bool:
        return self.value is not None

    def predict(self, x: List[float]) -> float:
        if self.is_leaf():
            return self.value
        if x[self.feature] <= self.threshold:
            return self.left.predict(x)
        return self.right.predict(x)

    def to_dict(self) -> Dict:
        if self.is_leaf():
            return {"value": self.value}
        return {
            "feature":   self.feature,
            "threshold": self.threshold,
            "left":      self.left.to_dict(),
            "right":     self.right.to_dict(),
        }

    @staticmethod
    def from_dict(d: Dict) -> "_Node":
        node = _Node()
        if "value" in d:
            node.value = d["value"]
        else:
            node.feature   = d["feature"]
            node.threshold = d["threshold"]
            node.left      = _Node.from_dict(d["left"])
            node.right     = _Node.from_dict(d["right"])
        return node


# ---------------------------------------------------------------------------
# Regression tree builder
# ---------------------------------------------------------------------------

def _mse(values: List[float]) -> float:
    if not values:
        return 0.0
    m = sum(values) / len(values)
    return sum((v - m) ** 2 for v in values) / len(values)


def _build_tree(
    X: List[List[float]],
    y: List[float],
    depth: int,
    max_depth: int,
    min_samples_leaf: int = 2,
) -> _Node:
    node = _Node()

    if depth >= max_depth or len(y) < min_samples_leaf * 2:
        node.value = sum(y) / len(y) if y else 0.0
        return node

    n_features = len(X[0]) if X else 0
    best_feat, best_thresh, best_gain = None, None, -1.0
    parent_mse = _mse(y)

    for feat in range(n_features):
        values = sorted(set(x[feat] for x in X))
        thresholds = [
            (values[i] + values[i + 1]) / 2.0
            for i in range(len(values) - 1)
        ]
        for thresh in thresholds:
            left_y  = [y[i] for i, x in enumerate(X) if x[feat] <= thresh]
            right_y = [y[i] for i, x in enumerate(X) if x[feat] >  thresh]
            if len(left_y) < min_samples_leaf or len(right_y) < min_samples_leaf:
                continue
            n = len(y)
            gain = parent_mse - (
                len(left_y)  / n * _mse(left_y) +
                len(right_y) / n * _mse(right_y)
            )
            if gain > best_gain:
                best_gain, best_feat, best_thresh = gain, feat, thresh

    if best_feat is None or best_gain <= 0:
        node.value = sum(y) / len(y)
        return node

    node.feature   = best_feat
    node.threshold = best_thresh

    left_mask  = [x[best_feat] <= best_thresh for x in X]
    left_X     = [X[i]  for i, m in enumerate(left_mask) if m]
    left_y     = [y[i]  for i, m in enumerate(left_mask) if m]
    right_X    = [X[i]  for i, m in enumerate(left_mask) if not m]
    right_y    = [y[i]  for i, m in enumerate(left_mask) if not m]

    node.left  = _build_tree(left_X,  left_y,  depth + 1, max_depth, min_samples_leaf)
    node.right = _build_tree(right_X, right_y, depth + 1, max_depth, min_samples_leaf)
    return node


# ---------------------------------------------------------------------------
# Gradient Boosting model
# ---------------------------------------------------------------------------

class GradientBoostingRegressor(BaseMLModel):
    """
    Gradient boosted regression trees.

    Algorithm:
      1. Start with prediction = mean(y)
      2. For each round:
         a. Compute pseudo-residuals = y - current_prediction
         b. Fit a shallow decision tree on residuals
         c. Add learning_rate × tree_prediction to current prediction
      3. Final prediction = initial_mean + sum(lr × tree_i)
    """

    def __init__(
        self,
        model_path:    Optional[str] = None,
        n_estimators:  int   = 100,
        learning_rate: float = 0.1,
        max_depth:     int   = 3,
        min_samples_leaf: int = 2,
    ):
        super().__init__(model_path)
        self.n_estimators       = n_estimators
        self.learning_rate      = learning_rate
        self.max_depth          = max_depth
        self.min_samples_leaf   = min_samples_leaf

        self._trees:        List[_Node]         = []
        self._initial_pred: float               = 0.0
        self._feature_keys: List[str]           = []
        self._means:        Dict[str, float]    = {}
        self._stds:         Dict[str, float]    = {}

    def train(
        self,
        features: List[Dict[str, float]],
        labels:   List[float],
    ) -> Dict[str, float]:
        if len(features) < 4:
            raise ValueError("Need at least 4 samples for GBM training")

        X_raw, keys, means, stds = self._normalize_features(features)
        y = list(labels)

        self._feature_keys = keys
        self._means        = means
        self._stds         = stds

        # Initial prediction: mean of labels
        self._initial_pred = self._mean(y)
        F = [self._initial_pred] * len(y)

        self._trees = []
        for _ in range(self.n_estimators):
            residuals = [y[i] - F[i] for i in range(len(y))]
            tree = _build_tree(
                X_raw, residuals,
                depth=0,
                max_depth=self.max_depth,
                min_samples_leaf=self.min_samples_leaf,
            )
            update = [tree.predict(x) for x in X_raw]
            F = [F[i] + self.learning_rate * update[i] for i in range(len(y))]
            self._trees.append(tree)

        self.is_trained = True

        # Persist
        self._state = {
            "initial_pred": self._initial_pred,
            "feature_keys": keys,
            "means":        means,
            "stds":         stds,
            "trees":        [t.to_dict() for t in self._trees],
            "learning_rate":self.learning_rate,
        }

        return {
            "mae":       round(self._mae(F, y), 3),
            "r2":        round(self._r2(F, y),  3),
            "n_trees":   len(self._trees),
            "n_samples": len(y),
        }

    def predict(self, features: Dict[str, float]) -> float:
        if not self.is_trained:
            raise RuntimeError("Model not trained. Call train() first.")
        x = self._encode(features)
        pred = self._initial_pred
        for tree in self._trees:
            pred += self.learning_rate * tree.predict(x)
        return pred

    def predict_batch(self, features_list: List[Dict[str, float]]) -> List[float]:
        return [self.predict(f) for f in features_list]

    def _encode(self, features: Dict[str, float]) -> List[float]:
        return [
            (features.get(k, 0.0) - self._means.get(k, 0.0))
            / (self._stds.get(k, 1.0) or 1.0)
            for k in self._feature_keys
        ]

    def _restore_from_state(self) -> None:
        self._initial_pred  = self._state.get("initial_pred", 0.0)
        self._feature_keys  = self._state.get("feature_keys", [])
        self._means         = self._state.get("means",        {})
        self._stds          = self._state.get("stds",         {})
        self.learning_rate  = self._state.get("learning_rate", self.learning_rate)
        self._trees         = [
            _Node.from_dict(d) for d in self._state.get("trees", [])
        ]
