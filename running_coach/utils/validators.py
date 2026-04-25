"""
Input validation helpers.
"""

from typing import Any


def validate_score(value: float, name: str = "score") -> float:
    """Clamp a score to [0, 100] with a warning if out of range."""
    if not (0 <= value <= 100):
        value = max(0.0, min(100.0, value))
    return float(value)


def require_positive(value: Any, name: str) -> float:
    val = float(value)
    if val <= 0:
        raise ValueError(f"{name} must be positive, got {val}")
    return val
