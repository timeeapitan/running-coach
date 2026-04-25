"""
Global configuration and tuning constants.
"""

from dataclasses import dataclass, field
from typing import Dict, Any


@dataclass
class AnalysisConfig:
    # --- Fatigue ---
    atl_decay_days: int = 7       # Acute Training Load window
    ctl_decay_days: int = 42      # Chronic Training Load window
    acr_spike_threshold: float = 1.5  # ATL/CTL ratio that triggers high fatigue
    max_consecutive_days: int = 7

    # --- Consistency ---
    consistency_window_weeks: int = 4
    min_runs_for_consistency: int = 3

    # --- Readiness ---
    rest_day_bonus: float = 5.0   # Readiness bump per rest day (up to cap)
    readiness_fatigue_weight: float = 0.4
    readiness_consistency_weight: float = 0.3
    readiness_recovery_weight: float = 0.3

    # --- Warnings ---
    volume_spike_ratio: float = 1.30   # >30% increase triggers warning
    high_fatigue_threshold: float = 70.0

    # --- Coaching ---
    max_weekly_increase_pct: float = 10.0  # 10% rule


# Singleton used as the default throughout the codebase
ANALYSIS_CONFIG = AnalysisConfig()
