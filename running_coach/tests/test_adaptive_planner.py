from datetime import datetime, timedelta
from running_coach.coaching.adaptive_planner import AdaptiveTrainingPlanner
from running_coach.schemas import NormalizedRun
from running_coach.schemas.enums import ActivityType


def run(days_ago, km):
    return NormalizedRun(date=datetime.now()-timedelta(days=days_ago), activity_type=ActivityType.OUTDOOR_RUN,
                         distance_km=km, duration_minutes=km*7.0, avg_pace_min_per_km=7.0)


def test_no_fixed_10k_ceiling():
    p=AdaptiveTrainingPlanner()
    s=p.state([run(2, 12.0), run(5, 10.0), run(9, 9.0)])
    assert s.sustainable_long_km > 12.0


def test_two_week_break_enters_returning_mode():
    p=AdaptiveTrainingPlanner()
    s=p.state([run(15, 6.0), run(20, 5.0)])
    assert s.continuity == 'returning'
    assert s.volume_multiplier < 1.0
    assert s.sustainable_long_km < 6.0


def test_progression_is_runner_specific():
    p=AdaptiveTrainingPlanner()
    a=p.state([run(2,4), run(5,3.5), run(9,3)])
    b=p.state([run(2,9), run(5,8), run(9,7)])
    assert b.sustainable_long_km > a.sustainable_long_km
