"""
Readiness calculator — combines training load with recovery signals.

Garmin/watch metrics are consumed from the app's DB-backed feedback cache; this
module never calls Garmin directly. When present, sleep, HRV, Body Battery,
stress and resting HR all influence the readiness score. Missing metrics are
simply omitted and the remaining weights are re-normalised.
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

        energy = max(0.0, min(100.0, 100.0 - fatigue_score))
        recovery = self._recovery_quality(runs, feedback)
        momentum = consistency_score

        # Recovery signals sourced from today's/most-recent cached Garmin data.
        hrv_score = self._hrv_score(feedback)
        body_battery_score = self._latest_metric_score(feedback, "body_battery")
        stress_score = self._stress_score(feedback)
        resting_hr_score = self._resting_hr_score(feedback)

        # Base training-context signals are always present. Garmin signals are
        # additive when available, without punishing users for missing data.
        weighted = [
            (energy, 0.30),
            (recovery, 0.22),
            (momentum, 0.13),
        ]
        if hrv_score is not None:
            weighted.append((hrv_score, 0.15))
        if body_battery_score is not None:
            weighted.append((body_battery_score, 0.10))
        if stress_score is not None:
            weighted.append((stress_score, 0.05))
        if resting_hr_score is not None:
            weighted.append((resting_hr_score, 0.05))

        total_weight = sum(weight for _, weight in weighted)
        raw = sum(value * weight for value, weight in weighted) / total_weight

        # A very recent/hard run still gets an explicit recovery penalty.
        last_run_penalty = self._last_run_penalty(runs)
        raw = max(0.0, raw - last_run_penalty)
        score = min(100.0, max(0.0, raw))

        factors: Dict[str, float] = {
            "energy_available": round(energy, 1),
            "recovery_quality": round(recovery, 1),
            "consistency_momentum": round(momentum, 1),
        }
        if hrv_score is not None:
            factors["hrv_score"] = round(hrv_score, 1)
        if body_battery_score is not None:
            factors["body_battery_score"] = round(body_battery_score, 1)
        if stress_score is not None:
            factors["stress_score"] = round(stress_score, 1)
        if resting_hr_score is not None:
            factors["resting_hr_score"] = round(resting_hr_score, 1)
        if last_run_penalty > 0:
            factors["last_run_penalty"] = round(last_run_penalty, 1)

        return round(score, 1), factors

    # ── Last run penalty ──────────────────────────────────────────────

    def _last_run_penalty(self, runs: List[NormalizedRun]) -> float:
        if not runs:
            return 0.0

        now = datetime.now()
        last_run = sorted(runs, key=lambda r: r.date, reverse=True)[0]
        days_ago = (now - last_run.date).days

        if days_ago >= 3:
            return 0.0

        hr_intensity = 0.5
        if last_run.avg_hr:
            est_max = float(getattr(self.profile, "max_hr", None) or 185.0)
            hr_intensity = min(1.0, last_run.avg_hr / est_max)

        dist_factor = min(1.0, last_run.distance_km / 12.0)
        effort = (hr_intensity * 0.65) + (dist_factor * 0.35)

        if days_ago == 0:
            time_factor = 1.0
        elif days_ago == 1:
            time_factor = 0.6
        else:
            time_factor = 0.2

        return round(25.0 * effort * time_factor, 1)

    # ── Garmin/watch recovery metrics ────────────────────────────────

    def _recent_feedback(self, feedback: Dict[str, ManualFeedback], days: int = 30):
        now = datetime.now()
        return [fb for fb in feedback.values() if (now - fb.date).days <= days]

    def _latest_metric_score(self, feedback, attr: str) -> Optional[float]:
        values = [fb for fb in self._recent_feedback(feedback, 3)
                  if getattr(fb, attr, None) is not None]
        if not values:
            return None
        latest = max(values, key=lambda fb: fb.date)
        return max(0.0, min(100.0, float(getattr(latest, attr))))

    def _hrv_score(self, feedback: Dict[str, ManualFeedback]) -> Optional[float]:
        now = datetime.now()
        window = [fb for fb in feedback.values()
                  if fb.hrv_ms is not None and (now - fb.date).days <= 30]
        if not window:
            return None

        latest = max(window, key=lambda fb: fb.date)
        baseline_readings = [fb.hrv_ms for fb in window
                             if fb.date.date() != latest.date.date()
                             and 1 <= (latest.date - fb.date).days <= 14]
        if not baseline_readings:
            baseline_readings = [fb.hrv_ms for fb in window]

        baseline = sum(baseline_readings) / len(baseline_readings)
        ratio = latest.hrv_ms / baseline if baseline > 0 else 1.0

        if ratio >= 1.15:
            return min(100.0, 65 + (ratio - 1.0) * 200)
        if ratio >= 1.0:
            return 65 + (ratio - 1.0) * 150
        return max(0.0, 65 - (1.0 - ratio) * 250)

    def _stress_score(self, feedback: Dict[str, ManualFeedback]) -> Optional[float]:
        values = [fb for fb in self._recent_feedback(feedback, 3) if fb.stress is not None]
        if not values:
            return None
        latest = max(values, key=lambda fb: fb.date)
        # Garmin stress is higher when recovery is worse, so invert it.
        return max(0.0, min(100.0, 100.0 - float(latest.stress)))

    def _resting_hr_score(self, feedback: Dict[str, ManualFeedback]) -> Optional[float]:
        values = [fb for fb in self._recent_feedback(feedback, 30) if fb.resting_hr is not None]
        if len(values) < 2:
            return None

        latest = max(values, key=lambda fb: fb.date)
        baseline_values = [fb.resting_hr for fb in values
                           if fb.date.date() != latest.date.date()
                           and 1 <= (latest.date - fb.date).days <= 14]
        if not baseline_values:
            return None

        baseline = sum(baseline_values) / len(baseline_values)
        delta_pct = (float(latest.resting_hr) - baseline) / baseline if baseline else 0.0
        # Around baseline = neutral/good. A notably elevated resting HR lowers readiness.
        if delta_pct <= -0.05:
            return 85.0
        if delta_pct <= 0.03:
            return 75.0
        if delta_pct <= 0.08:
            return 55.0
        if delta_pct <= 0.12:
            return 35.0
        return 20.0

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
                # 8h is ideal; short nights reduce the score more quickly than
                # slightly longer nights, while still keeping a bounded signal.
                if fb.sleep_hours < 8.0:
                    scores.append(max(0.0, 100.0 - (8.0 - fb.sleep_hours) * 18.0))
                else:
                    scores.append(max(70.0, 100.0 - (fb.sleep_hours - 8.0) * 8.0))
        base = (sum(scores) / len(scores)) if scores else 60.0
        rest_bonus = self._rest_day_bonus(runs)
        return min(100.0, base + rest_bonus)

    def _rest_day_bonus(self, runs) -> float:
        if not runs:
            return 0.0
        run_dates = {r.date.date() for r in runs}
        today = datetime.now().date()
        bonus = 0.0
        for offset in range(1, 4):
            day = today - timedelta(days=offset)
            if day >= min(run_dates) and day not in run_dates:
                bonus += self.config.rest_day_bonus
        return min(15.0, bonus)
