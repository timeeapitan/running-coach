"""
Feature extraction — converts run history + feedback into a flat dict
suitable for any ML model. All values are floats.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from ..schemas import NormalizedRun, ManualFeedback, RunnerProfile


def extract_features(
    runs: List[NormalizedRun],
    feedback: Dict[str, ManualFeedback],
    profile: RunnerProfile,
) -> Dict[str, float]:
    now = datetime.now()

    def vol(days: int) -> float:
        cutoff = now - timedelta(days=days)
        return sum(r.distance_km for r in runs if r.date >= cutoff)

    def run_count(days: int) -> int:
        cutoff = now - timedelta(days=days)
        return sum(1 for r in runs if r.date >= cutoff)

    def avg_hr(days: int) -> float:
        cutoff = now - timedelta(days=days)
        hrs = [r.avg_hr for r in runs if r.date >= cutoff and r.avg_hr]
        return sum(hrs) / len(hrs) if hrs else 0.0

    def avg_pace(days: int) -> float:
        cutoff = now - timedelta(days=days)
        paces = [r.avg_pace_min_per_km for r in runs
                 if r.date >= cutoff and r.avg_pace_min_per_km]
        return sum(paces) / len(paces) if paces else 0.0

    recent_fb  = [fb for fb in feedback.values() if fb.date >= now - timedelta(days=7)]
    rpe_vals   = [fb.rpe   for fb in recent_fb if fb.rpe   is not None]
    sleep_vals = [fb.sleep_quality for fb in recent_fb if fb.sleep_quality is not None]
    mood_vals  = [fb.mood  for fb in recent_fb if fb.mood  is not None]
    hrv_vals   = [fb.hrv_ms for fb in recent_fb if fb.hrv_ms is not None]

    # HRV: ratio of today's reading vs 7-day baseline
    all_hrv = [fb.hrv_ms for fb in feedback.values()
                if fb.hrv_ms is not None and (now - fb.date).days <= 30]
    baseline_hrv = [fb.hrv_ms for fb in feedback.values()
                    if fb.hrv_ms is not None and 1 <= (now - fb.date).days <= 8]
    if baseline_hrv and hrv_vals:
        hrv_ratio = hrv_vals[-1] / (sum(baseline_hrv) / len(baseline_hrv))
    else:
        hrv_ratio = 1.0   # neutral — no data

    vol_7  = vol(7)
    vol_28 = vol(28)

    return {
        # Volume features
        "vol_7d":               vol_7,
        "vol_14d":              vol(14),
        "vol_28d":              vol_28,
        "vol_ratio_7_28":       (vol_7 / vol_28) if vol_28 else 0.0,
        # HR features
        "avg_hr_7d":            avg_hr(7),
        "avg_hr_14d":           avg_hr(14),
        # Pace features
        "avg_pace_7d":          avg_pace(7),
        "avg_pace_14d":         avg_pace(14),
        # Frequency
        "total_runs_7d":        float(run_count(7)),
        "total_runs_28d":       float(run_count(28)),
        # Subjective feedback
        "avg_rpe_7d":           (sum(rpe_vals)   / len(rpe_vals))   if rpe_vals   else 0.0,
        "avg_sleep_quality_7d": (sum(sleep_vals) / len(sleep_vals)) if sleep_vals else 3.0,
        "avg_mood_7d":          (sum(mood_vals)  / len(mood_vals))  if mood_vals  else 3.0,
        # HRV — the most important recovery signal
        "hrv_ratio":            hrv_ratio,        # >1 = above baseline (good), <1 = below (tired)
        "hrv_available":        1.0 if hrv_vals else 0.0,
        # Pain flag
        "pain_flag_recent":     float(any(fb.pain_flag for fb in recent_fb)),
        # Profile
        "max_hr":               float(profile.get_effective_max_hr()),
        "runs_per_week_target": float(profile.runs_per_week),
    }
