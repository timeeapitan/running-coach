"""Continuous, goal-optional progression for the personalised running coach.

The planner does not chase a hard-coded 5K/10K ceiling. It estimates a safe
training dose from the runner's own recent history. ML can personalise pace and
workout type; this layer supplies continuity/progression constraints so ML does
not merely reproduce the past or prescribe quality work after a long break.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

from ..schemas import NormalizedRun


@dataclass(frozen=True)
class ProgressionState:
    days_since_last_run: Optional[int]
    km_7d: float
    km_prev_7d: float
    km_28d: float
    recent_longest_km: float
    sustainable_long_km: float
    continuity: str
    volume_multiplier: float


class AdaptiveTrainingPlanner:
    """Derive a continuously moving training target from cached run history."""

    def state(self, runs: List[NormalizedRun], now: Optional[datetime] = None) -> ProgressionState:
        now = now or datetime.now()
        if not runs:
            return ProgressionState(None, 0, 0, 0, 0, 3.0, "new", 0.8)

        def naive(dt):
            return dt.replace(tzinfo=None) if getattr(dt, "tzinfo", None) else dt

        dated = sorted(runs, key=lambda r: naive(r.date))
        last = naive(dated[-1].date)
        days = max(0, (now.replace(tzinfo=None) - last).days)

        def km_between(lo, hi=0):
            # lo/hi are days ago: [hi, lo)
            older = now - timedelta(days=lo)
            newer = now - timedelta(days=hi)
            return sum(r.distance_km for r in dated if older <= naive(r.date) < newer)

        km7 = km_between(7)
        prev7 = km_between(14, 7)
        km28 = km_between(28)
        recent = [r.distance_km for r in dated if naive(r.date) >= now - timedelta(days=42)]
        longest = max(recent) if recent else max(r.distance_km for r in dated[-10:])

        # A break changes what is appropriate even when wearable readiness is high.
        if days >= 21:
            continuity, mult = "returning", 0.65
        elif days >= 14:
            continuity, mult = "returning", 0.75
        elif days >= 7:
            continuity, mult = "interrupted", 0.85
        else:
            continuity, mult = "consistent", 1.0

        # Continuous long-run target: no fixed end distance. Progress gently when
        # training is continuous; rebuild after interruptions. The 42-day longest
        # anchors the runner to demonstrated capacity rather than an arbitrary goal.
        if continuity == "consistent":
            target = longest * 1.06
        elif continuity == "interrupted":
            target = longest * 0.90
        else:
            target = longest * mult
        target = max(3.0, min(35.0, round(target * 2) / 2))

        return ProgressionState(days, round(km7, 2), round(prev7, 2), round(km28, 2),
                                round(longest, 2), target, continuity, mult)

    def long_run_target(self, runs: List[NormalizedRun], baseline_km: float) -> float:
        s = self.state(runs)
        # For sparse history, baseline still contributes; never jump straight to 8 km.
        if s.recent_longest_km <= 0:
            return max(3.0, baseline_km)
        return s.sustainable_long_km
