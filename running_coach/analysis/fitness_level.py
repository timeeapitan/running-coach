"""
Auto fitness level detector.

Fixed: now weighs overall history alongside recent activity.
A runner with 100+ runs who took a break is not a beginner —
they are a returning intermediate with base fitness still intact.
"""

from datetime import datetime, timedelta
from typing import Dict, List
from ..schemas import NormalizedRun, RunnerProfile
from ..schemas.enums import FitnessLevel


def detect_fitness_level(runs: List[NormalizedRun], profile: RunnerProfile) -> Dict:
    if not runs or len(runs) < 3:
        return {
            "level": FitnessLevel.BEGINNER,
            "changed": profile.fitness_level != FitnessLevel.BEGINNER,
            "reason": "Not enough runs yet to assess fitness level.",
            "evidence": [],
        }

    now        = datetime.now()
    total_runs = len(runs)

    # ── Recent activity (last 8 weeks) ────────────────────────────────
    weekly_vols = []
    for w in range(8):
        start = now - timedelta(weeks=w+1)
        end   = now - timedelta(weeks=w)
        vol   = sum(r.distance_km for r in runs if start <= r.date < end)
        weekly_vols.append(vol)
    active_weeks_recent  = sum(1 for v in weekly_vols if v > 0)
    avg_weekly_km_recent = (sum(weekly_vols) / active_weeks_recent
                            if active_weeks_recent else 0)

    # ── All-time history (gives context for returning runners) ────────
    all_weeks = max(1, (now - min(r.date for r in runs)).days // 7)
    total_km  = sum(r.distance_km for r in runs)
    avg_weekly_km_alltime = total_km / all_weeks

    # Best 8-week stretch (highest avg volume over any 8-week window)
    best_8wk = 0.0
    for start_w in range(max(0, all_weeks - 8)):
        start = now - timedelta(weeks=start_w + 8)
        end   = now - timedelta(weeks=start_w)
        vol   = sum(r.distance_km for r in runs if start <= r.date < end)
        best_8wk = max(best_8wk, vol / 8)

    # ── Consistency in last 8 weeks ───────────────────────────────────
    consistent_weeks = sum(
        1 for w in range(8)
        if sum(1 for r in runs
               if (now - timedelta(weeks=w+1)) <= r.date < (now - timedelta(weeks=w))) >= 2
    )

    # ── Pace trend ────────────────────────────────────────────────────
    pace_runs = sorted(
        [r for r in runs if r.avg_pace_min_per_km and r.distance_km >= 3],
        key=lambda r: r.date
    )
    pace_improving = False
    if len(pace_runs) >= 10:
        early  = sum(r.avg_pace_min_per_km for r in pace_runs[:5]) / 5
        recent = sum(r.avg_pace_min_per_km for r in pace_runs[-5:]) / 5
        pace_improving = recent < early - 0.15

    # ── Returning runner detection ────────────────────────────────────
    # Has good history but low recent volume → returning, not beginner
    days_since_last = (now - sorted(runs, key=lambda r: r.date)[-1].date).days
    is_returning = (
        total_runs >= 20
        and best_8wk >= 15.0
        and avg_weekly_km_recent < 10.0
        and days_since_last <= 60
    )

    evidence = [
        f"{total_runs} total runs",
        f"{avg_weekly_km_recent:.0f} km/week recently",
        f"{best_8wk:.0f} km/week at peak",
        f"{consistent_weeks}/8 consistent weeks",
    ]

    # ── Decision ─────────────────────────────────────────────────────
    if is_returning:
        level  = FitnessLevel.INTERMEDIATE
        reason = (
            f"Returning runner — {total_runs} runs in your history, "
            f"peak of {best_8wk:.0f} km/week. "
            f"Easing back in after a break."
        )

    elif (total_runs >= 50
          and (avg_weekly_km_recent >= 35 or best_8wk >= 40)
          and consistent_weeks >= 5
          and pace_improving):
        level  = FitnessLevel.ADVANCED
        reason = (
            f"Strong history — {total_runs} runs, "
            f"{avg_weekly_km_recent:.0f} km/week, consistent and improving."
        )

    elif (total_runs >= 15
          and (avg_weekly_km_recent >= 12 or best_8wk >= 15)
          and (consistent_weeks >= 3 or total_runs >= 30)):
        level  = FitnessLevel.INTERMEDIATE
        reason = (
            f"Solid base — {total_runs} runs, "
            f"{avg_weekly_km_recent:.0f} km/week recently."
        )

    elif total_runs < 10 or (avg_weekly_km_recent < 5 and best_8wk < 8):
        level  = FitnessLevel.BEGINNER
        reason = (
            f"Building your base — {total_runs} runs logged, "
            f"{avg_weekly_km_recent:.0f} km/week average."
        )

    else:
        level  = FitnessLevel.BEGINNER
        reason = (
            f"Still building consistency — {consistent_weeks}/8 weeks active, "
            f"{avg_weekly_km_recent:.0f} km/week recently."
        )

    changed = level != profile.fitness_level
    return {
        "level":      level,
        "changed":    changed,
        "reason":     reason,
        "evidence":   evidence,
        "prev_level": profile.fitness_level.value if changed else None,
    }
