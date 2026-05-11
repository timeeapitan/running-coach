"""
Schema for recommended workout.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict

from .enums import WorkoutType, Intensity


@dataclass
class WorkoutRecommendation:
    """A single recommended workout."""

    workout_type:            WorkoutType
    intensity:               Intensity
    target_distance_km:      Optional[float] = None
    target_duration_minutes: Optional[float] = None
    description:             str             = ""
    rationale:               str             = ""
    target_hr_zone:          Optional[str]   = None
    warnings:                List[str]       = field(default_factory=list)
    # Structured breakdown: [{label, detail}, ...]
    steps:                   List[Dict]      = field(default_factory=list)
    # Terrain: "road", "trail", "hilly trail"
    terrain:                 str             = "road"

    @property
    def is_rest(self) -> bool:
        return self.workout_type == WorkoutType.REST
