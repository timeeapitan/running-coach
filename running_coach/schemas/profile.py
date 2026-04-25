"""
Runner profile — personal settings persisted as JSON so you set them once.
"""

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional, Dict, Tuple

from .enums import FitnessLevel


@dataclass
class RunnerProfile:
    # Identity
    name: str = "Runner"
    age: Optional[int] = None

    # Heart rate (pull from Garmin settings or enter manually)
    max_hr: Optional[int] = None
    resting_hr: Optional[int] = None

    # Training habits
    fitness_level: FitnessLevel = FitnessLevel.INTERMEDIATE
    runs_per_week: int = 3
    goal_weekly_km: Optional[float] = None

    # Personal pace benchmarks (min/km) — filled in from your actual runs
    easy_pace_min_per_km: Optional[float] = None
    threshold_pace_min_per_km: Optional[float] = None

    # Race goal (optional — activates the training plan generator)
    race_date: Optional[str] = None        # ISO date string "2025-10-12"
    race_distance_km: Optional[float] = None   # 5, 10, 21.1, 42.2
    race_goal_time_minutes: Optional[float] = None  # your target finish time

    # ------------------------------------------------------------------

    def get_effective_max_hr(self) -> int:
        if self.max_hr:
            return self.max_hr
        if self.age:
            return int(208 - 0.7 * self.age)
        return 185

    def get_hr_zones(self) -> Dict[str, Tuple[int, int]]:
        """Karvonen method when resting_hr known, else % of max."""
        mhr = self.get_effective_max_hr()
        if self.resting_hr:
            hrr = mhr - self.resting_hr
            def z(lo, hi):
                return (int(self.resting_hr + hrr * lo), int(self.resting_hr + hrr * hi))
        else:
            def z(lo, hi):
                return (int(mhr * lo), int(mhr * hi))
        return {
            "recovery":  z(0.50, 0.60),
            "easy":      z(0.60, 0.70),
            "aerobic":   z(0.70, 0.80),
            "threshold": z(0.80, 0.90),
            "max":       z(0.90, 1.00),
        }

    def weeks_to_race(self) -> Optional[int]:
        if not self.race_date:
            return None
        try:
            rd = date.fromisoformat(self.race_date)
            delta = (rd - date.today()).days
            return max(0, delta // 7)
        except ValueError:
            return None

    def race_goal_pace(self) -> Optional[float]:
        """Return goal pace in min/km from target time and distance."""
        if self.race_goal_time_minutes and self.race_distance_km:
            return self.race_goal_time_minutes / self.race_distance_km
        return None

    # ------------------------------------------------------------------
    # Persistence

    def save(self, path: str = "profile.json") -> None:
        d = asdict(self)
        d["fitness_level"] = self.fitness_level.value
        with open(path, "w") as f:
            json.dump(d, f, indent=2)

    @classmethod
    def load(cls, path: str = "profile.json") -> "RunnerProfile":
        with open(path) as f:
            d = json.load(f)
        d["fitness_level"] = FitnessLevel(d.get("fitness_level", "intermediate"))
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def from_interactive(cls) -> "RunnerProfile":
        """Build a profile by asking questions on the terminal."""
        print("\n=== Personal profile setup ===")
        name    = input("Your name: ").strip() or "Runner"
        age     = _int("Age (enter to skip): ")
        max_hr  = _int("Max heart rate from Garmin/watch (enter to skip): ")
        rhr     = _int("Resting heart rate (enter to skip): ")
        rpw     = _int("Runs per week target [3]: ") or 3
        goal_km = _float("Weekly km goal (enter to skip): ")

        print("\nFitness level: 1=beginner  2=intermediate  3=advanced  4=elite")
        lvl_map = {"1": FitnessLevel.BEGINNER, "2": FitnessLevel.INTERMEDIATE,
                   "3": FitnessLevel.ADVANCED, "4": FitnessLevel.ELITE}
        lvl = lvl_map.get(input("Choice [2]: ").strip(), FitnessLevel.INTERMEDIATE)

        print("\n--- Race goal (optional, press Enter to skip) ---")
        race_date = input("Race date (YYYY-MM-DD): ").strip() or None
        race_dist = _float("Race distance km (5 / 10 / 21.1 / 42.2): ") if race_date else None
        race_time = _float("Goal finish time in minutes (e.g. 240 for 4h marathon): ") if race_date else None

        return cls(
            name=name, age=age, max_hr=max_hr, resting_hr=rhr,
            runs_per_week=rpw, goal_weekly_km=goal_km, fitness_level=lvl,
            race_date=race_date, race_distance_km=race_dist, race_goal_time_minutes=race_time,
        )


def _int(prompt: str) -> Optional[int]:
    v = input(prompt).strip()
    return int(v) if v.isdigit() else None

def _float(prompt: str) -> Optional[float]:
    v = input(prompt).strip()
    try: return float(v)
    except ValueError: return None
