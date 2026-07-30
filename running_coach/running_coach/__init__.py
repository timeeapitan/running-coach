"""
running_coach — AI-powered running analysis and coaching.
"""

from .coaching.coach import RunningCoach
from .ml.models.next_run_predictor import NextRunPredictor
from .schemas import (
    NormalizedRun,
    ManualFeedback,
    RunnerProfile,
    AnalysisResult,
    WorkoutRecommendation,
    ActivityType,
    FitnessLevel,
    WorkoutType,
)

__all__ = [
    "RunningCoach",
    "NextRunPredictor",
    "NormalizedRun",
    "ManualFeedback",
    "RunnerProfile",
    "AnalysisResult",
    "WorkoutRecommendation",
    "ActivityType",
    "FitnessLevel",
    "WorkoutType",
]

__version__ = "0.2.0"
