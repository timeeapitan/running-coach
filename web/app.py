"""
Running Coach — Flask web application with Garmin Connect sync.
"""

import json, os, sys, tempfile, time
from datetime import datetime, timedelta
from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, session, abort)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from web.auth import (
    login_required, current_user_id, current_user_name, current_user_avatar,
    host_credentials_set, get_host_garmin_credentials, secret_session_set,
)
from web.db import (
    load_profile, save_profile, load_activity_token, save_activity_token,
    load_feedback, save_feedback_entry, list_users, load_athlete_info, USE_DB,
    load_cached_summary, save_cached_summary, load_daily_cache_raw,
    load_cached_runs, save_cached_runs, invalidate_runs_cache,
    load_cached_watch_health, save_cached_watch_health, invalidate_watch_health_cache, mark_sync_failed,
    load_schedule, save_schedule,
)
from running_coach.schemas.profile  import RunnerProfile
from running_coach.schemas.feedback import ManualFeedback
from running_coach.coaching.coach   import RunningCoach
from running_coach.ml.models.next_run_predictor import NextRunPredictor
from running_coach.analysis.insights import (
    zone2_drift, injury_risk, predict_race_time, generate_race_plan
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "running-coach-dev-key-change-me")

_MODEL_CACHE = {}

# Global Garmin rate limit tracker
_GARMIN_RATE_LIMITED_UNTIL = None
_GARMIN_RATE_LIMIT_SECONDS = 300  # 5 min backoff after any 429

# Set rate limit at startup to prevent immediate Garmin fetch after restart/crash.
# This gives the process time to stabilise before hitting the API.
# Cleared after 60 seconds so first user Sync still works quickly.
import time as _time
_GARMIN_RATE_LIMITED_UNTIL = datetime.now() + __import__('datetime').timedelta(seconds=60)
print("[STARTUP] 60s Garmin cooldown set to prevent post-restart 429", flush=True)

def _garmin_is_rate_limited() -> bool:
    global _GARMIN_RATE_LIMITED_UNTIL
    if _GARMIN_RATE_LIMITED_UNTIL is None:
        return False
    if datetime.now() < _GARMIN_RATE_LIMITED_UNTIL:
        remaining = int((_GARMIN_RATE_LIMITED_UNTIL - datetime.now()).total_seconds())
        print(f"[GARMIN] rate limited — {remaining}s remaining", flush=True)
        return True
    _GARMIN_RATE_LIMITED_UNTIL = None
    return False

def _garmin_set_rate_limited():
    global _GARMIN_RATE_LIMITED_UNTIL
    from datetime import timedelta
    _GARMIN_RATE_LIMITED_UNTIL = datetime.now() + timedelta(seconds=_GARMIN_RATE_LIMIT_SECONDS)
    print(f"[GARMIN] rate limit set until {_GARMIN_RATE_LIMITED_UNTIL.strftime('%H:%M:%S')}", flush=True)


# Pre-warm the Garmin session at startup so the first user request is not
# the one that triggers garth.resume() — reduces cold-start 429 risk.
def _prewarm_garmin():
    try:
        from running_coach.parsers.garmin_connect import _ensure_garth, secret_session_available
        from web.auth import _GARTH_SESSION_DIR
        if secret_session_available(_GARTH_SESSION_DIR):
            _ensure_garth(_GARTH_SESSION_DIR)
            print("[STARTUP] Garmin session pre-warmed", flush=True)
    except Exception as e:
        print(f"[STARTUP] Garmin pre-warm skipped: {e}", flush=True)

with app.app_context():
    _prewarm_garmin()


# ── Auth routes ───────────────────────────────────────────────────────────────

@app.route("/login")
def login():
    if session.get("user_id"):
        return redirect(url_for("dashboard"))
    return render_template("login.html",
        host_credentials=host_credentials_set(),
        secret_session=secret_session_set())


@app.route("/auth/credentials", methods=["POST"])
def auth_credentials():
    """Sign in with Garmin Connect credentials and save them for future syncs."""
    email = request.form.get("email", "").strip()
    password = request.form.get("password", "").strip()
    if not email or not password:
        return render_template("login.html",
            host_credentials=host_credentials_set(),
            secret_session=secret_session_set(),
            error="Please enter both Garmin email and password.")
    return _finish_garmin_login(email, password)



@app.route("/auth/session")
def auth_session():
    """Sign in using Garmin session files mounted as Render Secret Files.

    This does not call Garmin's login endpoint, so it avoids the 429 rate-limit
    issue caused by repeated username/password authentication from Render.
    """
    if not secret_session_set():
        return render_template("login.html",
            host_credentials=host_credentials_set(),
            secret_session=False,
            error="Garmin session files are missing. Upload oauth1_token.json and oauth2_token.json as Render Secret Files.")

    # Use one stable local app user. No Garmin password is stored in Supabase.
    username = os.environ.get("APP_USER_ID", "timeea").strip().lower()
    name = os.environ.get("APP_USER_NAME", "Timeea Pitan").strip() or username
    token = {"provider": "garmin", "auth_mode": "secret_files", "session_dir": os.environ.get("GARTH_SESSION_DIR", "/etc/secrets")}

    try:
        save_activity_token(username, token, display_name=name, athlete_data={})
    except Exception as e:
        import traceback
        print("SUPABASE SAVE ERROR:", traceback.format_exc(), flush=True)
        return render_template("login.html",
            host_credentials=host_credentials_set(),
            secret_session=secret_session_set(),
            error=f"Database error: {e} — Have you run setup_db.sql in Supabase?")

    if not load_profile(username):
        from running_coach.schemas.enums import FitnessLevel
        import dataclasses
        p = RunnerProfile(name=name, fitness_level=FitnessLevel.INTERMEDIATE, runs_per_week=3)
        d = dataclasses.asdict(p)
        d["fitness_level"] = p.fitness_level.value
        save_profile(username, d)

    session.permanent = True
    session["user_id"] = username
    session["user_name"] = name
    session["user_avatar"] = ""
    return redirect(url_for("dashboard"))

@app.route("/auth/host")
def auth_host():
    """Sign in using GARMIN_EMAIL/GARMIN_PASSWORD from Render environment."""
    email, password = get_host_garmin_credentials()
    if not email or not password:
        return redirect(url_for("login"))
    return _finish_garmin_login(email, password)


def _finish_garmin_login(email: str, password: str):
    """Verify Garmin credentials, store them, and start the app session."""
    from running_coach.parsers.garmin_connect import GarminConnectParser

    username = email.strip().lower()
    name = username.split("@")[0].replace(".", " ").title()

    # Skip upfront verification to avoid rate-limiting the OAuth endpoint.
    # Credentials are verified on the first dashboard load instead.
    token = {
        "provider": "garmin",
        "email": email,
        "password": password,
    }

    try:
        save_activity_token(username, token, display_name=name, athlete_data={})
    except Exception as e:
        import traceback
        print("SUPABASE SAVE ERROR:", traceback.format_exc(), flush=True)
        return render_template("login.html",
            host_credentials=host_credentials_set(),
            secret_session=secret_session_set(),
            error=f"Database error: {e} — Have you run setup_db.sql in Supabase?")

    if not load_profile(username):
        from running_coach.schemas.enums import FitnessLevel
        import dataclasses
        p = RunnerProfile(name=name, fitness_level=FitnessLevel.INTERMEDIATE, runs_per_week=3)
        d = dataclasses.asdict(p)
        d["fitness_level"] = p.fitness_level.value
        save_profile(username, d)

    session.permanent = True
    session["user_id"] = username
    session["user_name"] = name
    session["user_avatar"] = ""

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_callback_domain() -> str:
    """Return the current app domain."""
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url:
        return render_url.replace("https://", "").replace("http://", "")
    try:
        return request.host
    except Exception:
        return "localhost:5000"


def _get_profile(uid) -> RunnerProfile:
    d = load_profile(uid)
    if not d:
        return None
    from running_coach.schemas.enums import FitnessLevel
    d["fitness_level"] = FitnessLevel(d.get("fitness_level", "intermediate"))
    return RunnerProfile(**{k: v for k, v in d.items()
                            if k in RunnerProfile.__dataclass_fields__})

def _save_profile_obj(uid, profile: RunnerProfile):
    import dataclasses
    d = dataclasses.asdict(profile)
    d["fitness_level"] = profile.fitness_level.value
    save_profile(uid, d)

def _get_model_dir(uid):
    if uid not in _MODEL_CACHE:
        _MODEL_CACHE[uid] = tempfile.mkdtemp(prefix=f"rc_{uid}_")
    return _MODEL_CACHE[uid]

def _serialize_runs(runs) -> list:
    return [{
        "date":               r.date.isoformat(),
        "activity_type":      r.activity_type.value,
        "distance_km":        r.distance_km,
        "duration_minutes":   r.duration_minutes,
        "avg_pace_min_per_km":r.avg_pace_min_per_km,
        "avg_hr":             r.avg_hr,
        "max_hr":             r.max_hr,
        "elevation_gain_m":   r.elevation_gain_m,
        "cadence":            r.cadence,
        "source":             r.source,
        "external_id":        r.external_id,
    } for r in runs]

def _deserialize_runs(data: list):
    from running_coach.schemas import NormalizedRun, ActivityType
    runs = []
    for d in data:
        try:
            runs.append(NormalizedRun(
                date=datetime.fromisoformat(d["date"]),
                activity_type=ActivityType(d["activity_type"]),
                distance_km=d["distance_km"],
                duration_minutes=d["duration_minutes"],
                avg_pace_min_per_km=d.get("avg_pace_min_per_km"),
                avg_hr=d.get("avg_hr"),
                max_hr=d.get("max_hr"),
                elevation_gain_m=d.get("elevation_gain_m"),
                cadence=d.get("cadence"),
                source=d.get("source", "cache"),
                external_id=d.get("external_id"),
            ))
        except Exception:
            pass
    return runs

# Module-level singleton parsers — created once per process, reused for all requests.
# This is the key fix for 429 errors: garth.resume() is called only once.
_PARSER_CACHE: dict = {}

def _get_garmin_parser(uid):
    """
    Return a cached GarminConnectParser. Creating a new instance on every request
    triggers garth.resume() repeatedly, which causes Garmin 429 rate-limit errors.
    The singleton pattern ensures the session is established once per process lifetime.
    """
    if uid in _PARSER_CACHE:
        return _PARSER_CACHE[uid]

    from running_coach.parsers.garmin_connect import GarminConnectParser, secret_session_available

    if secret_session_available():
        # garth session — _ensure_garth() called once inside the parser
        parser = GarminConnectParser()
    else:
        token    = load_activity_token(uid) or {}
        email    = token.get("email", "")
        password = token.get("password", "")
        if not email or not password:
            raise RuntimeError(
                "Missing Garmin session. Upload oauth1_token.json and oauth2_token.json "
                "as Render Secret Files, or set GARMIN_EMAIL and GARMIN_PASSWORD."
            )
        parser = GarminConnectParser(email, password)

    _PARSER_CACHE[uid] = parser
    return parser

def _load_runs(uid, force=False, auto_fetch=False):
    """Load cached running history and sync Garmin only when needed.

    v2 behavior:
    - normal tabs read Supabase only;
    - first Dashboard visit of the day syncs once if daily_cache is missing;
    - /refresh force-syncs, but append/upsert keeps existing history.
    """
    cached = load_cached_runs(uid)

    today_has_daily_cache = load_daily_cache_raw(uid) is not None
    should_fetch = force or (auto_fetch and not today_has_daily_cache) or (cached is None and auto_fetch)

    if not should_fetch:
        return _deserialize_runs(cached) if cached is not None else []

    try:
        runs = _get_garmin_parser(uid).fetch_runs(max_runs=30)
        print(f"[GARMIN] fetched {len(runs)} recent runs", flush=True)
        if runs:
            save_cached_runs(uid, _serialize_runs(runs))
        merged = load_cached_runs(uid)
        return _deserialize_runs(merged) if merged is not None else runs
    except Exception as e:
        import traceback
        print("[GARMIN SYNC] fetch failed:", repr(e), flush=True)
        print(traceback.format_exc(), flush=True)
        try:
            mark_sync_failed(uid, str(e))
        except Exception:
            pass
        return _deserialize_runs(cached) if cached is not None else []

def _load_watch_health(uid, date_obj=None, force=False, auto_fetch=False):
    """Load daily watch metrics with a once-per-day auto sync.

    If today's daily_cache is missing and auto_fetch=True, fetch Garmin once,
    save daily_cache, and reuse it for the rest of the day. Notes and other
    tabs keep reading cache only. /refresh still forces a fresh fetch.
    """
    if date_obj is None:
        date_obj = datetime.now().date()
    date_str = date_obj.isoformat() if hasattr(date_obj, "isoformat") else str(date_obj)

    cached = load_cached_watch_health(uid, date_str)
    if cached is not None and not force:
        return cached
    if cached is None and not force and not auto_fetch:
        return {}

    # Check global rate limit before health fetch
    if not force and _garmin_is_rate_limited():
        print("[GARMIN HEALTH] rate limited — serving cache", flush=True)
        return cached or {}

    try:
        watch = _get_garmin_parser(uid).fetch_daily_health(date_obj) or {}
        if watch:
            save_cached_watch_health(uid, date_str, watch)
        return watch
    except Exception as e:
        err_str = repr(e)
        print("[GARMIN HEALTH] unavailable:", err_str, flush=True)
        if "429" in err_str:
            _garmin_set_rate_limited()
        try:
            mark_sync_failed(uid, str(e), date_str)
        except Exception:
            pass
        cached = load_cached_watch_health(uid, date_str)
        return cached or {}

def _merge_watch_feedback(uid, feedback, force=False, auto_fetch=False):
    """Overlay cached watch sleep/HRV data on today's feedback without overwriting manual mood/RPE/pain."""
    from running_coach.schemas.feedback import ManualFeedback
    today = datetime.now().date()
    key = today.isoformat()
    watch = _load_watch_health(uid, today, force=force, auto_fetch=auto_fetch)
    if not watch:
        return feedback, {}
    existing = feedback.get(key)
    fb = existing or ManualFeedback(date=datetime.combine(today, datetime.min.time()))
    changed = False
    for attr in ("sleep_hours", "sleep_quality", "hrv_ms"):
        val = watch.get(attr)
        if val is not None and getattr(fb, attr, None) is None:
            setattr(fb, attr, val)
            changed = True
    feedback[key] = fb
    # Keep it cached in your normal feedback table so the recommendation uses it consistently today.
    if changed:
        try:
            save_feedback_entry(uid, key, fb)
        except Exception as e:
            print("[GARMIN HEALTH] could not cache feedback:", repr(e), flush=True)
    return feedback, watch

def _get_coach(uid, profile):
    return RunningCoach(profile, model_dir=_get_model_dir(uid))

def _require_profile(uid):
    """
    Load the user profile or redirect to setup.
    Returns (profile, None) on success, (None, redirect_response) on missing.
    Usage:
        profile, redir = _require_profile(uid)
        if redir: return redir
    """
    profile = _get_profile(uid)
    if not profile:
        return None, redirect(url_for("setup"))
    return profile, None

def _fmt_pace(p):
    if not p: return "—"
    return f"{int(p)}:{int((p%1)*60):02d}/km"

def _compute_prs(runs):
    if not runs: return {}
    wp = [r for r in runs if r.avg_pace_min_per_km]
    we = [r for r in runs if r.elevation_gain_m]
    return {
        "longest_km":       round(max(r.distance_km for r in runs), 1),
        "fastest_pace_str": _fmt_pace(min(r.avg_pace_min_per_km for r in wp)) if wp else None,
        "most_elev":        int(max(r.elevation_gain_m for r in we)) if we else None,
        "total_runs":       len(runs),
        "total_km":         round(sum(r.distance_km for r in runs), 1),
    }

def _weekly_summary(runs, weeks=8):
    now = datetime.now()
    result = []
    for w in range(weeks-1, -1, -1):
        start = now - timedelta(weeks=w+1)
        end   = now - timedelta(weeks=w)
        wr    = [r for r in runs if start <= r.date < end]
        hrs   = [r.avg_hr for r in wr if r.avg_hr]
        result.append({
            "label": start.strftime("%d %b"),
            "km":    round(sum(r.distance_km for r in wr), 1),
            "runs":  len(wr),
            "avg_hr":round(sum(hrs)/len(hrs), 0) if hrs else 0,
        })
    return result

def _rest_recommendation_from_summary(summary):
    """Convert the daily recovery gate into the recommendation card."""
    from running_coach.schemas.enums import WorkoutType, Intensity
    from running_coach.schemas.workout import WorkoutRecommendation

    days = int(summary.get("days_until_next_run") or 0)
    if days <= 0:
        next_text = "your next planned run"
    elif days == 1:
        next_text = "tomorrow"
    else:
        next_text = f"in {days} days"

    reasons = summary.get("recovery_reasons") or []
    reason = ", ".join(reasons[:2]) if reasons else "your recent training load"

    return WorkoutRecommendation(
        workout_type=WorkoutType.REST,
        intensity=Intensity.VERY_EASY,
        description="Rest / recovery day",
        rationale=f"No run recommended today. Next run: {next_text}. Reason: {reason}.",
        target_distance_km=None,
        target_duration_minutes=None,
        target_hr_zone=None,
        steps=[
            {"label": "Recovery", "detail": "Walk lightly, stretch, hydrate, and sleep well."},
            {"label": "Next run", "detail": f"Come back {next_text} unless your watch recovery metrics are still low."},
        ],
    )



# ── App pages (all protected) ─────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    uid      = current_user_id()
    profile  = _get_profile(uid)
    if not profile:
        return redirect(url_for("setup"))

    # Dashboard is the only normal page that may auto-sync.
    # If today's cache is missing, this fetches Garmin once and saves cache.
    # Later visits today read from runs_cache/daily_cache instantly.
    runs     = _load_runs(uid, auto_fetch=False)
    feedback = load_feedback(uid)
    feedback, watch_health = _merge_watch_feedback(uid, feedback, auto_fetch=False)
    coach    = _get_coach(uid, profile)
    schedule = load_schedule(uid)

    # Personal stats — form score, VO2max, fitness age, personal bests, streak
    from running_coach.analysis.personal_stats import compute_personal_stats
    personal_stats = compute_personal_stats(runs, profile) if runs else {}

    # Pre-compute sync availability for the template
    # so the JS knows instantly without an extra API call
    sync_available = True
    sync_wait_message = None
    try:
        raw_cache = load_cached_summary(uid) or {}
        last_sync_text = raw_cache.get("health_cached_at") or raw_cache.get("summary_cached_at")
        if last_sync_text:
            last_sync_dt = datetime.fromisoformat(str(last_sync_text).replace("Z", "+00:00")).replace(tzinfo=None)
            diff_minutes = int((datetime.now() - last_sync_dt).total_seconds() / 60)
            if diff_minutes < 60:
                sync_available = False
                remaining = 60 - diff_minutes
                sync_wait_message = f"Already synced {diff_minutes} minute{'s' if diff_minutes != 1 else ''} ago. Next sync available in {remaining} minute{'s' if remaining != 1 else ''}."
    except Exception:
        pass

    # No runs yet — still show a profile-based recommendation using rules engine
    is_new_user = len(runs) == 0
    sync_rate_limited = False
    if not runs and not is_new_user:
        # Runs were expected but returned empty — likely a rate limit, not truly new user
        from web.db import load_cached_runs as _lcr
        if _lcr(uid):
            sync_rate_limited = True  # cache has data but fetch failed

    # ML training disabled — uses too much memory on Render free tier (512MB limit)
    # Re-enable when running on a paid plan with more memory
    # if len(runs) >= 10 and not coach.trainer.fatigue_predictor.is_trained:
    #     coach.train_models(runs)

    # Auto-detect fitness level (only when we have data)
    fitness_result = {"changed": False, "reason": "", "level": profile.fitness_level}
    if runs:
        fitness_result = coach.detect_and_update_fitness_level(runs)
        if fitness_result["changed"]:
            _save_profile_obj(uid, coach.profile)

    analysis = coach.analyze(runs, feedback)

    # Determine what today's schedule says
    import datetime as _dt
    today_name   = _dt.datetime.now().strftime("%A").lower()
    user_schedule = load_schedule(uid)
    planned_type  = user_schedule.get(today_name, "coach")
    rec = (coach.predict_next_run(runs, feedback)
           if len(runs) >= NextRunPredictor.MIN_RUNS_FOR_PERSONALISATION
           else coach.recommend(analysis, runs))

    from running_coach.analysis.daily_summary import build_daily_summary

    # Try to load today's cached summary first
    summary = load_cached_summary(uid)
    if summary is None:
        # Not cached yet today — compute and store it
        summary = build_daily_summary(runs, profile, analysis, rec, feedback)
        save_cached_summary(uid, summary)

    # Recovery gate: when the latest run was hard enough to require rest,
    # the dashboard must not show a normal run suggestion.
    if summary and summary.get("should_run_today") is False:
        rec = _rest_recommendation_from_summary(summary)

    # Build planned recommendation if schedule specifies a type (not "coach")
    planned_rec = None
    if planned_type != "coach" and planned_type != "rest" and not analysis.warnings:
        from running_coach.schemas.enums import WorkoutType as _WT
        type_map = {
            "easy":     _WT.EASY,
            "moderate": _WT.MODERATE,
            "tempo":    _WT.TEMPO,
            "long_run": _WT.LONG_RUN,
            "interval": _WT.INTERVAL,
        }
        if planned_type in type_map:
            planned_rec = coach.rules.recommend_specific_type(
                analysis, runs, type_map[planned_type]
            )
    elif planned_type == "rest":
        from running_coach.schemas.enums import WorkoutType as _WT, Intensity as _IN
        from running_coach.schemas.workout import WorkoutRecommendation as _WR
        planned_rec = _WR(
            workout_type=_WT.REST, intensity=_IN.VERY_EASY,
            description="Rest day",
            rationale="You planned a rest day for today.",
            steps=[{"label": "Rest", "detail": "No run today — scheduled rest."}],
        )
        # Override rec with rest too — don't show contradictory coach advice
        rec = planned_rec

    return render_template("dashboard.html",
        profile=profile, analysis=analysis, recommendation=rec,
        zones=profile.get_hr_zones(), prs=_compute_prs(runs),
        weeks_to_race=profile.weeks_to_race(), run_count=len(runs),
        ml_active=coach.trainer.fatigue_predictor.is_trained,
        summary=summary if not is_new_user else None,
        fitness_result=fitness_result,
        is_new_user=is_new_user,
        watch_health=watch_health,
        latest_run=sorted(runs, key=lambda r: r.date, reverse=True)[0] if runs else None,
        no_runs=False, error=None,
        planned_rec=planned_rec,
        today_planned=planned_type,
        schedule=schedule,
        sync_available=sync_available,
        sync_wait_message=sync_wait_message,
        personal_stats=personal_stats,
        user_name=current_user_name(), user_avatar=current_user_avatar())


@app.route("/history")
@login_required
def history():
    uid              = current_user_id()
    profile, redir   = _require_profile(uid)
    if redir: return redir
    runs = _load_runs(uid)
    if not runs:
        return render_template("history.html", profile=profile,
            runs=[], chart_data="[]", weekly="[]", drift={},
            is_new_user=True,
            user_name=current_user_name(), user_avatar=current_user_avatar())
    recent = sorted(runs, key=lambda r: r.date, reverse=True)[:60]
    chart_data = [
        {"date": r.date.strftime("%d %b"),
         "pace": round(r.avg_pace_min_per_km,2) if r.avg_pace_min_per_km else None,
         "hr":   int(r.avg_hr) if r.avg_hr else None,
         "distance": round(r.distance_km,1)}
        for r in reversed(recent)
    ]
    return render_template("history.html",
        profile=profile, runs=recent,
        chart_data=json.dumps(chart_data),
        weekly=json.dumps(_weekly_summary(runs, 8)),
        drift=zone2_drift(runs, profile),
        zones=profile.get_hr_zones(),
        user_name=current_user_name(), user_avatar=current_user_avatar())


@app.route("/insights")
@login_required
def insights():
    uid     = current_user_id()
    profile = _get_profile(uid)
    if not profile: return redirect(url_for("setup"))
    runs  = _load_runs(uid)
    risk  = injury_risk(runs, profile) if runs else \
            {"score":0,"level":"unknown","factors":{},"advice":[]}
    pred  = (predict_race_time(runs, profile, profile.race_distance_km)
             if profile.race_distance_km and runs else None)
    return render_template("insights.html",
        profile=profile, risk=risk, race_pred=pred, runs_count=len(runs),
        user_name=current_user_name(), user_avatar=current_user_avatar())


@app.route("/log", methods=["GET", "POST"])
@login_required
def log_feedback():
    uid     = current_user_id()
    profile = _get_profile(uid)
    if not profile: return redirect(url_for("setup"))

    if request.method == "POST":
        f    = request.form
        date = f.get("date", datetime.now().date().isoformat())
        def _i(k): v=f.get(k,"").strip(); return int(v) if v.isdigit() else None
        def _f(k):
            v=f.get(k,"").strip()
            try: return float(v)
            except: return None
        fb = ManualFeedback(
            date=datetime.fromisoformat(date), rpe=_i("rpe"), mood=_i("mood"),
            sleep_hours=_f("sleep_hours"), sleep_quality=_i("sleep_quality"),
            hrv_ms=_f("hrv_ms"), pain_flag=bool(f.get("pain_flag")),
            pain_location=f.get("pain_location","").strip() or None,
            notes=f.get("notes","").strip() or None,
        )
        save_feedback_entry(uid, date, fb)
        return redirect(url_for("dashboard"))

    feedback = load_feedback(uid)
    feedback, watch_health = _merge_watch_feedback(uid, feedback)
    recent   = sorted(feedback.values(), key=lambda x: x.date, reverse=True)[:7]
    return render_template("log.html", profile=profile, watch_health=watch_health,
        recent_feedback=recent, today=datetime.now().date().isoformat(),
        user_name=current_user_name(), user_avatar=current_user_avatar())


@app.route("/race", methods=["GET", "POST"])
@login_required
def race():
    uid     = current_user_id()
    profile = _get_profile(uid)
    if not profile: return redirect(url_for("setup"))
    if request.method == "POST":
        f = request.form
        profile.race_date              = f.get("race_date") or None
        profile.race_distance_km       = float(f.get("race_distance_km") or 0) or None
        profile.race_goal_time_minutes = float(f.get("race_goal_minutes") or 0) or None
        _save_profile_obj(uid, profile)
        return redirect(url_for("race"))
    runs = _load_runs(uid)
    plan = generate_race_plan(profile, runs) if profile.race_date else None
    pred = (predict_race_time(runs, profile, profile.race_distance_km)
            if profile.race_distance_km and runs else None)
    return render_template("race.html",
        profile=profile, plan=plan, pred=pred,
        weeks_to_race=profile.weeks_to_race(), fmt_pace=_fmt_pace,
        user_name=current_user_name(), user_avatar=current_user_avatar())


@app.route("/setup", methods=["GET", "POST"])
@login_required
def setup():
    uid = current_user_id()
    if request.method == "POST":
        f = request.form
        def _i(k): v=f.get(k,"").strip(); return int(v) if v.isdigit() else None
        def _f(k):
            v=f.get(k,"").strip()
            try: return float(v)
            except: return None
        from running_coach.schemas.enums import FitnessLevel
        profile = RunnerProfile(
            name=f.get("name", current_user_name()).strip(),
            age=_i("age"), max_hr=_i("max_hr"), resting_hr=_i("resting_hr"),
            runs_per_week=_i("runs_per_week") or 3,
            fitness_level=FitnessLevel(f.get("fitness_level","intermediate")),
            goal_weekly_km=_f("goal_weekly_km"),
        )
        _save_profile_obj(uid, profile)
        return redirect(url_for("dashboard"))
    return render_template("setup.html", profile=_get_profile(uid),
        user_name=current_user_name(), user_avatar=current_user_avatar())


@app.route("/refresh")
@login_required
def refresh_summary():
    """Force-recalculate today's summary — called after logging a new run."""
    from web.db import save_cached_summary
    from running_coach.analysis.daily_summary import build_daily_summary

    uid      = current_user_id()
    profile  = _get_profile(uid)
    if not profile:
        return redirect(url_for("dashboard"))

    # Avoid repeatedly hitting Garmin if the user presses Sync several times.
    # This is especially important on Render because Garmin can rate-limit the shared IP.
    raw_cache = load_daily_cache_raw(uid) or {}
    last_sync_text = raw_cache.get("health_cached_at") or raw_cache.get("summary_cached_at")
    if last_sync_text and request.args.get("force") != "1":
        try:
            last_sync = datetime.fromisoformat(str(last_sync_text).replace("Z", "+00:00")).replace(tzinfo=None)
            if datetime.now() - last_sync < timedelta(minutes=60):
                mins = int((datetime.now() - last_sync).total_seconds() / 60)
                print(f"[GARMIN SYNC] skipped: synced {mins}m ago", flush=True)
                return redirect(url_for("dashboard") + "?skipped=1")
        except Exception:
            pass

    # v2 cache is append-only; force=True fetches Garmin but does not wipe existing history.
    runs     = _load_runs(uid, force=True)
    feedback = load_feedback(uid)
    feedback, watch_health = _merge_watch_feedback(uid, feedback, force=True)
    coach    = _get_coach(uid, profile)
    analysis = coach.analyze(runs, feedback)

    # Determine what today's schedule says
    import datetime as _dt
    today_name   = _dt.datetime.now().strftime("%A").lower()
    user_schedule = load_schedule(uid)
    planned_type  = user_schedule.get(today_name, "coach")

    from running_coach.ml.models.next_run_predictor import NextRunPredictor
    rec = (coach.predict_next_run(runs, feedback)
           if len(runs) >= NextRunPredictor.MIN_RUNS_FOR_PERSONALISATION
           else coach.recommend(analysis))

    summary = build_daily_summary(runs, profile, analysis, rec, feedback)
    save_cached_summary(uid, summary)  # overwrites today's cache
    return redirect(url_for("dashboard"))


@app.route("/schedule", methods=["GET", "POST"])
@login_required
def schedule():
    uid = current_user_id()
    profile = _get_profile(uid)
    if not profile:
        return redirect(url_for("setup"))

    if request.method == "POST":
        from web.db import DAYS, VALID_TYPES
        sched = {}
        for day in DAYS:
            val = request.form.get(day, "coach")
            sched[day] = val if val in VALID_TYPES else "coach"
        save_schedule(uid, sched)
        return redirect(url_for("schedule"))

    sched = load_schedule(uid)
    return render_template("schedule.html",
        profile=profile,
        schedule=sched,
        user_name=current_user_name(),
        user_avatar=current_user_avatar(),
    )


@app.route("/api/sync-status")
@login_required
def api_sync_status():
    """Returns whether a sync is available or still in cooldown."""
    uid = current_user_id()
    try:
        from web.db import load_cached_summary
        raw_cache = load_cached_summary(uid) or {}
        last_sync_text = raw_cache.get("health_cached_at") or raw_cache.get("summary_cached_at")
        if last_sync_text:
            last_sync = datetime.fromisoformat(str(last_sync_text).replace("Z", "+00:00")).replace(tzinfo=None)
            diff_minutes = int((datetime.now() - last_sync).total_seconds() / 60)
            if diff_minutes < 60:
                remaining = 60 - diff_minutes
                return jsonify({
                    "can_sync": False,
                    "minutes_ago": diff_minutes,
                    "wait_minutes": remaining,
                    "message": f"Already synced {diff_minutes} minute{'s' if diff_minutes != 1 else ''} ago. Next sync available in {remaining} minute{'s' if remaining != 1 else ''}.",
                })
    except Exception:
        pass
    return jsonify({"can_sync": True})


@app.route("/api/health")
def api_health():
    """Lightweight health check for UptimeRobot keep-alive pings.
    Returns 200 immediately — no DB or Garmin calls.
    Point UptimeRobot at /api/health every 14 minutes to prevent Render cold starts.
    """
    return "", 200


@app.route("/api/status")
def api_status():
    uid = current_user_id()
    return jsonify({
        "logged_in": bool(uid),
        "user_id":   uid,
        "database":  USE_DB,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5000)
