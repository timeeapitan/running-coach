"""
Schema for recommended workout.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Optional, List

from .enums import WorkoutType, Intensity


@dataclass
class WorkoutRecommendation:
    """A single recommended workout."""

    workout_type: WorkoutType
    intensity: Intensity
    target_distance_km: Optional[float] = None
    target_duration_minutes: Optional[float] = None
    description: str = ""
    rationale: str = ""
    target_hr_zone: Optional[str] = None  # e.g. "easy", "threshold"
    warnings: List[str] = field(default_factory=list)

    @property
    def is_rest(self) -> bool:
        return self.workout_type == WorkoutType.REST
