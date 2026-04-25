"""
Base classes and utilities for analysis.
"""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
from statistics import mean, stdev

from ..schemas import NormalizedRun, ManualFeedback, RunnerProfile
from ..config import ANALYSIS_CONFIG, AnalysisConfig


class BaseCalculator(ABC):
    """Base class for score calculators."""
    
    def __init__(self, profile: RunnerProfile, config: Optional[AnalysisConfig] = None):
        self.profile = profile
        self.config = config or ANALYSIS_CONFIG
    
    @abstractmethod
    def calculate(
        self,
        runs: List[NormalizedRun],
        feedback: Dict[str, ManualFeedback]
    ) -> Tuple[float, Dict[str, float]]:
        """Calculate score and return (score, factors)."""
        pass
    
    def get_recent_runs(
        self,
        runs: List[NormalizedRun],
        days: int
    ) -> List[NormalizedRun]:
        """Get runs from the last N days."""
        cutoff = datetime.now() - timedelta(days=days)
        return [r for r in runs if r.date >= cutoff]
    
    def calculate_volume(self, runs: List[NormalizedRun]) -> float:
        """Calculate total volume from runs."""
        return sum(r.distance_km for r in runs)
    
    def safe_mean(self, values: List[float], default: float = 0.0) -> float:
        """Calculate mean with empty list handling."""
        return mean(values) if values else default
    
    def safe_stdev(self, values: List[float], default: float = 0.0) -> float:
        """Calculate standard deviation with handling for small samples."""
        if len(values) < 2:
            return default
        return stdev(values)
