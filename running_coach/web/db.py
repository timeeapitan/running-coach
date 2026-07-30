"""Clean database layer for Running Coach v2.

Garmin-only architecture:
- users: one row per app user, with JSONB profile/activity_token/settings
- runs_cache: one row per Garmin running activity
- daily_cache: one row per user/day with watch metrics + coach summary JSON
- feedback: optional manual notes/check-ins
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, date
from typing import Any, Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
USE_DB = bool(SUPABASE_URL and SUPABASE_KEY)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_data")


def _json(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default
    return value


def _sb_request(method: str, table: str, data: Any = None, query: str = "", prefer: str = "return=representation"):
    if not USE_DB:
        raise RuntimeError("Supabase is not configured")
    url = f"{SUPABASE_URL}/rest/v1/{table}{query}"
    headers = {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": prefer,
    }
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Supabase {method} {table}: {e.code} {detail}")


def _sb_get(table: str, query: str):
    return _sb_request("GET", table, query=query)


def _sb_insert(table: str, data: Any):
    return _sb_request("POST", table, data=data)


def _sb_patch(table: str, data: dict, query: str):
    return _sb_request("PATCH", table, data=data, query=query)


def _sb_delete(table: str, query: str):
    return _sb_request("DELETE", table, query=query)


def _sb_upsert(table: str, data: Any, on_conflict: str):
    query = "?on_conflict=" + urllib.parse.quote(on_conflict)
    return _sb_request(
        "POST",
        table,
        data=data,
        query=query,
        prefer="resolution=merge-duplicates,return=representation",
    )


def _udir(username: str) -> str:
    safe = "".join(c for c in str(username).lower() if c.isalnum() or c in "_-.")
    d = os.path.join(DATA_DIR, safe)
    os.makedirs(d, exist_ok=True)
    return d


# ── Users / profile ──────────────────────────────────────────────────────────

def load_profile(username: str) -> Optional[dict]:
    if not USE_DB:
        p = os.path.join(_udir(username), "profile.json")
        return json.load(open(p)) if os.path.exists(p) else None
    rows = _sb_get("users", f"?username=eq.{urllib.parse.quote(str(username))}&select=profile,display_name")
    if not rows:
        return None
    profile = _json(rows[0].get("profile"), {}) or {}
    if rows[0].get("display_name") and not profile.get("name"):
        profile["name"] = rows[0]["display_name"]
    return profile


def save_profile(username: str, profile_dict: dict) -> None:
    if not USE_DB:
        with open(os.path.join(_udir(username), "profile.json"), "w") as f:
            json.dump(profile_dict, f, indent=2)
        return
    payload = {
        "username": username,
        "provider": "garmin",
        "profile": profile_dict,
        "display_name": profile_dict.get("name") or username,
        "updated_at": datetime.utcnow().isoformat(),
    }
    _sb_upsert("users", payload, "username")


def load_activity_token(username: str) -> Optional[dict]:
    if not USE_DB:
        p = os.path.join(_udir(username), "activity_token.json")
        return json.load(open(p)) if os.path.exists(p) else None
    rows = _sb_get("users", f"?username=eq.{urllib.parse.quote(str(username))}&select=activity_token")
    if not rows:
        return None
    return _json(rows[0].get("activity_token"), None)


def save_activity_token(username: str, token: dict, display_name: str = "", athlete_data: dict | None = None) -> None:
    if not USE_DB:
        with open(os.path.join(_udir(username), "activity_token.json"), "w") as f:
            json.dump(token, f, indent=2)
        return
    payload = {
        "username": username,
        "provider": "garmin",
        "activity_token": token,
        "display_name": (display_name or username).strip(),
        "avatar_url": "",
        "updated_at": datetime.utcnow().isoformat(),
    }
    _sb_upsert("users", payload, "username")


def has_activity_provider(username: str) -> bool:
    return load_activity_token(username) is not None


def load_athlete_info(username: str) -> dict:
    if not USE_DB:
        profile = load_profile(username) or {}
        return {"name": profile.get("name", username), "avatar": ""}
    rows = _sb_get("users", f"?username=eq.{urllib.parse.quote(str(username))}&select=display_name,avatar_url")
    if not rows:
        return {"name": username, "avatar": ""}
    return {"name": rows[0].get("display_name") or username, "avatar": rows[0].get("avatar_url") or ""}


def list_users() -> list:
    if not USE_DB:
        if not os.path.exists(DATA_DIR):
            return []
        out = []
        for u in os.listdir(DATA_DIR):
            p = load_profile(u) or {}
            out.append({"username": u, "name": p.get("name", u), "avatar": ""})
        return out
    rows = _sb_get("users", "?select=username,display_name,avatar_url&order=updated_at.desc")
    return [{"username": r["username"], "name": r.get("display_name") or r["username"], "avatar": r.get("avatar_url") or ""} for r in rows]


# ── Feedback / manual notes ──────────────────────────────────────────────────

def load_feedback(username: str) -> dict:
    from running_coach.schemas.feedback import ManualFeedback
    if not USE_DB:
        p = os.path.join(_udir(username), "feedback.json")
        if not os.path.exists(p):
            return {}
        raw = json.load(open(p))
        result = {}
        for key, val in raw.items():
            try:
                val["date"] = datetime.fromisoformat(val["date"])
                result[key] = ManualFeedback(**{k: v for k, v in val.items() if k in ManualFeedback.__dataclass_fields__})
            except Exception:
                pass
        return result
    rows = _sb_get("feedback", f"?username=eq.{urllib.parse.quote(str(username))}&order=entry_date.desc&limit=120")
    result = {}
    for row in rows:
        key = row.get("entry_date") or row.get("date")
        val = _json(row.get("data"), {}) or {}
        try:
            val["date"] = datetime.fromisoformat(val.get("date") or key)
            result[key] = ManualFeedback(**{k: v for k, v in val.items() if k in ManualFeedback.__dataclass_fields__})
        except Exception:
            pass
    return result


def save_feedback_entry(username: str, date_str: str, fb) -> None:
    data = {
        "date": fb.date.isoformat(),
        "rpe": fb.rpe,
        "mood": fb.mood,
        "sleep_hours": fb.sleep_hours,
        "sleep_quality": fb.sleep_quality,
        "hrv_ms": fb.hrv_ms,
        "pain_flag": fb.pain_flag,
        "pain_location": fb.pain_location,
        "notes": fb.notes,
    }
    data = {k: v for k, v in data.items() if v is not None}
    data["pain_flag"] = fb.pain_flag
    if not USE_DB:
        p = os.path.join(_udir(username), "feedback.json")
        existing = json.load(open(p)) if os.path.exists(p) else {}
        existing[date_str] = data
        with open(p, "w") as f:
            json.dump(existing, f, indent=2)
        return
    _sb_upsert("feedback", {"username": username, "entry_date": date_str, "data": data}, "username,entry_date")


# ── Runs cache: one row per Garmin run ───────────────────────────────────────

def _run_to_row(username: str, run: dict) -> dict:
    activity_id = str(run.get("external_id") or run.get("activity_id") or run.get("date"))
    distance_km = run.get("distance_km")
    duration_min = run.get("duration_minutes")
    pace = run.get("avg_pace_min_per_km")
    training_load = None
    try:
        # Simple deterministic load proxy. Stored for trends, not as a medical metric.
        training_load = round(float(distance_km or 0) * (float(run.get("avg_hr") or 140) / 140.0), 2)
    except Exception:
        pass
    return {
        "username": username,
        "activity_id": activity_id,
        "activity_date": run.get("date"),
        "activity_type": run.get("activity_type"),
        "distance_km": distance_km,
        "duration_minutes": duration_min,
        "avg_pace_min_per_km": pace,
        "avg_hr": run.get("avg_hr"),
        "max_hr": run.get("max_hr"),
        "elevation_gain_m": run.get("elevation_gain_m"),
        "cadence": run.get("cadence"),
        "training_load": training_load,
        "source": run.get("source") or "garmin_connect",
        "raw_json": run,
        "updated_at": datetime.utcnow().isoformat(),
    }


def _row_to_run(row: dict) -> dict:
    raw = _json(row.get("raw_json"), {}) or {}
    # Prefer normalized raw_json, but repair from columns if needed.
    raw.setdefault("date", row.get("activity_date"))
    raw.setdefault("activity_type", row.get("activity_type") or "outdoor_run")
    raw.setdefault("distance_km", row.get("distance_km"))
    raw.setdefault("duration_minutes", row.get("duration_minutes"))
    raw.setdefault("avg_pace_min_per_km", row.get("avg_pace_min_per_km"))
    raw.setdefault("avg_hr", row.get("avg_hr"))
    raw.setdefault("max_hr", row.get("max_hr"))
    raw.setdefault("elevation_gain_m", row.get("elevation_gain_m"))
    raw.setdefault("cadence", row.get("cadence"))
    raw.setdefault("source", row.get("source") or "garmin_cache")
    raw.setdefault("external_id", row.get("activity_id"))
    return raw


def load_cached_runs(username: str) -> Optional[list]:
    if not USE_DB:
        p = os.path.join(_udir(username), "runs_cache.json")
        if not os.path.exists(p):
            return None
        data = json.load(open(p))
        return data.get("runs") or None
    try:
        rows = _sb_get(
            "runs_cache",
            f"?username=eq.{urllib.parse.quote(str(username))}&select=*&order=activity_date.desc&limit=1000",
        )
        if not rows:
            return None
        return [_row_to_run(r) for r in rows]
    except Exception as e:
        print(f"[cache] load_cached_runs error: {e}", flush=True)
        return None


def save_cached_runs(username: str, runs_data: list) -> None:
    """Append/update runs. Never wipes existing history."""
    if not runs_data:
        print("[cache] no runs to save; keeping existing cache", flush=True)
        return
    if not USE_DB:
        existing = load_cached_runs(username) or []
        by_id = {str(r.get("external_id") or r.get("date")): r for r in existing}
        for r in runs_data:
            by_id[str(r.get("external_id") or r.get("date"))] = r
        merged = sorted(by_id.values(), key=lambda r: r.get("date", ""), reverse=True)
        with open(os.path.join(_udir(username), "runs_cache.json"), "w") as f:
            json.dump({"runs": merged, "updated_at": datetime.utcnow().isoformat()}, f, indent=2)
        return
    try:
        rows = [_run_to_row(username, r) for r in runs_data if r]
        if rows:
            _sb_upsert("runs_cache", rows, "username,activity_id")
            print(f"[cache] upserted {len(rows)} run rows", flush=True)
    except Exception as e:
        print(f"[cache] save_cached_runs error (non-fatal): {e}", flush=True)


def invalidate_runs_cache(username: str) -> None:
    """No-op for v2: runs_cache is historical. /refresh appends new rows instead of wiping."""
    return


# ── Daily cache: one row per user/day ────────────────────────────────────────

def _today() -> str:
    return datetime.now().date().isoformat()


def _normalise_daily(row: dict | None) -> Optional[dict]:
    if not row:
        return None
    summary = _json(row.get("summary"), {}) or {}
    watch = _json(row.get("watch_health"), None)
    if watch is None and isinstance(summary, dict):
        watch = summary.get("watch_health")
    if watch:
        summary["watch_health"] = watch
    for k in ("should_run_today", "next_run_date", "recommended_session", "coach_message", "coach_reason", "risk_level", "last_sync", "sync_status", "sync_error"):
        if row.get(k) is not None:
            summary[k] = row.get(k)
    return summary


def _load_daily_cache_row(username: str, date_str: str) -> Optional[dict]:
    if not USE_DB:
        p = os.path.join(_udir(username), "daily_cache.json")
        if not os.path.exists(p):
            return None
        return (json.load(open(p)) or {}).get(date_str)
    try:
        rows = _sb_get(
            "daily_cache",
            f"?username=eq.{urllib.parse.quote(str(username))}&cache_date=eq.{date_str}&select=*",
        )
        return _normalise_daily(rows[0]) if rows else None
    except Exception as e:
        print(f"[cache] load daily_cache error: {e}", flush=True)
        return None


def load_daily_cache_raw(username: str, date_str: str | None = None) -> Optional[dict]:
    return _load_daily_cache_row(username, date_str or _today())


def load_cached_summary(username: str) -> Optional[dict]:
    summary = _load_daily_cache_row(username, _today())
    if isinstance(summary, dict) and (summary.get("status") or summary.get("headline") or summary.get("coach_message")):
        return summary
    return None


def save_cached_summary(username: str, summary: dict) -> None:
    date_str = _today()
    summary = dict(summary or {})
    summary["summary_cached_at"] = datetime.utcnow().isoformat()
    if summary.get("headline") and not summary.get("coach_message"):
        summary["coach_message"] = summary.get("headline")
    if summary.get("subline") and not summary.get("coach_reason"):
        summary["coach_reason"] = summary.get("subline")
    if not USE_DB:
        p = os.path.join(_udir(username), "daily_cache.json")
        data = json.load(open(p)) if os.path.exists(p) else {}
        existing = data.get(date_str) or {}
        if existing.get("watch_health") and not summary.get("watch_health"):
            summary["watch_health"] = existing["watch_health"]
        data[date_str] = summary
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        return
    existing = _load_daily_cache_row(username, date_str) or {}
    if existing.get("watch_health") and not summary.get("watch_health"):
        summary["watch_health"] = existing["watch_health"]
    payload = {
        "username": username,
        "cache_date": date_str,
        "summary": summary,
        "watch_health": summary.get("watch_health"),
        "should_run_today": summary.get("should_run_today"),
        "next_run_date": summary.get("next_run_date"),
        "recommended_session": summary.get("recommended_session") or summary.get("rec_type"),
        "coach_message": summary.get("coach_message") or summary.get("headline"),
        "coach_reason": summary.get("coach_reason") or summary.get("subline"),
        "risk_level": summary.get("risk_level"),
        "last_sync": summary.get("health_cached_at") or summary.get("summary_cached_at") or datetime.utcnow().isoformat(),
        "sync_status": summary.get("sync_status") or "ok",
        "sync_error": summary.get("sync_error"),
        "updated_at": datetime.utcnow().isoformat(),
    }
    _sb_upsert("daily_cache", payload, "username,cache_date")


def load_cached_watch_health(username: str, date_str: str) -> Optional[dict]:
    row = _load_daily_cache_row(username, date_str)
    if not row:
        return None
    watch = row.get("watch_health")
    return watch if isinstance(watch, dict) else None


def save_cached_watch_health(username: str, date_str: str, health: dict) -> None:
    if not health:
        return
    summary = _load_daily_cache_row(username, date_str) or {}
    summary["watch_health"] = health
    summary["health_cached_at"] = datetime.utcnow().isoformat()
    summary["sync_status"] = "ok"
    if not USE_DB:
        p = os.path.join(_udir(username), "daily_cache.json")
        data = json.load(open(p)) if os.path.exists(p) else {}
        data[date_str] = summary
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        return
    payload = {
        "username": username,
        "cache_date": date_str,
        "summary": summary,
        "watch_health": health,
        "last_sync": summary["health_cached_at"],
        "sync_status": "ok",
        "updated_at": datetime.utcnow().isoformat(),
    }
    _sb_upsert("daily_cache", payload, "username,cache_date")


def mark_sync_failed(username: str, error: str, date_str: str | None = None) -> None:
    date_str = date_str or _today()
    summary = _load_daily_cache_row(username, date_str) or {}
    summary["sync_status"] = "failed"
    summary["sync_error"] = str(error)[:500]
    summary["last_sync"] = datetime.utcnow().isoformat()
    if not USE_DB:
        p = os.path.join(_udir(username), "daily_cache.json")
        data = json.load(open(p)) if os.path.exists(p) else {}
        data[date_str] = summary
        with open(p, "w") as f:
            json.dump(data, f, indent=2)
        return
    _sb_upsert("daily_cache", {
        "username": username,
        "cache_date": date_str,
        "summary": summary,
        "last_sync": summary["last_sync"],
        "sync_status": "failed",
        "sync_error": summary["sync_error"],
        "updated_at": datetime.utcnow().isoformat(),
    }, "username,cache_date")


def invalidate_watch_health_cache(username: str, date_str: str | None = None) -> None:
    # Historical daily rows are kept. Force refresh is controlled by /refresh cooldown.
    return


# ── Weekly schedule ───────────────────────────────────────────────────────────

DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
VALID_TYPES = {"rest", "easy", "moderate", "tempo", "long_run", "interval", "coach"}
# "coach" means: let the coaching engine decide for that day


def load_schedule(username: str) -> dict:
    """Return {day: run_type} dict. Defaults to all 'coach' (engine decides)."""
    default = {d: "coach" for d in DAYS}
    if not USE_DB:
        return _file_load_schedule(username) or default
    try:
        rows = _sb_get("weekly_schedule", f"?username=eq.{username}&select=schedule")
        if not rows or not rows[0].get("schedule"):
            return default
        raw = rows[0]["schedule"]
        sched = raw if isinstance(raw, dict) else json.loads(raw)
        # Fill any missing days with 'coach'
        return {d: sched.get(d, "coach") for d in DAYS}
    except Exception as e:
        print(f"[DB] load_schedule error: {e}", flush=True)
        return default


def save_schedule(username: str, schedule: dict) -> None:
    """Save {day: run_type} dict."""
    # Validate and sanitise
    clean = {d: schedule.get(d, "coach") for d in DAYS}
    clean = {d: (v if v in VALID_TYPES else "coach") for d, v in clean.items()}
    if not USE_DB:
        _file_save_schedule(username, clean)
        return
    try:
        existing = _sb_get("weekly_schedule", f"?username=eq.{username}&select=username")
        if existing:
            _sb_patch("weekly_schedule", {"schedule": clean}, f"?username=eq.{username}")
        else:
            _sb_insert("weekly_schedule", {"username": username, "schedule": clean})
    except Exception as e:
        print(f"[DB] save_schedule error: {e}", flush=True)


def _file_load_schedule(username: str):
    p = os.path.join(_udir(username), "schedule.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        return json.load(f)


def _file_save_schedule(username: str, schedule: dict):
    p = os.path.join(_udir(username), "schedule.json")
    with open(p, "w") as f:
        json.dump(schedule, f, indent=2)
