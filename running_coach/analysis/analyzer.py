"""
Main analyzer that combines all calculators.
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any
from statistics import mean

from .fatigue import FatigueCalculator
from .consistency import ConsistencyCalculator
from .readiness import ReadinessCalculator
from ..schemas import NormalizedRun, ManualFeedback, RunnerProfile, AnalysisResult
from ..config import ANALYSIS_CONFIG


class RunningAnalyzer:
    """Analyzes running data to compute fatigue, consistency, and readiness."""
    
    def __init__(self, profile: RunnerProfile, config: Optional[Dict[str, Any]] = None):
        self.profile = profile
        self.config = config or ANALYSIS_CONFIG
        
        self.fatigue_calc = FatigueCalculator(profile, config)
        self.consistency_calc = ConsistencyCalculator(profile, config)
        self.readiness_calc = ReadinessCalculator(profile, config)
    
    def analyze(
        self,
        runs: List[NormalizedRun],
        feedback: Optional[Dict[str, ManualFeedback]] = None
    ) -> AnalysisResult:
        """Perform complete analysis on running history."""
        if not runs:
            return self._empty_analysis()
        
        feedback = feedback or {}
        
        fatigue_score, fatigue_factors = self.fatigue_calc.calculate(runs, feedback)
        consistency_score, consistency_factors = self.consistency_calc.calculate(runs, feedback)
        readiness_score, readiness_factors = self.readiness_calc.calculate(
            runs, feedback, fatigue_score, consistency_score
        )
        
        recent_volume = self._calculate_recent_volume(runs, days=7)
        avg_weekly_volume = self._calculate_average_weekly_volume(runs, weeks=4)
        trend = self._determine_trend(runs)
        
        warnings = self._generate_warnings(runs, feedback, fatigue_score)
        
        return AnalysisResult(
            fatigue_score=round(fatigue_score, 1),
            consistency_score=round(consistency_score, 1),
            readiness_score=round(readiness_score, 1),
            fatigue_factors=fatigue_factors,
            consistency_factors=consistency_factors,
            readiness_factors=readiness_factors,
            recent_volume_km=round(recent_volume, 1),
            average_weekly_volume_km=round(avg_weekly_volume, 1),
            trend=trend,
            warnings=warnings,
        )
    
    def _calculate_recent_volume(self, runs: List[NormalizedRun], days: int) -> float:
        """Sum of distance in the last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        return sum(r.distance_km for r in runs if r.date >= cutoff)
    
    def _calculate_average_weekly_volume(self, runs: List[NormalizedRun], weeks: int) -> float:
        """Average weekly volume over the last N weeks."""
        cutoff = datetime.now() - timedelta(weeks=weeks)
        relevant = [r for r in runs if r.date >= cutoff]
        
        if not relevant:
            return 0.0
        
        total = sum(r.distance_km for r in relevant)
        actual_weeks = max(1, (datetime.now() - relevant[0].date).days / 7)
        
        return total / min(weeks, actual_weeks)
    
    def _determine_trend(self, runs: List[NormalizedRun]) -> str:
        """Determine if volume is increasing, stable, or decreasing."""
        if len(runs) < 4:
            return "stable"
        
        now = datetime.now()
        recent_cutoff = now - timedelta(weeks=2)
        older_cutoff = now - timedelta(weeks=4)
        
        recent = sum(r.distance_km for r in runs if r.date >= recent_cutoff)
        older = sum(r.distance_km for r in runs if older_cutoff <= r.date < recent_cutoff)
        
        if older == 0:
            return "stable"
        
        ratio = recent / older
        
        if ratio > 1.15:
            return "increasing"
        elif ratio < 0.85:
            return "decreasing"
        return "stable"
    
    def _generate_warnings(
        self,
        runs: List[NormalizedRun],
        feedback: Dict[str, ManualFeedback],
        fatigue_score: float
    ) -> List[str]:
        """Generate warning messages."""
        warnings = []
        
        # Pain flags
        cutoff = datetime.now() - timedelta(days=14)
        for run in runs:
            if run.date < cutoff:
                continue
            date_key = run.date.date().isoformat()
            if date_key in feedback and feedback[date_key].pain_flag:
                location = feedback[date_key].pain_location or "unspecified"
                warnings.append(f"Pain reported on {run.date.date()}: {location}")
        
        # Volume spike
        recent = self._calculate_recent_volume(runs, days=7)
        baseline = self._calculate_average_weekly_volume(runs, weeks=4)
        if baseline > 0 and recent > baseline * 1.3:
            warnings.append(
                f"Volume spike: {recent:.1f}km this week vs {baseline:.1f}km average"
            )
        
        # High fatigue
        if fatigue_score > 70:
            warnings.append(f"High fatigue ({fatigue_score:.0f}/100) - consider recovery")
        
        # HR pattern
        recent_hrs = [r.avg_hr for r in runs[-5:] if r.avg_hr]
        older_hrs = [r.avg_hr for r in runs[-15:-5] if r.avg_hr]
        if recent_hrs and older_hrs:
            if mean(recent_hrs) > mean(older_hrs) * 1.1:
                warnings.append("Elevated HR pattern - possible fatigue")
        
        return warnings
    
    def _empty_analysis(self) -> AnalysisResult:
        """Return neutral analysis when no data available."""
        return AnalysisResult(
            fatigue_score=0.0,
            consistency_score=0.0,
            readiness_score=50.0,
            fatigue_factors={},
            consistency_factors={},
            readiness_factors={},
            recent_volume_km=0.0,
            average_weekly_volume_km=0.0,
            trend="stable",
            warnings=["No running data available"],
        )
