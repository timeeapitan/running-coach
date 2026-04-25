"""
Readiness calculator — incorporates HRV when available.

HRV (heart rate variability) is the best single recovery signal.
When your HRV is above your personal baseline, your nervous system has
recovered well and you can handle more load.
When it drops significantly below baseline, back off.

We compare today's HRV to the runner's 7-day rolling average (baseline).
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from .base import BaseCalculator
from ..schemas import NormalizedRun, ManualFeedback


class ReadinessCalculator(BaseCalculator):

    def calculate(
        self,
        runs: List[NormalizedRun],
        feedback: Dict[str, ManualFeedback],
        fatigue_score: float = 50.0,
        consistency_score: float = 50.0,
    ) -> Tuple[float, Dict[str, float]]:

        energy    = max(0.0, min(100.0, 100.0 - fatigue_score))
        recovery  = self._recovery_quality(runs, feedback)
        hrv_score = self._hrv_score(feedback)
        momentum  = consistency_score

        # Weights — HRV gets its own slot when present, displacing some
        # of the subjective recovery weight
        has_hrv = hrv_score is not None
        if has_hrv:
            w_e, w_r, w_h, w_m = 0.35, 0.20, 0.25, 0.20
            raw = energy * w_e + recovery * w_r + hrv_score * w_h + momentum * w_m
        else:
            w_e, w_r, w_m = 0.40, 0.30, 0.30
            raw = energy * w_e + recovery * w_r + momentum * w_m

        score = min(100.0, max(0.0, raw))

        factors: Dict[str, float] = {
            "energy_available":     round(energy,    1),
            "recovery_quality":     round(recovery,  1),
            "consistency_momentum": round(momentum,  1),
        }
        if has_hrv:
            factors["hrv_score"] = round(hrv_score, 1)

        return round(score, 1), factors

    # ------------------------------------------------------------------

    def _hrv_score(self, feedback: Dict[str, ManualFeedback]) -> Optional[float]:
        """
        Compare today's (or most recent) HRV to 7-day rolling average.
        Returns 0-100 score, or None if no HRV data is available.

        Score interpretation:
          HRV at baseline (ratio=1.0) → 65 (normal training day)
          HRV 15%+ above baseline     → 90+ (very ready)
          HRV 15%+ below baseline     → 35- (back off)
        """
        now = datetime.now()

        # Collect all HRV readings from last 30 days
        window = [(key, fb) for key, fb in feedback.items()
                  if fb.hrv_ms is not None
                  and (now - fb.date).days <= 30]

        if not window:
            return None

        # Rolling 7-day baseline (excluding today)
        baseline_readings = [
            fb.hrv_ms for _, fb in window
            if 1 <= (now - fb.date).days <= 8
        ]
        if not baseline_readings:
            # Not enough history — use overall mean as baseline
            baseline_readings = [fb.hrv_ms for _, fb in window]

        baseline = sum(baseline_readings) / len(baseline_readings)

        # Most recent HRV reading
        latest_fb = sorted(window, key=lambda x: x[1].date)[-1][1]
        today_hrv = latest_fb.hrv_ms

        ratio = today_hrv / baseline if baseline > 0 else 1.0

        # Map ratio → score
        # 0.75 → 20,  0.85 → 40,  1.0 → 65,  1.1 → 80,  1.2+ → 95
        if ratio >= 1.15:
            return min(100.0, 65 + (ratio - 1.0) * 200)
        elif ratio >= 1.0:
            return 65 + (ratio - 1.0) * 150
        else:
            return max(0.0, 65 - (1.0 - ratio) * 250)

    def _recovery_quality(
        self,
        runs: List[NormalizedRun],
        feedback: Dict[str, ManualFeedback],
    ) -> float:
        cutoff = datetime.now() - timedelta(days=7)
        recent = [fb for fb in feedback.values() if fb.date >= cutoff]

        scores: List[float] = []
        for fb in recent:
            if fb.sleep_quality:
                scores.append((fb.sleep_quality / 5.0) * 100)
            if fb.mood:
                scores.append((fb.mood / 5.0) * 100)
            if fb.sleep_hours is not None:
                deviation = abs(fb.sleep_hours - 8.0)
                scores.append(max(0.0, 100.0 - deviation * 15.0))

        base = (sum(scores) / len(scores)) if scores else 60.0
        rest_bonus = self._rest_day_bonus(runs)
        return min(100.0, base + rest_bonus)

    def _rest_day_bonus(self, runs: List[NormalizedRun]) -> float:
        if not runs:
            return 0.0
        run_dates = {r.date.date() for r in runs}
        earliest  = min(run_dates)
        today     = datetime.now().date()
        bonus     = 0.0
        for offset in range(1, 4):
            day = today - timedelta(days=offset)
            if day >= earliest and day not in run_dates:
                bonus += self.config.rest_day_bonus
        return min(15.0, bonus)
