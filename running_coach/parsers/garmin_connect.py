"""
Garmin Connect online parser.

Preferred deployment mode for this personal app:
  - create a Garth session locally with garth.save(...)
  - upload oauth1_token.json and oauth2_token.json as Render Secret Files
  - Render exposes them at /etc/secrets/oauth1_token.json and /etc/secrets/oauth2_token.json

This avoids storing your Garmin password in Render or Supabase.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..schemas import ActivityType, NormalizedRun


RUN_TYPES = {
    "running": ActivityType.OUTDOOR_RUN,
    "run": ActivityType.OUTDOOR_RUN,
    "street_running": ActivityType.OUTDOOR_RUN,
    "track_running": ActivityType.OUTDOOR_RUN,
    "trail_running": ActivityType.TRAIL_RUN,
    "treadmill_running": ActivityType.TREADMILL_RUN,
    "indoor_running": ActivityType.TREADMILL_RUN,
    "virtual_run": ActivityType.TREADMILL_RUN,
}

DEFAULT_GARTH_SESSION_DIR = os.environ.get("GARTH_SESSION_DIR", "/etc/secrets")


def garth_secret_files_exist(session_dir: str = DEFAULT_GARTH_SESSION_DIR) -> bool:
    """True when Render Secret Files contain the two Garth token files."""
    return (
        os.path.exists(os.path.join(session_dir, "oauth1_token.json"))
        and os.path.exists(os.path.join(session_dir, "oauth2_token.json"))
    )


class GarminConnectParser:
    """Fetches activities from Garmin Connect and converts runs to NormalizedRun."""

    def __init__(self, email: str | None = None, password: str | None = None,
                 session_dir: str = DEFAULT_GARTH_SESSION_DIR):
        self.email = email
        self.password = password
        self.session_dir = session_dir

    def fetch_runs(self, max_runs: int = 200) -> List[NormalizedRun]:
        if garth_secret_files_exist(self.session_dir):
            activities = self._fetch_with_garth(max_runs)
        elif self.email and self.password:
            activities = self._fetch_with_garminconnect(max_runs)
        else:
            raise RuntimeError(
                "No Garmin auth found. Upload oauth1_token.json and oauth2_token.json as Render Secret Files, "
                "or set GARMIN_EMAIL/GARMIN_PASSWORD as a fallback."
            )

        runs: List[NormalizedRun] = []
        for item in activities or []:
            run = self._parse_activity(item)
            if run:
                runs.append(run)

        runs.sort(key=lambda r: r.date)
        return runs

    def _fetch_with_garth(self, max_runs: int) -> List[Dict[str, Any]]:
        try:
            import garth
        except ImportError as exc:
            raise RuntimeError("Missing dependency: garth. Add it to requirements.txt and redeploy.") from exc

        try:
            garth.resume(self.session_dir)
        except Exception as exc:
            raise RuntimeError(
                f"Could not load Garmin session from {self.session_dir}. "
                "Make sure Render Secret Files are named oauth1_token.json and oauth2_token.json. "
                "If the session expired, regenerate the files locally and replace them in Render."
            ) from exc

        path = f"/activitylist-service/activities/search/activities?start=0&limit={int(max_runs)}"
        try:
            return garth.connectapi(path) or []
        except Exception as exc:
            raise RuntimeError(
                "Could not fetch Garmin activities using the saved Garth session. "
                "The session may have expired; regenerate oauth1_token.json and oauth2_token.json."
            ) from exc

    def _fetch_with_garminconnect(self, max_runs: int) -> List[Dict[str, Any]]:
        try:
            from garminconnect import Garmin
        except ImportError as exc:
            raise RuntimeError("Missing dependency: garminconnect. Add it to requirements.txt and redeploy.") from exc

        client = Garmin(self.email, self.password)
        try:
            client.login()
        except Exception as exc:
            raise RuntimeError("Garmin login failed. Check Garmin credentials or use Render Secret Files.") from exc

        try:
            return client.get_activities(0, max_runs) or []
        except Exception as exc:
            raise RuntimeError(f"Could not fetch Garmin activities: {exc}") from exc

    def _parse_activity(self, data: Dict[str, Any]) -> Optional[NormalizedRun]:
        type_key = self._activity_type_key(data)
        activity_type = RUN_TYPES.get(type_key)
        if activity_type is None:
            return None

        try:
            date = self._parse_date(data.get("startTimeLocal") or data.get("startTimeGMT"))
            distance_km = self._number(data.get("distance"), 0.0) / 1000.0
            duration_min = self._number(data.get("duration"), 0.0) / 60.0
            if distance_km <= 0 or duration_min <= 0:
                return None

            avg_hr = self._optional_number(data.get("averageHR") or data.get("avgHr"))
            max_hr = self._optional_number(data.get("maxHR") or data.get("maxHr"))
            elevation = self._optional_number(data.get("elevationGain") or data.get("totalAscent"))
            cadence = self._optional_int(
                data.get("averageRunningCadenceInStepsPerMinute")
                or data.get("averageRunCadence")
                or data.get("avgRunCadence")
            )
            pace = duration_min / distance_km if distance_km > 0 else None

            return NormalizedRun(
                date=date,
                activity_type=activity_type,
                distance_km=round(distance_km, 3),
                duration_minutes=round(duration_min, 3),
                avg_pace_min_per_km=round(pace, 3) if pace else None,
                avg_hr=avg_hr,
                max_hr=max_hr,
                elevation_gain_m=elevation,
                cadence=cadence,
                source="garmin_connect",
                external_id=str(data.get("activityId", "")),
            )
        except Exception:
            return None

    def _activity_type_key(self, data: Dict[str, Any]) -> str:
        activity_type = data.get("activityType")
        if isinstance(activity_type, dict):
            value = activity_type.get("typeKey") or activity_type.get("typeId") or ""
        else:
            value = activity_type or data.get("activityTypeDTO", "")
        return str(value).strip().lower()

    def _parse_date(self, value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        text = str(value or "").strip().replace("Z", "")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(text[:19], fmt)
            except ValueError:
                continue
        raise ValueError(f"Cannot parse Garmin date: {value!r}")

    def _number(self, value: Any, default: float) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _optional_number(self, value: Any) -> Optional[float]:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    def _optional_int(self, value: Any) -> Optional[int]:
        num = self._optional_number(value)
        return int(num) if num is not None else None
