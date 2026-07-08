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
    load_profile, save_profile, load_strava_token, save_strava_token,
    load_feedback, save_feedback_entry, list_users, load_athlete_info, USE_DB,
    load_cached_summary, save_cached_summary, load_daily_cache_raw,
    load_cached_runs, save_cached_runs, invalidate_runs_cache,
    load_cached_watch_health, save_cached_watch_health, invalidate_watch_health_cache,
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
        save_strava_token(username, token, display_name=name, athlete_data={})
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

    try:
        # Verify that login + activities endpoint works before saving credentials.
        GarminConnectParser(email, password).fetch_runs(max_runs=1)
    except Exception as e:
        import traceback
        print("GARMIN LOGIN ERROR:", traceback.format_exc(), flush=True)
        return render_template("login.html",
            host_credentials=host_credentials_set(),
            secret_session=secret_session_set(),
            error=f"Could not connect to Garmin: {e}")

    token = {
        "provider": "garmin",
        "email": email,
        "password": password,
    }

    try:
        save_strava_token(username, token, display_name=name, athlete_data={})
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

    invalidate_runs_cache(username)
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

def _get_garmin_parser(uid):
    token = load_strava_token(uid) or {}
    from running_coach.parsers.garmin_connect import GarminConnectParser

    # Preferred Render mode: resume saved Garmin session from Secret Files.
    # This must be checked first so normal page loads never call Garmin login.
    if GarminConnectParser.secret_session_available():
        return GarminConnectParser.from_secret_session()

    # Local/manual fallback only. This can hit Garmin login limits, so avoid it on Render.
    email = token.get("email")
    password = token.get("password")
    if email and password:
        return GarminConnectParser(email, password)

    raise RuntimeError("Missing Garmin session. Upload oauth1_token.json and oauth2_token.json as Render Secret Files.")

def _load_runs(uid, force=False, auto_fetch=False):
    """Load runs with a once-per-day auto sync.

    Normal tabs read cache only. The Dashboard can pass auto_fetch=True:
    if today's runs cache is missing, the app fetches from Garmin once, saves
    runs_cache, and later page loads use the cache for the rest of the day.
    /refresh still forces a fresh Garmin fetch.
    """
    cached = load_cached_runs(uid)
    if cached is not None and not force:
        return _deserialize_runs(cached)
    if cached is None and not force and not auto_fetch:
        return []

    try:
        print(f"[GARMIN SYNC] fetching runs for {uid} (force={force}, auto_fetch={auto_fetch})", flush=True)
        runs = _get_garmin_parser(uid).fetch_runs(max_runs=200)
        print(f"[GARMIN SYNC] parsed {len(runs)} Garmin runs", flush=True)

        if runs:
            saved = save_cached_runs(uid, _serialize_runs(runs))
            print(f"[GARMIN SYNC] runs_cache save result: {saved}", flush=True)
            return runs

        # Do not wipe a previously good cache with an empty Garmin response.
        print("[GARMIN SYNC] Garmin returned 0 parsed runs; keeping existing cache if available", flush=True)
        return _deserialize_runs(cached) if cached is not None else []
    except Exception as e:
        import traceback
        print("[GARMIN SYNC] fetch failed:", repr(e), flush=True)
        print(traceback.format_exc(), flush=True)
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

    try:
        watch = _get_garmin_parser(uid).fetch_daily_health(date_obj) or {}
        if watch:
            save_cached_watch_health(uid, date_str, watch)
        return watch
    except Exception as e:
        print("[GARMIN HEALTH] unavailable:", repr(e), flush=True)
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
    runs     = _load_runs(uid, auto_fetch=True)
    feedback = load_feedback(uid)
    feedback, watch_health = _merge_watch_feedback(uid, feedback, auto_fetch=True)
    coach    = _get_coach(uid, profile)

    # No runs yet — still show a profile-based recommendation using rules engine
    is_new_user = len(runs) == 0

    if len(runs) >= 10 and not coach.trainer.fatigue_predictor.is_trained:
        coach.train_models(runs)

    # Auto-detect fitness level (only when we have data)
    fitness_result = {"changed": False, "reason": "", "level": profile.fitness_level}
    if runs:
        fitness_result = coach.detect_and_update_fitness_level(runs)
        if fitness_result["changed"]:
            _save_profile_obj(uid, coach.profile)

    analysis = coach.analyze(runs, feedback)
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
        user_name=current_user_name(), user_avatar=current_user_avatar())


@app.route("/history")
@login_required
def history():
    uid     = current_user_id()
    profile = _get_profile(uid)
    if not profile: return redirect(url_for("setup"))
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
    last_sync_text = raw_cache.get("watch_cached_at") or raw_cache.get("summary_cached_at")
    if last_sync_text and request.args.get("force") != "1":
        try:
            last_sync = datetime.fromisoformat(str(last_sync_text).replace("Z", "+00:00")).replace(tzinfo=None)
            if datetime.now() - last_sync < timedelta(minutes=30):
                print("[GARMIN SYNC] skipped: refreshed less than 30 minutes ago", flush=True)
                return redirect(url_for("dashboard"))
        except Exception:
            pass

    invalidate_runs_cache(uid)  # force fresh Garmin fetch now
    invalidate_watch_health_cache(uid)  # force fresh watch health fetch now
    runs     = _load_runs(uid, force=True)
    feedback = load_feedback(uid)
    feedback, watch_health = _merge_watch_feedback(uid, feedback, force=True)
    coach    = _get_coach(uid, profile)
    analysis = coach.analyze(runs, feedback)

    from running_coach.ml.models.next_run_predictor import NextRunPredictor
    rec = (coach.predict_next_run(runs, feedback)
           if len(runs) >= NextRunPredictor.MIN_RUNS_FOR_PERSONALISATION
           else coach.recommend(analysis))

    summary = build_daily_summary(runs, profile, analysis, rec, feedback)
    save_cached_summary(uid, summary)  # overwrites today's cache
    return redirect(url_for("dashboard"))


@app.route("/api/cache-status")
@login_required
def api_cache_status():
    uid = current_user_id()
    cached = load_cached_runs(uid)
    raw_daily = load_daily_cache_raw(uid) or {}
    return jsonify({
        "user_id": uid,
        "runs_cache_count": len(cached or []),
        "has_runs_cache": cached is not None,
        "has_daily_cache": bool(raw_daily),
        "daily_cache_keys": sorted(list(raw_daily.keys())) if isinstance(raw_daily, dict) else [],
        "watch_cached_at": raw_daily.get("watch_cached_at") if isinstance(raw_daily, dict) else None,
        "summary_cached_at": raw_daily.get("summary_cached_at") if isinstance(raw_daily, dict) else None,
    })


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
