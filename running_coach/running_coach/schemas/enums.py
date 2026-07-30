"""
Enumerations for the running coach application.
"""

from enum import Enum


class ActivityType(str, Enum):
    OUTDOOR_RUN = "outdoor_run"
    TREADMILL_RUN = "treadmill_run"
    TRAIL_RUN = "trail_run"
    RACE = "race"
    LONG_RUN = "long_run"
    INTERVAL = "interval"
    TEMPO = "tempo"
    EASY = "easy"
    RECOVERY = "recovery"


class FitnessLevel(str, Enum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"
    ELITE = "elite"


class WorkoutType(str, Enum):
    EASY = "easy"
    MODERATE = "moderate"
    TEMPO = "tempo"
    INTERVAL = "interval"
    LONG_RUN = "long_run"
    RECOVERY = "recovery"
    REST = "rest"


class Intensity(str, Enum):
    VERY_EASY = "very_easy"
    EASY = "easy"
    MODERATE = "moderate"
    HARD = "hard"
    VERY_HARD = "very_hard"
    MAX = "max"
