from .base import BaseMLModel
from .linear_regression import RidgeRegression
from .gradient_boosting import GradientBoostingRegressor
from .fatigue_predictor import FatiguePredictor
from .pace_predictor import PacePredictor
from .workout_recommender import KNNWorkoutRecommender, MLWorkoutRecommender
from .next_run_predictor import NextRunPredictor

__all__ = [
    "BaseMLModel",
    "RidgeRegression",
    "GradientBoostingRegressor",
    "FatiguePredictor",
    "PacePredictor",
    "KNNWorkoutRecommender",
    "MLWorkoutRecommender",
    "NextRunPredictor",
]
