"""
Database layer — Supabase for production, local files for development.

Username convention: Strava athlete ID as a string (e.g. "12345678").
This is set once during OAuth and never changes.
"""

import json, os
from datetime import datetime
from typing import Optional, Any

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
USE_DB = bool(SUPABASE_URL and SUPABASE_KEY)

import urllib.request, urllib.error

def _sb_request(method, table, data=None, query=""):
    url     = f"{SUPABASE_URL}/rest/v1/{table}{query}"
    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }
    body = json.dumps(data).encode() if data else None
    req  = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else []
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Supabase {method} {table}: {e.code} {e.read().decode()}")

def _sb_get(table, query):    return _sb_request("GET",    table, query=query)
def _sb_upsert(table, data):  return _sb_request("POST",   table, data=data)
def _sb_patch(table, data, q):return _sb_request("PATCH",  table, data=data, query=q)
def _sb_delete(table, q):     return _sb_request("DELETE", table, query=q)
def _sb_insert(table, data):  return _sb_request("POST",   table, data=data)


# ── Profile ───────────────────────────────────────────────────────────────────

def load_profile(username: str) -> Optional[dict]:
    if not USE_DB:
        return _file_load_profile(username)
    rows = _sb_get("users", f"?username=eq.{username}&select=profile,display_name")
    if not rows:
        return None
    d = rows[0].get("profile") or {}
    if isinstance(d, str):
        d = json.loads(d)
    # Inject display_name into profile if missing
    if rows[0].get("display_name") and not d.get("name"):
        d["name"] = rows[0]["display_name"]
    return d

def save_profile(username: str, profile_dict: dict) -> None:
    if not USE_DB:
        _file_save_profile(username, profile_dict)
        return
    existing = _sb_get("users", f"?username=eq.{username}&select=username")
    payload  = {"profile": json.dumps(profile_dict),
                "display_name": profile_dict.get("name", username)}
    if existing:
        _sb_patch("users", payload, f"?username=eq.{username}")
    else:
        _sb_insert("users", {"username": username, **payload})


# ── Strava token ──────────────────────────────────────────────────────────────

def load_strava_token(username: str) -> Optional[dict]:
    if not USE_DB:
        return _file_load_token(username)
    rows = _sb_get("users", f"?username=eq.{username}&select=strava_token")
    if not rows or not rows[0].get("strava_token"):
        return None
    raw = rows[0]["strava_token"]
    return json.loads(raw) if isinstance(raw, str) else raw

def save_strava_token(username: str, token: dict,
                      display_name: str = "", athlete_data: dict = None) -> None:
    if not USE_DB:
        _file_save_token(username, token)
        return
    existing = _sb_get("users", f"?username=eq.{username}&select=username")
    name     = display_name or (athlete_data or {}).get("firstname", "") + \
               " " + (athlete_data or {}).get("lastname", "")
    avatar   = (athlete_data or {}).get("profile_medium", "")
    payload  = {
        "strava_token":  json.dumps(token),
        "display_name":  name.strip() or username,
        "avatar_url":    avatar,
    }
    if existing:
        _sb_patch("users", payload, f"?username=eq.{username}")
    else:
        _sb_insert("users", {"username": username, **payload})

def has_strava(username: str) -> bool:
    return load_strava_token(username) is not None

def load_athlete_info(username: str) -> dict:
    """Return display_name and avatar_url for the nav bar."""
    if not USE_DB:
        d = _file_load_profile(username) or {}
        return {"name": d.get("name", username), "avatar": ""}
    rows = _sb_get("users", f"?username=eq.{username}&select=display_name,avatar_url")
    if not rows:
        return {"name": username, "avatar": ""}
    return {"name": rows[0].get("display_name", username),
            "avatar": rows[0].get("avatar_url", "")}


# ── Feedback ──────────────────────────────────────────────────────────────────

def load_feedback(username: str) -> dict:
    from running_coach.schemas.feedback import ManualFeedback
    if not USE_DB:
        return _file_load_feedback(username)
    rows   = _sb_get("feedback", f"?username=eq.{username}&order=date.desc&limit=90")
    result = {}
    for row in rows:
        key = row["date"]
        val = row["data"]
        if isinstance(val, str):
            val = json.loads(val)
        val["date"] = datetime.fromisoformat(val["date"])
        try:
            result[key] = ManualFeedback(**{
                k: v for k, v in val.items()
                if k in ManualFeedback.__dataclass_fields__
            })
        except Exception:
            pass
    return result

def save_feedback_entry(username: str, date_str: str, fb) -> None:
    if not USE_DB:
        _file_save_feedback_entry(username, date_str, fb)
        return
    data = {
        "date": fb.date.isoformat(), "rpe": fb.rpe, "mood": fb.mood,
        "sleep_hours": fb.sleep_hours, "sleep_quality": fb.sleep_quality,
        "hrv_ms": fb.hrv_ms, "pain_flag": fb.pain_flag,
        "pain_location": fb.pain_location, "notes": fb.notes,
    }
    data = {k: v for k, v in data.items() if v is not None}
    data["date"]      = fb.date.isoformat()
    data["pain_flag"] = fb.pain_flag
    _sb_delete("feedback", f"?username=eq.{username}&date=eq.{date_str}")
    _sb_insert("feedback", {"username": username, "date": date_str,
                             "data": json.dumps(data)})

def list_users() -> list:
    if not USE_DB:
        return _file_list_users()
    rows = _sb_get("users", "?select=username,display_name,avatar_url")
    return [{"username": r["username"],
             "name":     r.get("display_name", r["username"]),
             "avatar":   r.get("avatar_url", "")} for r in rows]


# ── File fallback ─────────────────────────────────────────────────────────────

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "user_data"
)

def _udir(u):
    safe = "".join(c for c in str(u).lower() if c.isalnum() or c == "_")
    d = os.path.join(DATA_DIR, safe)
    os.makedirs(os.path.join(d, "models"), exist_ok=True)
    return d

def _file_load_profile(u):
    p = os.path.join(_udir(u), "profile.json")
    return json.load(open(p)) if os.path.exists(p) else None

def _file_save_profile(u, d):
    with open(os.path.join(_udir(u), "profile.json"), "w") as f:
        json.dump(d, f, indent=2)

def _file_load_token(u):
    p = os.path.join(_udir(u), "strava_token.json")
    return json.load(open(p)) if os.path.exists(p) else None

def _file_save_token(u, d):
    with open(os.path.join(_udir(u), "strava_token.json"), "w") as f:
        json.dump(d, f, indent=2)

def _file_load_feedback(u):
    from running_coach.schemas.feedback import ManualFeedback
    p = os.path.join(_udir(u), "feedback.json")
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        raw = json.load(f)
    result = {}
    for key, val in raw.items():
        val["date"] = datetime.fromisoformat(val["date"])
        try:
            result[key] = ManualFeedback(**{
                k: v for k, v in val.items()
                if k in ManualFeedback.__dataclass_fields__
            })
        except Exception:
            pass
    return result

def _file_save_feedback_entry(u, date_str, fb):
    p = os.path.join(_udir(u), "feedback.json")
    existing = json.load(open(p)) if os.path.exists(p) else {}
    data = {
        "date": fb.date.isoformat(), "rpe": fb.rpe, "mood": fb.mood,
        "sleep_hours": fb.sleep_hours, "sleep_quality": fb.sleep_quality,
        "hrv_ms": fb.hrv_ms, "pain_flag": fb.pain_flag,
        "pain_location": fb.pain_location, "notes": fb.notes,
    }
    existing[date_str] = {k: v for k, v in data.items() if v is not None}
    existing[date_str]["pain_flag"] = fb.pain_flag
    existing[date_str]["date"]      = fb.date.isoformat()
    with open(p, "w") as f:
        json.dump(existing, f, indent=2)

def _file_list_users():
    if not os.path.exists(DATA_DIR):
        return []
    result = []
    for u in os.listdir(DATA_DIR):
        p = os.path.join(DATA_DIR, u, "profile.json")
        if os.path.exists(p):
            with open(p) as f:
                d = json.load(f)
            result.append({"username": u, "name": d.get("name", u), "avatar": ""})
    return result


# ── Daily summary cache ───────────────────────────────────────────────────────
# Stored in Supabase under a "daily_cache" table, or as a JSON file locally.
# Key: (username, date_str) — one row per user per day.
# Expires automatically when the date changes (checked on read).

def load_cached_summary(username: str) -> Optional[dict]:
    """
    Return today's cached summary for this user, or None if not cached yet.
    'Today' is determined in the user's local date — we use the server date
    as a reasonable proxy (close enough for a daily summary).
    """
    today = datetime.now().date().isoformat()

    if not USE_DB:
        return _file_load_cache(username, today)

    try:
        rows = _sb_get("daily_cache",
                       f"?username=eq.{username}&date=eq.{today}&select=summary")
        if rows and rows[0].get("summary"):
            raw = rows[0]["summary"]
            return json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        pass
    return None


def save_cached_summary(username: str, summary: dict) -> None:
    """Save today's summary. Overwrites any existing entry for today."""
    today = datetime.now().date().isoformat()

    if not USE_DB:
        _file_save_cache(username, today, summary)
        return

    try:
        # Delete old entry for today then insert fresh
        _sb_delete("daily_cache",
                   f"?username=eq.{username}&date=eq.{today}")
        _sb_insert("daily_cache", {
            "username": username,
            "date":     today,
            "summary":  json.dumps(summary),
        })
    except Exception as e:
        print(f"Cache save failed (non-fatal): {e}")


# ── File fallback for cache ───────────────────────────────────────────────────

def _file_load_cache(username: str, today: str) -> Optional[dict]:
    p = os.path.join(_udir(username), "daily_cache.json")
    if not os.path.exists(p):
        return None
    with open(p) as f:
        data = json.load(f)
    # Only return if it was computed today
    if data.get("date") == today:
        return data.get("summary")
    return None


def _file_save_cache(username: str, today: str, summary: dict) -> None:
    p = os.path.join(_udir(username), "daily_cache.json")
    with open(p, "w") as f:
        json.dump({"date": today, "summary": summary}, f, indent=2)
