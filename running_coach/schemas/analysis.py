"""
Schema for analysis results.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class AnalysisResult:
    """Result of the full running analysis."""

    fatigue_score: float       # 0-100 (higher = more fatigued)
    consistency_score: float   # 0-100 (higher = more consistent)
    readiness_score: float     # 0-100 (higher = more ready to train)

    fatigue_factors: Dict[str, float] = field(default_factory=dict)
    consistency_factors: Dict[str, float] = field(default_factory=dict)
    readiness_factors: Dict[str, float] = field(default_factory=dict)

    recent_volume_km: float = 0.0
    average_weekly_volume_km: float = 0.0
    trend: str = "stable"           # "increasing" | "stable" | "decreasing"
    warnings: List[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"Fatigue {self.fatigue_score:.0f}/100 | "
            f"Consistency {self.consistency_score:.0f}/100 | "
            f"Readiness {self.readiness_score:.0f}/100 | "
            f"Trend: {self.trend}"
        )
