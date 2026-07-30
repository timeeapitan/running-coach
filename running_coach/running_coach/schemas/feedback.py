"""
Schema for manual user feedback entries — logged after each run or each morning.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class ManualFeedback:
    """User-submitted feedback for a training day."""

    date: datetime

    # Post-run subjective scores
    rpe: Optional[int] = None           # Rate of Perceived Exertion 1-10
    mood: Optional[int] = None          # 1-5 (1=terrible, 5=great)

    # Sleep — log these each morning
    sleep_hours: Optional[float] = None
    sleep_quality: Optional[int] = None # 1-5

    # HRV — morning measurement (ms), from Garmin/watch morning report
    hrv_ms: Optional[float] = None      # e.g. 52.0 ms

    # Pain / injury flags
    pain_flag: bool = False
    pain_location: Optional[str] = None

    notes: Optional[str] = None

    def __post_init__(self):
        if self.rpe is not None and not (1 <= self.rpe <= 10):
            raise ValueError("rpe must be between 1 and 10")
        if self.mood is not None and not (1 <= self.mood <= 5):
            raise ValueError("mood must be between 1 and 5")
        if self.sleep_quality is not None and not (1 <= self.sleep_quality <= 5):
            raise ValueError("sleep_quality must be between 1 and 5")
        if self.sleep_hours is not None and not (0 <= self.sleep_hours <= 24):
            raise ValueError("sleep_hours must be between 0 and 24")
        if self.hrv_ms is not None and self.hrv_ms < 0:
            raise ValueError("hrv_ms must be non-negative")
        if self.pain_flag and not self.pain_location:
            self.pain_location = "unspecified"
