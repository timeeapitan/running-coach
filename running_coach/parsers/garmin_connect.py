"""
Garmin Connect online parser.

This uses the unofficial `garminconnect` Python package to log in to your
Garmin Connect account and fetch recent activities. It is intended for personal
use, not for a public multi-user app.
"""

from __future__ import annotations

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


class GarminConnectParser:
    """Fetches activities from Garmin Connect and converts runs to NormalizedRun."""

    def __init__(self, email: str, password: str):
        self.email = email
        self.password = password

    def fetch_runs(self, max_runs: int = 200) -> List[NormalizedRun]:
        try:
            from garminconnect import Garmin
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: garminconnect. Add it to requirements.txt and redeploy."
            ) from exc

        client = Garmin(self.email, self.password)
        try:
            client.login()
        except Exception as exc:
            raise RuntimeError(
                "Garmin login failed. Check GARMIN_EMAIL/GARMIN_PASSWORD or the credentials entered in the app. "
                "If Garmin asks for MFA/2FA, log in once in the browser and try again, or use app credentials without MFA."
            ) from exc

        try:
            activities = client.get_activities(0, max_runs)
        except Exception as exc:
            raise RuntimeError(f"Could not fetch Garmin activities: {exc}") from exc

        runs: List[NormalizedRun] = []
        for item in activities or []:
            run = self._parse_activity(item)
            if run:
                runs.append(run)

        runs.sort(key=lambda r: r.date)
        return runs



    def fetch_daily_health(self, date=None) -> dict:
        """Fetch sleep/HRV/body-battery style metrics for one day.

        The unofficial Garmin package has changed method names across versions, so
        this method is intentionally defensive. Missing metrics simply return None.
        """
        from datetime import date as date_cls
        if date is None:
            date = date_cls.today()
        date_str = date.isoformat() if hasattr(date, 'isoformat') else str(date)

        client = self._login_client()
        health = {
            "date": date_str,
            "sleep_hours": None,
            "sleep_quality": None,
            "hrv_ms": None,
            "resting_hr": None,
            "body_battery": None,
            "stress": None,
            "source": "watch",
        }

        # Sleep
        sleep = self._call_first(client, ["get_sleep_data", "get_sleep"], date_str)
        if isinstance(sleep, dict):
            secs = self._first_number(sleep, [
                "sleepTimeSeconds", "totalSleepSeconds", "sleepDurationSeconds",
                "durationInSeconds", "totalSleepDuration"
            ])
            mins = self._first_number(sleep, ["sleepTimeMinutes", "totalSleepMinutes", "durationInMinutes"])
            hours = self._first_number(sleep, ["sleepHours", "totalSleepHours"])
            if secs:
                health["sleep_hours"] = round(secs / 3600, 2)
            elif mins:
                health["sleep_hours"] = round(mins / 60, 2)
            elif hours:
                health["sleep_hours"] = round(hours, 2)

            score = self._first_number(sleep, ["sleepScore", "overallSleepScore", "sleepScores"])
            if isinstance(sleep.get("sleepScores"), dict):
                score = self._first_number(sleep["sleepScores"], ["overall", "total", "sleepScore"])
            if score is not None:
                # Convert Garmin 0-100 score to the existing 1-5 app scale.
                health["sleep_quality"] = max(1, min(5, round(score / 20)))

        # HRV
        hrv = self._call_first(client, ["get_hrv_data", "get_hrv", "get_hrv_status"], date_str)
        if isinstance(hrv, dict):
            val = self._first_number(hrv, ["lastNightAvg", "weeklyAvg", "avgHrv", "average", "hrvValue", "value"])
            if val is None and isinstance(hrv.get("hrvSummary"), dict):
                val = self._first_number(hrv["hrvSummary"], ["lastNightAvg", "weeklyAvg", "average"])
            health["hrv_ms"] = val

        # Resting HR
        rhr = self._call_first(client, ["get_rhr_day", "get_resting_heart_rate"], date_str)
        if isinstance(rhr, dict):
            health["resting_hr"] = self._first_number(rhr, ["restingHeartRate", "value", "restingHR"])
        elif isinstance(rhr, (int, float)):
            health["resting_hr"] = float(rhr)

        # Body Battery / stress if available
        bb = self._call_first(client, ["get_body_battery", "get_body_battery_events"], date_str)
        health["body_battery"] = self._extract_latest_numeric(bb, ["bodyBatteryValue", "value", "bodyBattery"])

        stress = self._call_first(client, ["get_stress_data", "get_stress"], date_str)
        if isinstance(stress, dict):
            health["stress"] = self._first_number(stress, ["avgStressLevel", "averageStressLevel", "stressLevel", "value"])

        return health

    def _login_client(self):
        try:
            from garminconnect import Garmin
        except ImportError as exc:
            raise RuntimeError(
                "Missing dependency: garminconnect. Add it to requirements.txt and redeploy."
            ) from exc
        client = Garmin(self.email, self.password)
        client.login()
        return client

    def _call_first(self, client, names, *args):
        for name in names:
            method = getattr(client, name, None)
            if not method:
                continue
            try:
                return method(*args)
            except TypeError:
                try:
                    return method()
                except Exception:
                    continue
            except Exception:
                continue
        return None

    def _first_number(self, data, keys):
        for key in keys:
            value = data.get(key) if isinstance(data, dict) else None
            if isinstance(value, dict):
                value = self._first_number(value, ["value", "amount", "score", "overall"])
            try:
                if value is not None and value != "":
                    return float(value)
            except (TypeError, ValueError):
                pass
        return None

    def _extract_latest_numeric(self, data, keys):
        if isinstance(data, dict):
            direct = self._first_number(data, keys)
            if direct is not None:
                return direct
            for value in data.values():
                found = self._extract_latest_numeric(value, keys)
                if found is not None:
                    return found
        if isinstance(data, list):
            for item in reversed(data):
                found = self._extract_latest_numeric(item, keys)
                if found is not None:
                    return found
        return None

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
