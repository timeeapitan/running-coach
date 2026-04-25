"""
Garmin Connect data parser — handles both CSV and JSON exports.

CSV column mapping tested against real Garmin Connect exports.
Handles Garmin's quirks: commas in numbers, "--" for missing values,
"mm:ss" pace format, "hh:mm:ss.d" duration format.
"""

import csv
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..schemas import NormalizedRun, ActivityType


RUN_TYPES = {
    "running":          ActivityType.OUTDOOR_RUN,
    "run":              ActivityType.OUTDOOR_RUN,
    "outdoor running":  ActivityType.OUTDOOR_RUN,
    "treadmill running":ActivityType.TREADMILL_RUN,
    "treadmill_running":ActivityType.TREADMILL_RUN,
    "trail running":    ActivityType.TRAIL_RUN,
    "trail_running":    ActivityType.TRAIL_RUN,
    "track running":    ActivityType.OUTDOOR_RUN,
    "track_running":    ActivityType.OUTDOOR_RUN,
}


class GarminParser:

    def load(self, filepath: str) -> List[NormalizedRun]:
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        ext = os.path.splitext(filepath)[1].lower()
        if ext == ".csv":
            runs = self._load_csv(filepath)
        elif ext == ".json":
            runs = self._load_json(filepath)
        else:
            raise ValueError(f"Unsupported format: {ext}")
        runs.sort(key=lambda r: r.date)
        return runs

    # ------------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------------

    def _load_csv(self, filepath: str) -> List[NormalizedRun]:
        runs = []
        with open(filepath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                run = self._parse_csv_row(row)
                if run:
                    runs.append(run)
        return runs

    def _parse_csv_row(self, row: Dict[str, str]) -> Optional[NormalizedRun]:
        raw_type = row.get("Activity Type", "").strip().lower()
        activity_type = RUN_TYPES.get(raw_type)
        if activity_type is None:
            return None

        try:
            date     = self._parse_date(row.get("Date", ""))
            dist_km  = self._float(row.get("Distance", ""))
            duration = self._duration(row.get("Time", ""))

            if not dist_km or dist_km <= 0 or not duration or duration <= 0:
                return None

            avg_hr   = self._float(row.get("Avg HR", ""))
            max_hr   = self._float(row.get("Max HR", ""))

            # Garmin exports elevation as "Total Ascent" not "Elev Gain"
            elevation = (self._float(row.get("Total Ascent", ""))
                         or self._float(row.get("Elev Gain", "")))

            # Cadence: running cadence stored in "Avg Bike Cadence" column (Garmin quirk)
            cadence = (self._int(row.get("Avg Run Cadence", ""))
                       or self._int(row.get("Avg Bike Cadence", "")))

            # Pace: "Avg Speed" column is actually pace in mm:ss format for runs
            pace = (self._pace(row.get("Avg Speed", ""))
                    or (duration / dist_km if dist_km else None))

            # Extra Garmin fields
            norm_power = self._float(row.get("Normalized Power® (NP®)", ""))
            avg_power  = self._float(row.get("Avg Power", ""))
            gct        = self._float(row.get("Avg Ground Contact Time", ""))
            vert_ratio = self._float(row.get("Avg Vertical Ratio", ""))
            vert_osc   = self._float(row.get("Avg Vertical Oscillation", ""))

            return NormalizedRun(
                date=date,
                activity_type=activity_type,
                distance_km=dist_km,
                duration_minutes=duration,
                avg_pace_min_per_km=round(pace, 3) if pace else None,
                avg_hr=avg_hr,
                max_hr=max_hr,
                elevation_gain_m=elevation,
                cadence=cadence,
                source="garmin_csv",
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # JSON
    # ------------------------------------------------------------------

    def _load_json(self, filepath: str) -> List[NormalizedRun]:
        with open(filepath, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("activities", data.get("activityList", []))
        runs = []
        for item in data:
            run = self._parse_json_activity(item)
            if run:
                runs.append(run)
        return runs

    def _parse_json_activity(self, data: Dict[str, Any]) -> Optional[NormalizedRun]:
        sport    = data.get("activityType", {})
        type_key = (sport.get("typeKey", "") if isinstance(sport, dict) else str(sport)).lower().strip()
        activity_type = RUN_TYPES.get(type_key)
        if not activity_type:
            return None
        try:
            date_raw = data.get("startTimeLocal") or data.get("startTimeGMT")
            date     = self._parse_date(date_raw)
            dist_km  = float(data.get("distance", 0)) / 1000.0
            dur_min  = float(data.get("duration", 0)) / 60.0
            if dist_km <= 0 or dur_min <= 0:
                return None
            avg_hr   = data.get("averageHR") or data.get("avgHr")
            max_hr   = data.get("maxHR") or data.get("maxHr")
            elevation= data.get("elevationGain")
            cadence  = data.get("averageRunningCadenceInStepsPerMinute")
            pace     = (dur_min / dist_km) if dist_km > 0 else None
            return NormalizedRun(
                date=date, activity_type=activity_type,
                distance_km=dist_km, duration_minutes=dur_min,
                avg_pace_min_per_km=round(pace, 3) if pace else None,
                avg_hr=float(avg_hr) if avg_hr else None,
                max_hr=float(max_hr) if max_hr else None,
                elevation_gain_m=float(elevation) if elevation else None,
                cadence=int(cadence) if cadence else None,
                source="garmin_json",
                external_id=str(data.get("activityId", "")),
            )
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Value parsers
    # ------------------------------------------------------------------

    def _parse_date(self, s: Any) -> datetime:
        if isinstance(s, datetime):
            return s
        s = str(s).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                    "%d-%m-%Y %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[:19], fmt)
            except ValueError:
                pass
        raise ValueError(f"Cannot parse date: {s!r}")

    def _duration(self, s: str) -> Optional[float]:
        """Parse hh:mm:ss or hh:mm:ss.d → minutes."""
        s = s.strip()
        if not s or s == "--":
            return None
        # Strip sub-second part
        s = s.split(".")[0]
        parts = s.split(":")
        try:
            if len(parts) == 3:
                return int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60
            elif len(parts) == 2:
                return int(parts[0]) + int(parts[1]) / 60
        except ValueError:
            pass
        return None

    def _pace(self, s: str) -> Optional[float]:
        """Parse mm:ss pace string → float min/km. Garmin stores pace in 'Avg Speed'."""
        s = s.strip()
        if not s or s == "--":
            return None
        parts = s.split(":")
        if len(parts) == 2:
            try:
                mins = int(parts[0])
                secs = int(parts[1])
                # Sanity check: a valid running pace is 3:00–15:00 min/km
                val = mins + secs / 60
                if 3.0 <= val <= 15.0:
                    return val
            except ValueError:
                pass
        return None

    def _float(self, s: str) -> Optional[float]:
        s = str(s).strip().replace(",", "")
        if not s or s == "--":
            return None
        try:
            return float(s)
        except ValueError:
            return None

    def _int(self, s: str) -> Optional[int]:
        v = self._float(s)
        return int(v) if v is not None else None

    def describe(self, runs: List[NormalizedRun]) -> str:
        if not runs:
            return "No runs found."
        total_km = sum(r.distance_km for r in runs)
        with_hr  = sum(1 for r in runs if r.avg_hr)
        with_pace= sum(1 for r in runs if r.avg_pace_min_per_km)
        return (
            f"  {len(runs)} runs imported\n"
            f"  Date range : {runs[0].date.date()} → {runs[-1].date.date()}\n"
            f"  Total km   : {total_km:.1f} km\n"
            f"  With HR    : {with_hr}/{len(runs)} runs\n"
            f"  With pace  : {with_pace}/{len(runs)} runs"
        )
