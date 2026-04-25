"""
Shared pytest fixtures available to all test modules.
"""

import pytest
from datetime import datetime, timedelta

from running_coach.schemas import (
    NormalizedRun,
    ManualFeedback,
    RunnerProfile,
    ActivityType,
    FitnessLevel,
)


@pytest.fixture
def base_profile():
    return RunnerProfile(
        max_hr=185,
        resting_hr=55,
        age=30,
        runs_per_week=3,
        fitness_level=FitnessLevel.INTERMEDIATE,
    )


@pytest.fixture
def sample_runs():
    """8 runs spread over 4 weeks at a consistent ~5 km / run."""
    base_date = datetime.now() - timedelta(weeks=4)
    return [
        NormalizedRun(
            date=base_date + timedelta(days=i * 3),
            activity_type=ActivityType.OUTDOOR_RUN,
            distance_km=5.0,
            duration_minutes=32.5,
            avg_hr=145,
            avg_pace_min_per_km=6.5,
        )
        for i in range(8)
    ]


@pytest.fixture
def sample_feedback(sample_runs):
    """Mild RPE + decent sleep for the two most recent runs."""
    fb = {}
    for run in sample_runs[-2:]:
        key = run.date.date().isoformat()
        fb[key] = ManualFeedback(
            date=run.date,
            rpe=6,
            sleep_hours=7.5,
            sleep_quality=4,
            mood=4,
        )
    return fb
