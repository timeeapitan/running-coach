"""
Readiness calculator — incorporates HRV, last run intensity, and recovery days.

Changes:
  - Last run intensity now reduces readiness if run was recent and hard
  - Days since last run gives a recovery bonus after adequate rest
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

        # Last run penalty — reduces readiness if recent hard session
        last_run_penalty = self._last_run_penalty(runs)

        has_hrv = hrv_score is not None
        if has_hrv:
            w_e, w_r, w_h, w_m = 0.35, 0.20, 0.25, 0.20
            raw = energy * w_e + recovery * w_r + hrv_score * w_h + momentum * w_m
        else:
            w_e, w_r, w_m = 0.40, 0.30, 0.30
            raw = energy * w_e + recovery * w_r + momentum * w_m

        # Apply last run penalty after weighted sum
        raw   = max(0.0, raw - last_run_penalty)
        score = min(100.0, max(0.0, raw))

        factors: Dict[str, float] = {
            "energy_available":     round(energy,           1),
            "recovery_quality":     round(recovery,         1),
            "consistency_momentum": round(momentum,         1),
        }
        if has_hrv:
            factors["hrv_score"] = round(hrv_score, 1)
        if last_run_penalty > 0:
            factors["last_run_penalty"] = round(last_run_penalty, 1)

        return round(score, 1), factors

    # ── Last run penalty ──────────────────────────────────────────────

    def _last_run_penalty(self, runs: List[NormalizedRun]) -> float:
        """
        Reduces readiness based on how recent and hard the last run was.

        Logic:
          - Ran today at high HR   → up to -25 points
          - Ran yesterday at high HR → up to -15 points
          - Ran 2 days ago at high HR → up to -5 points
          - Easy runs have much lower penalty
          - 3+ days ago: no penalty regardless of intensity
        """
        if not runs:
            return 0.0

        now      = datetime.now()
        last_run = sorted(runs, key=lambda r: r.date, reverse=True)[0]
        days_ago = (now - last_run.date).days

        if days_ago >= 3:
            return 0.0  # enough recovery time — no penalty

        # HR intensity factor 0-1
        hr_intensity = 0.5  # default if no HR data
        if last_run.avg_hr:
            # Use a reasonable max HR estimate if not in profile
            est_max = 185.0
            hr_intensity = min(1.0, last_run.avg_hr / est_max)

        # Distance factor 0-1 (longer = harder to recover from)
        dist_factor = min(1.0, last_run.distance_km / 12.0)

        # Combined effort 0-1
        effort = (hr_intensity * 0.65) + (dist_factor * 0.35)

        # Scale penalty by days ago
        if days_ago == 0:    time_factor = 1.0   # today
        elif days_ago == 1:  time_factor = 0.6   # yesterday
        else:                time_factor = 0.2   # 2 days ago

        max_penalty = 25.0
        penalty = max_penalty * effort * time_factor

        return round(penalty, 1)

    # ── HRV ──────────────────────────────────────────────────────────

    def _hrv_score(self, feedback: Dict[str, ManualFeedback]) -> Optional[float]:
        now    = datetime.now()
        window = [(key, fb) for key, fb in feedback.items()
                  if fb.hrv_ms is not None and (now - fb.date).days <= 30]
        if not window:
            return None

        baseline_readings = [fb.hrv_ms for _, fb in window
                              if 1 <= (now - fb.date).days <= 8]
        if not baseline_readings:
            baseline_readings = [fb.hrv_ms for _, fb in window]

        baseline  = sum(baseline_readings) / len(baseline_readings)
        latest_fb = sorted(window, key=lambda x: x[1].date)[-1][1]
        ratio     = latest_fb.hrv_ms / baseline if baseline > 0 else 1.0

        if ratio >= 1.15:  return min(100.0, 65 + (ratio - 1.0) * 200)
        elif ratio >= 1.0: return 65 + (ratio - 1.0) * 150
        else:              return max(0.0, 65 - (1.0 - ratio) * 250)

    # ── Recovery quality ──────────────────────────────────────────────

    def _recovery_quality(self, runs, feedback) -> float:
        cutoff = datetime.now() - timedelta(days=7)
        recent = [fb for fb in feedback.values() if fb.date >= cutoff]
        scores = []
        for fb in recent:
            if fb.sleep_quality:
                scores.append((fb.sleep_quality / 5.0) * 100)
            if fb.mood:
                scores.append((fb.mood / 5.0) * 100)
            if fb.sleep_hours is not None:
                scores.append(max(0.0, 100.0 - abs(fb.sleep_hours - 8.0) * 15.0))
        base       = (sum(scores) / len(scores)) if scores else 60.0
        rest_bonus = self._rest_day_bonus(runs)
        return min(100.0, base + rest_bonus)

    def _rest_day_bonus(self, runs) -> float:
        if not runs:
            return 0.0
        run_dates = {r.date.date() for r in runs}
        today     = datetime.now().date()
        bonus     = 0.0
        for offset in range(1, 4):
            day = today - timedelta(days=offset)
            if day >= min(run_dates) and day not in run_dates:
                bonus += self.config.rest_day_bonus
        return min(15.0, bonus)
