"""
Garmin Connect parser — fetches activities and daily health metrics.

Production mode: saved garth session from Render Secret Files
  /etc/secrets/oauth1_token.json
  /etc/secrets/oauth2_token.json

The garth session is resumed ONCE at module import time and reused for
all subsequent calls in the same process. This is critical — calling
garth.resume() repeatedly (or using garminconnect.login()) triggers
the OAuth exchange endpoint, which Garmin rate-limits aggressively (429).

Local fallback: GARMIN_EMAIL + GARMIN_PASSWORD env vars (not recommended
for production — login() is called on every cold start).
"""

from __future__ import annotations

import os
import time
from datetime import date as date_cls, datetime
from typing import Any, Dict, List, Optional

from ..schemas import ActivityType, NormalizedRun

DEFAULT_SESSION_DIR = os.environ.get("GARTH_SESSION_DIR", "/etc/secrets")

RUN_TYPES = {
    "running":          ActivityType.OUTDOOR_RUN,
    "run":              ActivityType.OUTDOOR_RUN,
    "street_running":   ActivityType.OUTDOOR_RUN,
    "track_running":    ActivityType.OUTDOOR_RUN,
    "trail_running":    ActivityType.TRAIL_RUN,
    "treadmill_running":ActivityType.TREADMILL_RUN,
    "indoor_running":   ActivityType.TREADMILL_RUN,
    "virtual_run":      ActivityType.TREADMILL_RUN,
}

# ── Module-level garth session (resumed once per process) ─────────────────────
_garth_session_loaded = False
_garth_session_error:  Optional[str] = None

def _ensure_garth(session_dir: str = DEFAULT_SESSION_DIR) -> None:
    """
    Resume the garth session exactly once per process lifetime.
    Subsequent calls are no-ops. Raises RuntimeError if files are missing
    or the session is invalid.
    """
    global _garth_session_loaded, _garth_session_error

    if _garth_session_loaded:
        return
    if _garth_session_error:
        raise RuntimeError(_garth_session_error)

    try:
        import garth  # noqa: F401 — will raise ImportError if not installed
    except ImportError:
        _garth_session_error = (
            "garth is not installed. Add 'garth>=0.5.0' to requirements.txt and redeploy."
        )
        raise RuntimeError(_garth_session_error)

    token1 = os.path.join(session_dir, "oauth1_token.json")
    token2 = os.path.join(session_dir, "oauth2_token.json")
    if not (os.path.exists(token1) and os.path.exists(token2)):
        _garth_session_error = (
            f"Garmin session files not found in {session_dir}. "
            "Generate them locally with 'python -c \"import garth; garth.login(); garth.save(\\\"~/.garth\\\")\"' "
            "then upload oauth1_token.json and oauth2_token.json as Render Secret Files."
        )
        raise RuntimeError(_garth_session_error)

    import garth
    for attempt in range(3):
        try:
            garth.resume(session_dir)
            _garth_session_loaded = True
            print(f"[GARMIN] garth session resumed from {session_dir}", flush=True)
            return
        except Exception as exc:
            msg = str(exc)
            if "429" in msg and attempt < 2:
                wait = 60 * (attempt + 1)  # 60s, 120s
                print(f"[GARMIN] 429 on session resume — waiting {wait}s (attempt {attempt+1}/3)", flush=True)
                import time; time.sleep(wait)
            else:
                _garth_session_error = (
                    f"Could not load Garmin session: {exc}. "
                    "Regenerate oauth1_token.json and oauth2_token.json and re-upload to Render Secret Files."
                )
                raise RuntimeError(_garth_session_error) from exc


def secret_session_available(session_dir: str = DEFAULT_SESSION_DIR) -> bool:
    return (
        os.path.exists(os.path.join(session_dir, "oauth1_token.json"))
        and os.path.exists(os.path.join(session_dir, "oauth2_token.json"))
    )


# ── Retry helper ──────────────────────────────────────────────────────────────

def _garth_get(path: str, params: dict = None, retries: int = 2) -> Any:
    """
    Call garth.connectapi with automatic 429 backoff.
    Tries `retries` times before giving up.
    """
    import garth
    last_exc = None
    for attempt in range(retries + 1):
        try:
            if params:
                return garth.connectapi(path, params=params)
            return garth.connectapi(path)
        except Exception as exc:
            msg = str(exc)
            if "429" in msg:
                wait = 120 * (attempt + 1)  # 120s, 240s, 360s
                print(f"[GARMIN] 429 on {path} — waiting {wait}s (attempt {attempt+1}/{retries+1})", flush=True)
                time.sleep(wait)
                last_exc = exc
            else:
                raise
    raise RuntimeError(f"Garmin API rate limit after {retries+1} attempts on {path}") from last_exc


def _garth_get_first(paths: list[str]) -> Any:
    """Try each path in order, return the first non-None result."""
    for path in paths:
        try:
            data = _garth_get(path)
            if data is not None:
                return data
        except Exception:
            pass
    return None


# ── Main parser class ─────────────────────────────────────────────────────────

class GarminConnectParser:
    """
    Fetches activities and daily health metrics from Garmin Connect.

    Preferred: garth session files (no login call, no 429 risk).
    Fallback:  email + password (calls login() on every cold start — use only locally).
    """

    def __init__(
        self,
        email:       Optional[str] = None,
        password:    Optional[str] = None,
        session_dir: str = DEFAULT_SESSION_DIR,
    ):
        self.email       = email or os.environ.get("GARMIN_EMAIL", "")
        self.password    = password or os.environ.get("GARMIN_PASSWORD", "")
        self.session_dir = session_dir
        self._gc_client  = None  # lazily created garminconnect client

    # ── Public API ────────────────────────────────────────────────────────────

    def fetch_runs(self, max_runs: int = 200) -> List[NormalizedRun]:
        activities = self._fetch_activities(max_runs)
        runs = [r for r in (self._parse_activity(a) for a in (activities or [])) if r]
        runs.sort(key=lambda r: r.date)
        return runs

    def fetch_daily_health(self, date=None) -> dict:
        """
        Fetch sleep, HRV, resting HR, body battery, and stress for one day.
        Returns a dict with keys: sleep_hours, sleep_quality, hrv_ms,
        resting_hr, body_battery, stress, source.
        Missing endpoints are handled silently.
        """
        if date is None:
            date = date_cls.today()
        date_str = date.isoformat() if hasattr(date, "isoformat") else str(date)

        health = {
            "date":          date_str,
            "sleep_hours":   None,
            "sleep_quality": None,
            "hrv_ms":        None,
            "resting_hr":    None,
            "body_battery":  None,
            "stress":        None,
            "source":        "garmin_watch",
        }

        if secret_session_available(self.session_dir):
            _ensure_garth(self.session_dir)

            sleep = _garth_get_first([
                f"/wellness-service/wellness/dailySleepData/{date_str}",
                f"/sleep-service/sleep/dailySleepData/{date_str}",
                f"/sleep-service/sleep/{date_str}",
                f"/wellness-service/wellness/sleep/{date_str}",
            ])
            print(f"[HEALTH] sleep raw keys: {list(sleep.keys()) if isinstance(sleep, dict) else type(sleep)}", flush=True)
            self._apply_sleep(health, sleep)
            print(f"[HEALTH] sleep parsed: hours={health.get('sleep_hours')} quality={health.get('sleep_quality')}", flush=True)

            hrv = _garth_get_first([
                f"/hrv-service/hrv/{date_str}",
                f"/wellness-service/wellness/hrv/{date_str}",
            ])
            print(f"[HEALTH] hrv raw keys: {list(hrv.keys()) if isinstance(hrv, dict) else type(hrv)}", flush=True)
            self._apply_hrv(health, hrv)
            print(f"[HEALTH] hrv parsed: {health.get('hrv_ms')}", flush=True)

            rhr = _garth_get_first([
                f"/wellness-service/wellness/dailyHeartRate/{date_str}",
                f"/wellness-service/wellness/rhr/{date_str}",
                f"/userstats-service/wellness/daily/{date_str}",
                f"/wellness-service/wellness/dailySummary/{date_str}",
            ])
            print(f"[HEALTH] rhr raw keys: {list(rhr.keys()) if isinstance(rhr, dict) else type(rhr)}", flush=True)
            self._apply_resting_hr(health, rhr)
            print(f"[HEALTH] rhr parsed: {health.get('resting_hr')}", flush=True)

            bb = _garth_get_first([
                f"/wellness-service/wellness/bodyBattery/reports/daily/{date_str}",
                f"/wellness-service/wellness/bodyBattery/{date_str}",
            ])
            health["body_battery"] = self._extract_numeric(
                bb, ["bodyBatteryValue", "bodyBattery", "value", "charged", "drained"]
            )

            stress = _garth_get_first([
                f"/wellness-service/wellness/dailyStress/{date_str}",
                f"/wellness-service/wellness/stress/{date_str}",
            ])
            self._apply_stress(health, stress)

        else:
            client = self._gc_client_get()
            self._apply_sleep(health,
                self._gc_call(client, ["get_sleep_data", "get_sleep"], date_str))
            self._apply_hrv(health,
                self._gc_call(client, ["get_hrv_data", "get_hrv", "get_hrv_status"], date_str))
            self._apply_resting_hr(health,
                self._gc_call(client, ["get_rhr_day", "get_resting_heart_rate"], date_str))
            bb = self._gc_call(client, ["get_body_battery", "get_body_battery_events"], date_str)
            health["body_battery"] = self._extract_numeric(bb, ["bodyBatteryValue", "value", "bodyBattery"])
            self._apply_stress(health,
                self._gc_call(client, ["get_stress_data", "get_stress"], date_str))

        return health

    # ── Garmin access (internal) ──────────────────────────────────────────────

    def _fetch_activities(self, max_runs: int) -> list:
        if secret_session_available(self.session_dir):
            _ensure_garth(self.session_dir)
            path = "/activitylist-service/activities/search/activities"
            try:
                return _garth_get(path, params={"start": 0, "limit": max_runs}) or []
            except TypeError:
                return _garth_get(f"{path}?start=0&limit={max_runs}") or []
        else:
            client = self._gc_client_get()
            try:
                return client.get_activities(0, max_runs) or []
            except Exception as exc:
                raise RuntimeError(f"Could not fetch Garmin activities: {exc}") from exc

    def _gc_client_get(self):
        """Return a cached garminconnect client (login called once per process)."""
        if self._gc_client is not None:
            return self._gc_client
        if not self.email or not self.password:
            raise RuntimeError(
                "No Garmin credentials available. "
                "Upload oauth1_token.json and oauth2_token.json as Render Secret Files, "
                "or set GARMIN_EMAIL and GARMIN_PASSWORD environment variables."
            )
        try:
            from garminconnect import Garmin
        except ImportError as exc:
            raise RuntimeError(
                "garminconnect is not installed. Add 'garminconnect>=0.2.25' to requirements.txt."
            ) from exc
        client = Garmin(self.email, self.password)
        try:
            client.login()
        except Exception as exc:
            raise RuntimeError(
                "Garmin login failed. For production, use Render Secret Files instead."
            ) from exc
        self._gc_client = client
        return client

    def _gc_call(self, client, method_names: list[str], *args) -> Any:
        """Try each method name on the garminconnect client, return first success."""
        for name in method_names:
            method = getattr(client, name, None)
            if method is None:
                continue
            try:
                result = method(*args)
                if result is not None:
                    return result
            except Exception:
                pass
        return None

    # ── Activity parsing ──────────────────────────────────────────────────────

    def _parse_activity(self, data: Dict[str, Any]) -> Optional[NormalizedRun]:
        type_key = self._activity_type_key(data)
        activity_type = RUN_TYPES.get(type_key)
        if activity_type is None:
            return None
        try:
            date         = self._parse_date(data.get("startTimeLocal") or data.get("startTimeGMT"))
            distance_km  = self._num(data.get("distance"), 0.0) / 1000.0
            duration_min = self._num(data.get("duration"), 0.0) / 60.0
            if distance_km <= 0 or duration_min <= 0:
                return None
            pace = duration_min / distance_km if distance_km > 0 else None
            return NormalizedRun(
                date=date,
                activity_type=activity_type,
                distance_km=round(distance_km, 3),
                duration_minutes=round(duration_min, 3),
                avg_pace_min_per_km=round(pace, 3) if pace else None,
                avg_hr=self._opt(data.get("averageHR") or data.get("avgHr")),
                max_hr=self._opt(data.get("maxHR") or data.get("maxHr")),
                elevation_gain_m=self._opt(data.get("elevationGain") or data.get("totalAscent")),
                cadence=self._opt_int(
                    data.get("averageRunningCadenceInStepsPerMinute")
                    or data.get("averageRunCadence")
                    or data.get("avgRunCadence")
                ),
                source="garmin_connect",
                external_id=str(data.get("activityId", "")),
            )
        except Exception:
            return None

    def _activity_type_key(self, data: Dict[str, Any]) -> str:
        at = data.get("activityType")
        if isinstance(at, dict):
            v = at.get("typeKey") or at.get("typeId") or ""
        else:
            v = at or data.get("activityTypeDTO", "")
        return str(v).strip().lower()

    # ── Health metric extraction ──────────────────────────────────────────────

    def _apply_sleep(self, health: dict, data: Any) -> None:
        if not isinstance(data, dict):
            return
        # Check inside dailySleepDTO if present
        inner = data.get("dailySleepDTO") if isinstance(data.get("dailySleepDTO"), dict) else data
        secs = self._first_num(inner, [
            "sleepTimeSeconds", "totalSleepSeconds", "sleepDurationSeconds",
            "unmeasurableSleepSeconds",
        ])
        # Also sum deep + light + rem if individual stages available
        if secs is None:
            deep  = self._first_num(inner, ["deepSleepSeconds", "deepSleepDuration"]) or 0
            light = self._first_num(inner, ["lightSleepSeconds", "lightSleepDuration"]) or 0
            rem   = self._first_num(inner, ["remSleepSeconds", "remSleepDuration"]) or 0
            total = deep + light + rem
            if total > 0:
                secs = total
        if secs and secs > 0:
            health["sleep_hours"] = round(secs / 3600, 1)
        # Score
        score_data = inner.get("sleepScores") if isinstance(inner.get("sleepScores"), dict) else inner
        score = self._first_num(score_data, ["overallScore", "qualityTypePK", "totalScore"])
        if score is None:
            score = self._first_num(inner, ["sleepScores", "overallScore", "qualityTypePK"])
        if score:
            health["sleep_quality"] = max(1, min(5, round(score / 20)))

    def _apply_hrv(self, health: dict, data: Any) -> None:
        if not isinstance(data, dict):
            return
        # Try top-level fields first
        hrv = self._first_num(data, [
            "lastNight", "hrvValue", "rmssd", "averageHRV", "weeklyAvg",
        ])
        # Garmin API returns hrvSummary as a nested dict
        if hrv is None and isinstance(data.get("hrvSummary"), dict):
            hrv = self._first_num(data["hrvSummary"], [
                "lastNight", "hrvValue", "rmssd", "averageHRV", "weeklyAvg",
                "lastNight5MinHigh", "baseline", "balancedLow", "balancedUpper",
            ])
        # Also check hrvReadings list for average
        if hrv is None and isinstance(data.get("hrvReadings"), list):
            readings = data["hrvReadings"]
            vals = []
            for r in readings:
                if isinstance(r, dict):
                    v = self._first_num(r, ["hrvValue", "rmssd", "value"])
                    if v:
                        vals.append(v)
            if vals:
                hrv = round(sum(vals) / len(vals), 1)
        if hrv:
            health["hrv_ms"] = round(hrv, 1)

    def _apply_resting_hr(self, health: dict, data: Any) -> None:
        if not isinstance(data, dict):
            return
        rhr = self._first_num(data, [
            "restingHeartRate", "rhr", "averageRestingHeartRate",
            "restingHeartRateValue", "minHeartRate", "lowestHeartRate",
            "calendarDate",  # skip this one — just checking structure
        ])
        # Also check inside heartRateValues list
        if rhr is None and isinstance(data.get("heartRateValues"), list):
            vals = [v[1] for v in data["heartRateValues"] if isinstance(v, list) and len(v) > 1 and v[1]]
            if vals:
                rhr = min(vals)  # resting HR is typically the minimum
        if rhr and rhr > 20:  # sanity check
            health["resting_hr"] = int(rhr)

    def _apply_stress(self, health: dict, data: Any) -> None:
        if not isinstance(data, dict):
            return
        stress = self._first_num(data, [
            "avgStressLevel", "averageStressLevel", "stressLevel",
            "overallStressLevel",
        ])
        if stress:
            health["stress"] = int(stress)

    def _extract_numeric(self, data: Any, keys: list[str]) -> Optional[float]:
        if isinstance(data, (int, float)):
            return float(data)
        if isinstance(data, list) and data:
            data = data[-1]
        return self._first_num(data, keys) if isinstance(data, dict) else None

    def _first_num(self, data: dict, keys: list[str]) -> Optional[float]:
        for k in keys:
            v = data.get(k)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None

    # ── Primitive helpers ─────────────────────────────────────────────────────

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

    def _num(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _opt(self, value: Any) -> Optional[float]:
        try:
            return float(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None

    def _opt_int(self, value: Any) -> Optional[int]:
        v = self._opt(value)
        return int(v) if v is not None else None
