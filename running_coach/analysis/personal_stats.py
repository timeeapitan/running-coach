"""
Personal statistics calculator.

Computes the runner-facing metrics shown on the dashboard:
  - Form score (pace/HR efficiency trend)
  - VO2max estimate (Uth-Sørensen-Overgaard formula)
  - Fitness age (based on VO2max vs population norms)
  - Personal bests (5K, 10K estimated via Riegel)
  - Weekly run streak
  - Pace trend (last 8 weeks)
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from ..schemas import NormalizedRun, RunnerProfile


def _fmt_pace(p: Optional[float]) -> Optional[str]:
    if not p:
        return None
    m = int(p)
    s = int((p - m) * 60)
    return f"{m}:{s:02d}/km"


def _fmt_time(minutes: float) -> str:
    h = int(minutes // 60)
    m = int(minutes % 60)
    s = int((minutes - int(minutes)) * 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


# ── Form score ────────────────────────────────────────────────────────────────

def form_score(runs: List[NormalizedRun], profile: RunnerProfile) -> Optional[Dict]:
    """
    Pace/HR efficiency trend. Higher = running faster at the same heart rate.
    Score 0-100. >60 = improving, 40-60 = stable, <40 = declining.
    """
    valid = [r for r in runs
             if r.avg_pace_min_per_km and r.avg_hr and r.distance_km >= 2]
    if len(valid) < 5:
        return None

    sorted_runs = sorted(valid, key=lambda r: r.date)
    mhr = profile.get_effective_max_hr()

    # Efficiency = speed (km/h) / HR fraction
    def efficiency(r):
        speed = 60.0 / r.avg_pace_min_per_km
        hr_fraction = r.avg_hr / mhr
        return speed / hr_fraction if hr_fraction > 0 else 0

    effs = [efficiency(r) for r in sorted_runs]

    # Compare last 5 vs previous 5
    if len(effs) >= 10:
        recent = sum(effs[-5:]) / 5
        older  = sum(effs[-10:-5]) / 5
        delta  = (recent - older) / older if older > 0 else 0
    else:
        recent = sum(effs[-3:]) / 3
        older  = sum(effs[:3]) / 3
        delta  = (recent - older) / older if older > 0 else 0

    score = min(100, max(0, 50 + delta * 500))

    trend = "improving" if delta > 0.02 else "declining" if delta < -0.02 else "stable"
    return {
        "score": round(score),
        "trend": trend,
        "delta_pct": round(delta * 100, 1),
        "current_efficiency": round(recent, 2),
    }


# ── VO2max estimate ───────────────────────────────────────────────────────────

def estimate_vo2max(runs: List[NormalizedRun], profile: RunnerProfile) -> Optional[float]:
    """
    Uth-Sørensen-Overgaard-Pedersen formula:
    VO2max ≈ 15 × (HRmax / HRrest)

    Falls back to pace-based estimate if resting HR unknown.
    """
    mhr = profile.get_effective_max_hr()

    if profile.resting_hr and profile.resting_hr > 0:
        return round(15.0 * mhr / profile.resting_hr, 1)

    # Pace-based: use best recent 5K equivalent pace
    valid = [r for r in runs
             if r.avg_pace_min_per_km and r.distance_km >= 3]
    if not valid:
        return None
    best_pace = min(r.avg_pace_min_per_km for r in valid[-20:])
    speed_ms = (1000 / best_pace) / 60
    return round(-4.60 + 0.182258 * speed_ms * 60 + 0.000104 * (speed_ms * 60) ** 2, 1)


def fitness_age(vo2max: Optional[float], actual_age: Optional[int]) -> Optional[Dict]:
    """
    Estimate fitness age based on VO2max vs population norms for women.
    Returns fitness age and comparison to actual age.
    """
    if not vo2max:
        return None

    # VO2max norms for women (approximate midpoints per age decade)
    # Source: ACSM guidelines
    norms = [
        (20, 44), (25, 42), (30, 40), (35, 38),
        (40, 36), (45, 34), (50, 32), (55, 30), (60, 28),
    ]

    # Find fitness age where VO2max norm matches actual VO2max
    fitness_age_est = norms[-1][0]
    for age, norm_vo2 in norms:
        if vo2max >= norm_vo2:
            fitness_age_est = age
            break

    result = {"fitness_age": fitness_age_est, "vo2max": vo2max}
    if actual_age:
        diff = actual_age - fitness_age_est
        result["vs_actual"] = diff
        result["label"] = (
            f"{diff} years younger than your age" if diff > 2 else
            f"{-diff} years older than your age" if diff < -2 else
            "matches your actual age"
        )
    return result


# ── Personal bests ────────────────────────────────────────────────────────────

def personal_bests(runs: List[NormalizedRun]) -> Dict:
    """
    Estimate 5K and 10K times using Riegel formula from best recent run.
    Also tracks actual longest run and fastest pace.
    """
    valid = [r for r in runs if r.avg_pace_min_per_km and r.distance_km >= 1]
    if not valid:
        return {}

    bests = {}

    # Actual fastest pace (any distance)
    fastest = min(valid, key=lambda r: r.avg_pace_min_per_km)
    bests["fastest_pace"] = _fmt_pace(fastest.avg_pace_min_per_km)
    bests["fastest_pace_dist"] = round(fastest.distance_km, 1)
    bests["fastest_pace_date"] = fastest.date.strftime("%d %b %Y")

    # Actual longest run
    longest = max(valid, key=lambda r: r.distance_km)
    bests["longest_km"] = round(longest.distance_km, 1)
    bests["longest_date"] = longest.date.strftime("%d %b %Y")

    # Riegel formula: T2 = T1 × (D2/D1)^1.06
    # Use best pace run with enough distance for accuracy
    ref_runs = [r for r in valid if r.distance_km >= 3]
    if ref_runs:
        ref = min(ref_runs, key=lambda r: r.avg_pace_min_per_km)
        ref_time = ref.avg_pace_min_per_km * ref.distance_km
        ref_dist = ref.distance_km

        for dist, label in [(5.0, "5k"), (10.0, "10k"), (21.1, "half")]:
            if ref_dist >= dist * 0.5:  # only extrapolate reasonably
                pred = ref_time * (dist / ref_dist) ** 1.06
                bests[f"est_{label}"] = _fmt_time(pred)

    return bests


# ── Weekly run streak ─────────────────────────────────────────────────────────

def weekly_streak(runs: List[NormalizedRun]) -> int:
    """Consecutive weeks with at least one run. More realistic than daily streak."""
    if not runs:
        return 0
    now = datetime.now()
    streak = 0
    for w in range(52):
        week_start = now - timedelta(weeks=w+1)
        week_end   = now - timedelta(weeks=w)
        has_run = any(week_start <= r.date < week_end for r in runs)
        if has_run:
            streak += 1
        elif streak > 0:
            break
    return streak


# ── Pace trend ────────────────────────────────────────────────────────────────

def pace_trend(runs: List[NormalizedRun], weeks: int = 8) -> List[Dict]:
    """Weekly average pace over last N weeks for sparkline chart."""
    now = datetime.now()
    result = []
    for w in range(weeks - 1, -1, -1):
        start = now - timedelta(weeks=w+1)
        end   = now - timedelta(weeks=w)
        week_runs = [r for r in runs
                     if start <= r.date < end and r.avg_pace_min_per_km]
        if week_runs:
            avg = sum(r.avg_pace_min_per_km for r in week_runs) / len(week_runs)
            result.append({
                "label": start.strftime("%d %b"),
                "pace":  round(avg, 2),
                "runs":  len(week_runs),
            })
        else:
            result.append({
                "label": start.strftime("%d %b"),
                "pace":  None,
                "runs":  0,
            })
    return result


# ── Full stats bundle ─────────────────────────────────────────────────────────

def compute_personal_stats(
    runs: List[NormalizedRun],
    profile: RunnerProfile,
) -> Dict:
    """Return all personal stats in one call for the dashboard."""
    vo2 = estimate_vo2max(runs, profile)
    return {
        "form":          form_score(runs, profile),
        "vo2max":        vo2,
        "fitness_age":   fitness_age(vo2, profile.age),
        "personal_bests": personal_bests(runs),
        "weekly_streak": weekly_streak(runs),
        "pace_trend":    pace_trend(runs),
        "total_runs":    len(runs),
        "total_km":      round(sum(r.distance_km for r in runs), 1),
    }
