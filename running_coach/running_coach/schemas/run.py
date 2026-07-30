"""
Normalized run record — works with data from any source.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from .enums import ActivityType


@dataclass
class NormalizedRun:
    # Required
    date:             datetime
    activity_type:    ActivityType
    distance_km:      float
    duration_minutes: float

    # Standard fields
    avg_pace_min_per_km: Optional[float] = None
    avg_hr:              Optional[float] = None
    max_hr:              Optional[float] = None
    elevation_gain_m:    Optional[float] = None
    cadence:             Optional[int]   = None
    rpe:                 Optional[int]   = None
    notes:               Optional[str]   = None
    source:              Optional[str]   = None
    external_id:         Optional[str]   = None

    # Extra Garmin fields (parsed from CSV, used in analysis)
    _stride_m:      Optional[float] = field(default=None, repr=False)
    _gct_ms:        Optional[float] = field(default=None, repr=False)
    _battery_drain: Optional[float] = field(default=None, repr=False)
    _tss:           Optional[float] = field(default=None, repr=False)

    def __post_init__(self):
        if self.distance_km < 0:
            raise ValueError("distance_km must be non-negative")
        if self.duration_minutes <= 0:
            raise ValueError("duration_minutes must be positive")
        if self.rpe is not None and not (1 <= self.rpe <= 10):
            raise ValueError("rpe must be between 1 and 10")

    @property
    def pace_str(self) -> str:
        if self.avg_pace_min_per_km is None:
            return "N/A"
        mins = int(self.avg_pace_min_per_km)
        secs = int((self.avg_pace_min_per_km - mins) * 60)
        return f"{mins}:{secs:02d}/km"

    @property
    def speed_kmh(self) -> Optional[float]:
        if self.avg_pace_min_per_km and self.avg_pace_min_per_km > 0:
            return 60.0 / self.avg_pace_min_per_km
        return None
