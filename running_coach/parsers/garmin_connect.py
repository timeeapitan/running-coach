"""
Garmin Connect online parser for the personal Running Coach app.

Production mode uses a saved `garth` session from Render Secret Files:
  /etc/secrets/oauth1_token.json
  /etc/secrets/oauth2_token.json

This avoids calling Garmin's login endpoint on every sync, which is what causes
429 / IP rate-limit errors. Email/password login is kept only as a local/manual
fallback, not as the normal deployed path.
"""

from __future__ import annotations

import os
from datetime import datetime, date as date_cls
from typing import Any, Dict, List, Optional

from ..schemas import ActivityType, NormalizedRun


RUN_TYPES = {
    "running": ActivityType.OUTDOOR_RUN,
    "run": ActivityType.OUTDOOR_RUN,
    "street_running": ActivityType.OUTDOOR_RUN,
    "road_running": ActivityType.OUTDOOR_RUN,
    "track_running": ActivityType.OUTDOOR_RUN,
    "generic_running": ActivityType.OUTDOOR_RUN,
    "trail_running": ActivityType.TRAIL_RUN,
    "treadmill_running": ActivityType.TREADMILL_RUN,
    "indoor_running": ActivityType.TREADMILL_RUN,
    "virtual_run": ActivityType.TREADMILL_RUN,
}


class GarminConnectParser:
    """Fetches Garmin activities/health data and converts runs to NormalizedRun."""

    def __init__(self, email: str | None = None, password: str | None = None,
                 session_dir: str | None = None):
        self.email = email
        self.password = password
        self.session_dir = session_dir or os.environ.get("GARTH_SESSION_DIR", "/etc/secrets")
        self._garth_ready = False
        self._gc_client = None

    @classmethod
    def from_secret_session(cls, session_dir: str | None = None) -> "GarminConnectParser":
        return cls(session_dir=session_dir or os.environ.get("GARTH_SESSION_DIR", "/etc/secrets"))

    @staticmethod
    def secret_session_available(session_dir: str | None = None) -> bool:
        session_dir = session_dir or os.environ.get("GARTH_SESSION_DIR", "/etc/secrets")
        return (
            os.path.exists(os.path.join(session_dir, "oauth1_token.json"))
            and os.path.exists(os.path.join(session_dir, "oauth2_token.json"))
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_runs(self, max_runs: int = 200) -> List[NormalizedRun]:
        activities = self._fetch_activities(max_runs)
        print(f"[GARMIN] raw activities returned: {len(activities or [])}", flush=True)

        runs: List[NormalizedRun] = []
        skipped_types: dict[str, int] = {}
        parse_errors = 0

        for item in activities or []:
            type_key = self._activity_type_key(item if isinstance(item, dict) else {})
            run = self._parse_activity(item) if isinstance(item, dict) else None
            if run:
                runs.append(run)
            else:
                skipped_types[type_key or "unknown"] = skipped_types.get(type_key or "unknown", 0) + 1

        runs.sort(key=lambda r: r.date)
        print(f"[GARMIN] parsed runs: {len(runs)}", flush=True)
        if skipped_types:
            print(f"[GARMIN] skipped activity types/sample: {dict(list(skipped_types.items())[:8])}", flush=True)
        return runs

    def fetch_daily_health(self, date=None) -> dict:
        """Fetch sleep/HRV/body-battery style metrics for one day.

        Missing endpoints/metrics are handled safely. This method never performs
        a fresh Garmin login when secret session files are available.
        """
        if date is None:
            date = date_cls.today()
        date_str = date.isoformat() if hasattr(date, "isoformat") else str(date)

        health = {
            "date": date_str,
            "sleep_hours": None,
            "sleep_quality": None,
            "hrv_ms": None,
            "resting_hr": None,
            "body_battery": None,
            "stress": None,
            "source": "garmin_watch",
        }

        # Prefer garth session mode.
        if self.secret_session_available(self.session_dir):
            self._resume_garth()
            sleep = self._garth_get_first([
                f"/wellness-service/wellness/dailySleepData/{date_str}",
                f"/sleep-service/sleep/dailySleepData/{date_str}",
            ])
            self._apply_sleep(health, sleep)

            hrv = self._garth_get_first([
                f"/hrv-service/hrv/{date_str}",
                f"/wellness-service/wellness/hrv/{date_str}",
            ])
            self._apply_hrv(health, hrv)

            rhr = self._garth_get_first([
                f"/wellness-service/wellness/dailyHeartRate/{date_str}",
                f"/wellness-service/wellness/rhr/{date_str}",
            ])
            self._apply_resting_hr(health, rhr)

            bb = self._garth_get_first([
                f"/wellness-service/wellness/bodyBattery/reports/daily/{date_str}",
                f"/wellness-service/wellness/bodyBattery/{date_str}",
            ])
            health["body_battery"] = self._extract_latest_numeric(
                bb, ["bodyBatteryValue", "bodyBattery", "value", "charged", "drained"]
            )

            stress = self._garth_get_first([
                f"/wellness-service/wellness/dailyStress/{date_str}",
                f"/wellness-service/wellness/stress/{date_str}",
            ])
            self._apply_stress(health, stress)
            return health

        # Local/manual fallback using garminconnect credentials.
        client = self._login_garminconnect_client()
        sleep = self._call_first(client, ["get_sleep_data", "get_sleep"], date_str)
        self._apply_sleep(health, sleep)
        hrv = self._call_first(client, ["get_hrv_data", "get_hrv", "get_hrv_status"], date_str)
        self._apply_hrv(health, hrv)
        rhr = self._call_first(client, ["get_rhr_day", "get_resting_heart_rate"], date_str)
        self._apply_resting_hr(health, rhr)
        bb = self._call_first(client, ["get_body_battery", "get_body_battery_events"], date_str)
        health["body_battery"] = self._extract_latest_numeric(bb, ["bodyBatteryValue", "value", "bodyBattery"])
        stress = self._call_first(client, ["get_stress_data", "get_stress"], date_str)
        self._apply_stress(health, stress)
        return health

    # ── Garmin access ─────────────────────────────────────────────────────────

    def _resume_garth(self) -> None:
        if self._garth_ready:
            return
        try:
            import garth
        except ImportError as exc:
            raise RuntimeError("Missing dependency: garth. Add garth to requirements.txt and redeploy.") from exc
        try:
            garth.resume(self.session_dir)
            self._garth_ready = True
        except Exception as exc:
            raise RuntimeError(
                f"Could not resume Garmin session from {self.session_dir}. "
                "Regenerate oauth1_token.json and oauth2_token.json and upload them as Render Secret Files."
            ) from exc

    def _garth_get_first(self, paths: list[str]) -> Any:
        import garth
        for path in paths:
            try:
                data = garth.connectapi(path)
                if data is not None:
                    return data
            except Exception:
                continue
        return None

    def _fetch_activities(self, max_runs: int) -> list:
        if self.secret_session_available(self.session_dir):
            self._resume_garth()
            import garth
            # Garmin activity list endpoint used by Garmin Connect web.
            path = "/activitylist-service/activities/search/activities"
            try:
                return garth.connectapi(path, params={"start": 0, "limit": max_runs}) or []
            except TypeError:
                return garth.connectapi(f"{path}?start=0&limit={max_runs}") or []

        client = self._login_garminconnect_client()
        try:
            return client.get_activities(0, max_runs) or []
        except Exception as exc:
            raise RuntimeError(f"Could not fetch Garmin activities: {exc}") from exc

    def _login_garminconnect_client(self):
        if self._gc_client is not None:
            return self._gc_client
        if not self.email or not self.password:
            raise RuntimeError(
                "Missing Garmin session. Upload oauth1_token.json and oauth2_token.json as Render Secret Files."
            )
        try:
            from garminconnect import Garmin
        except ImportError as exc:
            raise RuntimeError("Missing dependency: garminconnect. Add it to requirements.txt and redeploy.") from exc
        client = Garmin(self.email, self.password)
        try:
            client.login()
        except Exception as exc:
            raise RuntimeError(
                "Garmin login failed. In production, use Render Secret Files instead of email/password login."
            ) from exc
        self._gc_client = client
        return client

    # ── Health extraction helpers ─────────────────────────────────────────────

    def _apply_sleep(self, health: dict, sleep: Any) -> None:
        if not isinstance(sleep, dict):
            return
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

        score = self._first_number(sleep, ["sleepScore", "overallSleepScore", "score"])
        if isinstance(sleep.get("sleepScores"), dict):
            score = self._first_number(sleep["sleepScores"], ["overall", "total", "sleepScore", "value"])
        if score is not None:
            health["sleep_quality"] = max(1, min(5, round(score / 20)))

    def _apply_hrv(self, health: dict, hrv: Any) -> None:
        if isinstance(hrv, dict):
            val = self._first_number(hrv, ["lastNightAvg", "weeklyAvg", "avgHrv", "average", "hrvValue", "value"])
            if val is None and isinstance(hrv.get("hrvSummary"), dict):
                val = self._first_number(hrv["hrvSummary"], ["lastNightAvg", "weeklyAvg", "average"])
            health["hrv_ms"] = val

    def _apply_resting_hr(self, health: dict, rhr: Any) -> None:
        if isinstance(rhr, dict):
            health["resting_hr"] = self._first_number(rhr, [
                "restingHeartRate", "value", "restingHR", "restingHeartRateThisTimestamp"
            ])
            if health["resting_hr"] is None:
                health["resting_hr"] = self._extract_latest_numeric(rhr, ["restingHeartRate", "value"])
        elif isinstance(rhr, (int, float)):
            health["resting_hr"] = float(rhr)

    def _apply_stress(self, health: dict, stress: Any) -> None:
        if isinstance(stress, dict):
            health["stress"] = self._first_number(stress, [
                "avgStressLevel", "averageStressLevel", "stressLevel", "value", "maxStressLevel"
            ])
            if health["stress"] is None:
                health["stress"] = self._extract_latest_numeric(stress, ["stressLevel", "value"])

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

    # ── Activity parsing ──────────────────────────────────────────────────────

    def _parse_activity(self, data: Dict[str, Any]) -> Optional[NormalizedRun]:
        type_key = self._activity_type_key(data)
        activity_type = RUN_TYPES.get(type_key)
        if activity_type is None:
            return None

        try:
            date = self._parse_date(
                data.get("startTimeLocal")
                or data.get("startTimeGMT")
                or data.get("beginTimestamp")
                or data.get("startTime")
            )
            distance_m = self._number(
                data.get("distance")
                or data.get("distanceInMeters")
                or data.get("activityDistance")
                or data.get("sumDistance"),
                0.0,
            )
            duration_s = self._number(
                data.get("duration")
                or data.get("durationInSeconds")
                or data.get("elapsedDuration")
                or data.get("movingDuration"),
                0.0,
            )
            distance_km = distance_m / 1000.0
            duration_min = duration_s / 60.0
            if distance_km <= 0 or duration_min <= 0:
                return None

            avg_hr = self._optional_number(data.get("averageHR") or data.get("avgHr") or data.get("averageHeartRate"))
            max_hr = self._optional_number(data.get("maxHR") or data.get("maxHr") or data.get("maxHeartRate"))
            elevation = self._optional_number(data.get("elevationGain") or data.get("totalAscent") or data.get("elevationGainInMeters"))
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

    def _number(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _optional_number(self, value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _optional_int(self, value: Any) -> Optional[int]:
        try:
            return int(round(float(value))) if value is not None else None
        except (TypeError, ValueError):
            return None
