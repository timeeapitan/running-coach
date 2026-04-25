"""
Fatigue calculator — uses HR-based Training Stress Score (TSS) when available,
falls back to pace/distance proxy otherwise.

TSS per session = (duration_s × HR_ratio²) / 3600 × 100
where HR_ratio = avg_hr / threshold_hr  (threshold ≈ 90% of max HR)

This is the same metric Garmin's Training Load uses internally.
"""

from datetime import datetime, timedelta
from math import exp
from typing import Dict, List, Optional, Tuple

from .base import BaseCalculator
from ..schemas import NormalizedRun, ManualFeedback


class FatigueCalculator(BaseCalculator):

    def calculate(
        self,
        runs: List[NormalizedRun],
        feedback: Dict[str, ManualFeedback],
    ) -> Tuple[float, Dict[str, float]]:
        if not runs:
            return 0.0, {}

        atl = self._ewma_tss(runs, days=self.config.atl_decay_days)
        ctl = self._ewma_tss(runs, days=self.config.ctl_decay_days)

        ratio = atl / ctl if ctl > 0 else 1.0
        ratio_score = min(100.0, max(0.0, (ratio - 0.0) * 40.0))

        recent = self.get_recent_runs(runs, days=7)
        acute_tss = sum(self._session_tss(r) for r in recent)
        # ~300 TSS/week = moderate load → 25 pts
        acute_score = min(50.0, acute_tss * (25.0 / 300.0))

        consec = self._consecutive_days(runs)
        consec_score = min(20.0, max(0.0, (consec - 1) * 4.0))

        rpe_score = self._rpe_score(runs, feedback)

        raw = (ratio_score * 0.40 + acute_score * 0.35
               + consec_score * 0.15 + rpe_score * 0.10)
        score = min(100.0, max(0.0, raw))

        # Also expose raw ATL/CTL/TSB for the CLI dashboard
        tsb = ctl - atl
        return round(score, 1), {
            "atl": round(atl, 1),
            "ctl": round(ctl, 1),
            "tsb": round(tsb, 1),
            "atl_ctl_ratio": round(ratio_score, 1),
            "acute_load":    round(acute_score, 1),
            "consecutive_days": round(consec_score, 1),
            "rpe_feedback":  round(rpe_score, 1),
        }

    # ------------------------------------------------------------------

    def _session_tss(self, run: NormalizedRun) -> float:
        """
        Training Stress Score for one run.
        Uses HR when available; falls back to pace-based intensity.
        """
        duration_h = run.duration_minutes / 60.0
        thr_hr = self.profile.get_hr_zones().get("threshold", (160, 180))[0]

        if run.avg_hr and thr_hr > 0:
            hr_ratio = run.avg_hr / thr_hr
        elif run.avg_pace_min_per_km and self.profile.threshold_pace_min_per_km:
            # Faster than threshold → ratio > 1, slower → < 1
            pace_ratio = self.profile.threshold_pace_min_per_km / run.avg_pace_min_per_km
            hr_ratio = 0.6 + pace_ratio * 0.4
        else:
            hr_ratio = 0.75  # assume moderate easy run

        tss = duration_h * (hr_ratio ** 2) * 100.0
        return min(tss, 300.0)  # cap single session at 300

    def _ewma_tss(self, runs: List[NormalizedRun], days: int) -> float:
        decay = 1.0 / days
        now = datetime.now()
        total = 0.0
        for r in runs:
            age = (now - r.date).total_seconds() / 86400.0
            total += self._session_tss(r) * exp(-decay * age)
        return total

    def _consecutive_days(self, runs: List[NormalizedRun]) -> int:
        run_dates = {r.date.date() for r in runs}
        today = datetime.now().date()
        start = today if today in run_dates else today - timedelta(days=1)
        count = 0
        d = start
        while d in run_dates:
            count += 1
            d -= timedelta(days=1)
        return count

    def _rpe_score(self, runs, feedback) -> float:
        recent = self.get_recent_runs(runs, days=7)
        vals = []
        for r in recent:
            key = r.date.date().isoformat()
            fb = feedback.get(key)
            rpe = (fb.rpe if fb and fb.rpe else r.rpe)
            if rpe is not None:
                vals.append(rpe)
        if not vals:
            return 0.0
        return max(0.0, min(15.0, (sum(vals)/len(vals) - 5.0) * 3.0))
