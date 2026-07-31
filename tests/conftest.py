import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from datetime import datetime, timedelta
from running_coach.schemas import NormalizedRun, RunnerProfile
from running_coach.schemas.enums import ActivityType, FitnessLevel


@pytest.fixture
def profile():
    return RunnerProfile(
        name="Timeea", age=27, max_hr=183, resting_hr=54,
        runs_per_week=3, fitness_level=FitnessLevel.INTERMEDIATE,
        goal_weekly_km=20.0,
    )

@pytest.fixture
def profile_beginner():
    return RunnerProfile(
        name="Test", age=30, max_hr=185, resting_hr=70,
        runs_per_week=2, fitness_level=FitnessLevel.BEGINNER,
    )

def make_run(days_ago=0, km=5.5, hr=155, pace=8.0,
             activity=ActivityType.OUTDOOR_RUN, elev=None):
    return NormalizedRun(
        date=datetime.now() - timedelta(days=days_ago),
        activity_type=activity, distance_km=km,
        duration_minutes=round(km * pace),
        avg_pace_min_per_km=pace, avg_hr=hr,
        elevation_gain_m=elev, source="test",
    )

@pytest.fixture
def recent_runs():
    return [make_run(days_ago=i*3, km=5.0+i*0.2, hr=150+i, pace=8.0-i*0.05)
            for i in range(10)]

@pytest.fixture
def no_runs():
    return []
