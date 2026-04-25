"""
Quick-start example — demonstrates the full running coach including
the personalised next-run predictor.

Run:
    python example.py
"""

from datetime import datetime, timedelta
import random

from running_coach import RunningCoach, RunnerProfile, NormalizedRun, ActivityType
from running_coach.schemas import ManualFeedback, FitnessLevel


def build_history(weeks: int = 6, runs_per_week: int = 4) -> list:
    """Simulate a realistic 6-week training block."""
    random.seed(42)
    runs = []
    base_date = datetime.now() - timedelta(weeks=weeks)

    # Distances follow a typical week pattern: easy, moderate, easy, long
    week_pattern = [5.0, 7.0, 5.5, 12.0]

    for week in range(weeks):
        for i in range(min(runs_per_week, len(week_pattern))):
            # Small random variation ±10%
            dist = week_pattern[i] * (1 + random.uniform(-0.1, 0.1))
            # Gradual progression: +3% per week
            dist *= (1 + 0.03 * week)
            dist = round(dist, 1)

            date = base_date + timedelta(weeks=week, days=i * 2)
            pace = 6.5 - (week * 0.05) + random.uniform(-0.1, 0.1)
            hr   = 145 + i * 3 + random.randint(-3, 3)

            runs.append(NormalizedRun(
                date=date,
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=dist,
                duration_minutes=round(dist * pace, 1),
                avg_pace_min_per_km=round(pace, 2),
                avg_hr=hr,
            ))

    return sorted(runs, key=lambda r: r.date)


def main():
    # 1. Configure the runner
    profile = RunnerProfile(
        max_hr=182,
        resting_hr=52,
        age=32,
        runs_per_week=4,
        fitness_level=FitnessLevel.INTERMEDIATE,
        threshold_pace_min_per_km=5.2,
    )

    # 2. Build history
    runs = build_history(weeks=6, runs_per_week=4)
    print(f"Training history: {len(runs)} runs over 6 weeks\n")

    # 3. Optional feedback for the most recent run
    feedback = {
        runs[-1].date.date().isoformat(): ManualFeedback(
            date=runs[-1].date,
            rpe=7,
            sleep_hours=7.5,
            sleep_quality=4,
            mood=4,
        )
    }

    coach = RunningCoach(profile)

    # 4. Get the full coaching advice (uses predictor automatically)
    print("=" * 50)
    print(coach.get_advice(runs, feedback))
    print()

    # 5. Access the raw prediction for programmatic use
    prediction = coach.predict_next_run(runs, feedback)
    print("=" * 50)
    print("Raw prediction object:")
    print(f"  Type     : {prediction.workout_type.value}")
    print(f"  Distance : {prediction.target_distance_km} km")
    print(f"  Duration : {prediction.target_duration_minutes} min")
    print(f"  HR zone  : {prediction.target_hr_zone}")
    print(f"  Intensity: {prediction.intensity.value}")

    # 6. Show analysis details separately
    analysis = coach.analyze(runs, feedback)
    print()
    print("=" * 50)
    print("Analysis breakdown:")
    print(f"  Fatigue factors   : {analysis.fatigue_factors}")
    print(f"  Consistency factors: {analysis.consistency_factors}")
    print(f"  Readiness factors  : {analysis.readiness_factors}")


if __name__ == "__main__":
    main()
