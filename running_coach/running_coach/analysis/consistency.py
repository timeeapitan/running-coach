"""
Consistency score calculator.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Tuple

from .base import BaseCalculator
from ..schemas import NormalizedRun, ManualFeedback


class ConsistencyCalculator(BaseCalculator):
    """
    Calculates training consistency on a 0-100 scale based on:
      - Run frequency relative to the target (runs_per_week)
      - Weekly volume stability (low coefficient of variation = high score)
      - Absence of long unplanned gaps between runs
    """

    def calculate(
        self,
        runs: List[NormalizedRun],
        feedback: Dict[str, ManualFeedback],
    ) -> Tuple[float, Dict[str, float]]:
        weeks = self.config.consistency_window_weeks

        if len(runs) < self.config.min_runs_for_consistency:
            return 50.0, {}

        regularity      = self._regularity_score(runs, weeks)
        vol_stability   = self._volume_stability_score(runs, weeks)
        gap_score       = self._gap_score(runs)

        raw = (
            regularity    * 0.50
            + vol_stability * 0.30
            + gap_score     * 0.20
        )
        score = min(100.0, max(0.0, raw))

        factors = {
            "regularity":       round(regularity, 1),
            "volume_stability": round(vol_stability, 1),
            "gap_score":        round(gap_score, 1),
        }
        return round(score, 1), factors

    # ------------------------------------------------------------------

    def _weekly_volumes(self, runs: List[NormalizedRun], weeks: int) -> List[float]:
        """Per-week distances for the last N weeks (including zero weeks)."""
        now = datetime.now()
        volumes = []
        for w in range(weeks):
            start = now - timedelta(weeks=w + 1)
            end   = now - timedelta(weeks=w)
            vol   = sum(r.distance_km for r in runs if start <= r.date < end)
            volumes.append(vol)
        return volumes

    def _weekly_run_counts(self, runs: List[NormalizedRun], weeks: int) -> List[int]:
        now = datetime.now()
        counts = []
        for w in range(weeks):
            start = now - timedelta(weeks=w + 1)
            end   = now - timedelta(weeks=w)
            counts.append(sum(1 for r in runs if start <= r.date < end))
        return counts

    def _regularity_score(self, runs: List[NormalizedRun], weeks: int) -> float:
        """
        0-100: fraction of weeks where run count is within ±1 of the target.
        """
        counts = self._weekly_run_counts(runs, weeks)
        target = self.profile.runs_per_week
        if not counts:
            return 0.0
        hit_ratio = sum(1 for c in counts if abs(c - target) <= 1) / len(counts)
        return hit_ratio * 100.0

    def _volume_stability_score(self, runs: List[NormalizedRun], weeks: int) -> float:
        """
        0-100 based on coefficient of variation of weekly volumes.
        Includes zero-volume weeks so skipped weeks are penalised.
        cv=0 → 100,  cv=0.5 → 50,  cv≥1 → 0
        """
        vols = self._weekly_volumes(runs, weeks)  # includes zeros
        mean = self.safe_mean(vols)
        if mean == 0:
            return 0.0
        if len(vols) < 2:
            return 50.0
        cv = self.safe_stdev(vols) / mean
        return max(0.0, min(100.0, (1.0 - cv) * 100.0))

    def _gap_score(self, runs: List[NormalizedRun]) -> float:
        """
        0-100 score for the typical gap between runs in the last 4 weeks.
        Expected gap = 7 / runs_per_week days.
        Gaps much larger than expected reduce the score.
        """
        recent = self.get_recent_runs(runs, days=28)
        if len(recent) < 2:
            return 50.0

        sorted_runs = sorted(recent, key=lambda r: r.date)
        gaps = [
            (sorted_runs[i + 1].date - sorted_runs[i].date).days
            for i in range(len(sorted_runs) - 1)
        ]

        expected_gap = 7.0 / max(1, self.profile.runs_per_week)
        max_gap = max(gaps)
        avg_gap = self.safe_mean(gaps)

        # Score drops when gaps exceed 2× expected
        max_ratio = max_gap / expected_gap
        avg_ratio = avg_gap / expected_gap

        # max_ratio=1 → 100,  max_ratio=3 → 0
        max_score = max(0.0, min(100.0, (3.0 - max_ratio) / 2.0 * 100.0))
        avg_score = max(0.0, min(100.0, (2.0 - avg_ratio) / 1.0 * 100.0))

        return (max_score * 0.6 + avg_score * 0.4)
