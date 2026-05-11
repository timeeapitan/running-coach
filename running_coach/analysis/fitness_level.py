"""
Auto fitness level detector.

Analyses run history to determine the runner's current fitness level
and updates the profile automatically when the level changes.

Criteria:
  Beginner:     < 10 runs OR < 10 km/week avg OR < 4 weeks consistent
  Intermediate: 10-50 runs AND 10-35 km/week AND some consistency
  Advanced:     50+ runs AND 35+ km/week AND high consistency
  Elite:        not auto-assigned (requires manual confirmation)

Returns a dict with:
  level         — FitnessLevel enum value
  changed       — True if different from current profile level
  reason        — plain-English explanation shown on dashboard
  evidence      — list of specific data points used
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from ..schemas import NormalizedRun, RunnerProfile
from ..schemas.enums import FitnessLevel


def detect_fitness_level(
    runs: List[NormalizedRun],
    profile: RunnerProfile,
) -> Dict:
    """
    Analyse run history and return the detected fitness level with reasoning.
    """
    if not runs or len(runs) < 3:
        return {
            "level":   FitnessLevel.BEGINNER,
            "changed": profile.fitness_level != FitnessLevel.BEGINNER,
            "reason":  "Not enough runs yet to assess fitness level.",
            "evidence": [],
        }

    now        = datetime.now()
    total_runs = len(runs)

    # Weekly volume (last 8 weeks)
    weekly_vols = []
    for w in range(8):
        start = now - timedelta(weeks=w+1)
        end   = now - timedelta(weeks=w)
        vol   = sum(r.distance_km for r in runs if start <= r.date < end)
        weekly_vols.append(vol)
    active_weeks  = sum(1 for v in weekly_vols if v > 0)
    avg_weekly_km = sum(weekly_vols) / max(1, active_weeks) if active_weeks else 0

    # Consistency — weeks with at least 2 runs in last 8
    consistent_weeks = sum(
        1 for w in range(8)
        if sum(1 for r in runs
               if (now - timedelta(weeks=w+1)) <= r.date < (now - timedelta(weeks=w))) >= 2
    )

    # Pace trend — is pace improving over last 30 runs?
    pace_runs = sorted(
        [r for r in runs if r.avg_pace_min_per_km and r.distance_km >= 3],
        key=lambda r: r.date
    )
    pace_improving = False
    if len(pace_runs) >= 10:
        early  = sum(r.avg_pace_min_per_km for r in pace_runs[:5]) / 5
        recent = sum(r.avg_pace_min_per_km for r in pace_runs[-5:]) / 5
        pace_improving = recent < early - 0.15  # 9+ sec/km improvement

    # Long run presence
    has_long_runs = any(r.distance_km >= 10 for r in runs)

    evidence = [
        f"{total_runs} total runs",
        f"{avg_weekly_km:.1f} km/week average",
        f"{consistent_weeks}/8 consistent weeks",
    ]
    if pace_improving:
        evidence.append("pace improving over time")
    if has_long_runs:
        evidence.append("completed runs over 10 km")

    # --- Decision logic ---
    if total_runs < 10 or avg_weekly_km < 8 or active_weeks < 2:
        level  = FitnessLevel.BEGINNER
        reason = (
            f"Building your base — {total_runs} runs logged, "
            f"{avg_weekly_km:.0f} km/week average."
        )

    elif (total_runs >= 50
          and avg_weekly_km >= 35
          and consistent_weeks >= 5
          and pace_improving):
        level  = FitnessLevel.ADVANCED
        reason = (
            f"Strong training history — {total_runs} runs, "
            f"{avg_weekly_km:.0f} km/week, consistent and improving."
        )

    elif (total_runs >= 15
          and avg_weekly_km >= 12
          and consistent_weeks >= 3):
        level  = FitnessLevel.INTERMEDIATE
        reason = (
            f"Solid base — {total_runs} runs, "
            f"{avg_weekly_km:.0f} km/week over {consistent_weeks} consistent weeks."
        )

    else:
        level  = FitnessLevel.BEGINNER
        reason = (
            f"Still building consistency — {consistent_weeks}/8 weeks active, "
            f"{avg_weekly_km:.0f} km/week average."
        )

    changed = level != profile.fitness_level

    return {
        "level":    level,
        "changed":  changed,
        "reason":   reason,
        "evidence": evidence,
        "prev_level": profile.fitness_level.value if changed else None,
    }
