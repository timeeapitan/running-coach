"""
Running Coach — Flask web application with Strava authentication.
"""

import json, os, sys, tempfile, time
from datetime import datetime, timedelta
from flask import (Flask, render_template, request, redirect,
                   url_for, jsonify, session, abort)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from web.auth import (
    login_required, current_user_id, current_user_name, current_user_avatar,
    build_auth_url, exchange_code, refresh_token, is_configured, CLIENT_ID
)
from web.db import (
    load_profile, save_profile, load_strava_token, save_strava_token,
    load_feedback, save_feedback_entry, list_users, load_athlete_info, USE_DB,
    load_cached_summary, save_cached_summary,
    load_cached_runs, save_cached_runs, invalidate_runs_cache,
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
    return render_template("login.html", configured=is_configured(),
                           client_id=CLIENT_ID)


@app.route("/auth/callback-start")
def auth_callback_start():
    """Redirect the browser to Strava auth page."""
    return redirect(build_auth_url())


@app.route("/auth/callback")
def auth_callback():
    """Strava redirects here after the user clicks Allow."""
    error = request.args.get("error")
    if error:
        return render_template("login.html", configured=is_configured(),
                               client_id=CLIENT_ID,
                               error="Strava authorization was denied. Please try again.")

    code = request.args.get("code")
    if not code:
        return redirect(url_for("login"))

    try:
        token_data = exchange_code(code)
    except Exception as e:
        import traceback
        print("STRAVA EXCHANGE ERROR:", traceback.format_exc())
        return render_template("login.html", configured=is_configured(),
                               client_id=CLIENT_ID,
                               error=f"Could not connect to Strava: {e}")

    athlete     = token_data.get("athlete", {})
    athlete_id  = str(athlete.get("id", ""))
    if not athlete_id:
        return render_template("login.html", configured=is_configured(),
                               client_id=CLIENT_ID,
                               error="Could not get athlete ID from Strava.")

    first  = athlete.get("firstname", "")
    last   = athlete.get("lastname",  "")
    name   = f"{first} {last}".strip() or athlete_id
    avatar = athlete.get("profile_medium", "")

    # Strip athlete info from token before storing
    clean_token = {k: v for k, v in token_data.items() if k != "athlete"}
    clean_token["client_id"]     = os.environ.get("STRAVA_CLIENT_ID", "")
    clean_token["client_secret"] = os.environ.get("STRAVA_CLIENT_SECRET", "")

    try:
        save_strava_token(athlete_id, clean_token,
                          display_name=name, athlete_data=athlete)
    except Exception as e:
        import traceback
        print("SUPABASE SAVE ERROR:", traceback.format_exc())
        return render_template("login.html", configured=is_configured(),
                               client_id=CLIENT_ID,
                               error=f"Database error: {e} — Have you run setup_db.sql in Supabase?")

    # Create a default profile if this is their first login
    if not load_profile(athlete_id):
        from running_coach.schemas.enums import FitnessLevel
        import dataclasses
        p = RunnerProfile(name=name, fitness_level=FitnessLevel.INTERMEDIATE,
                          runs_per_week=3)
        d = dataclasses.asdict(p)
        d["fitness_level"] = p.fitness_level.value
        save_profile(athlete_id, d)

    # Set session
    session.permanent   = True
    session["user_id"]  = athlete_id
    session["user_name"]= name
    session["user_avatar"] = avatar

    return redirect(url_for("dashboard"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ── Helpers ───────────────────────────────────────────────────────────────────

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
            ))
        except Exception:
            pass
    return runs

def _load_runs(uid):
    # Try cache first — avoids Strava API call on every page
    cached = load_cached_runs(uid)
    if cached is not None:
        return _deserialize_runs(cached)

    # Cache miss — fetch from Strava
    token = load_strava_token(uid)
    if not token:
        return []

    if token.get("expires_at", 0) < time.time() + 300:
        try:
            token = refresh_token(token)
            save_strava_token(uid, token)
        except Exception:
            pass

    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w")
    json.dump(token, tmp); tmp.close()
    try:
        from running_coach.parsers.strava import StravaParser
        runs = StravaParser(tmp.name).fetch_runs()
        # Store in cache for subsequent page loads
        save_cached_runs(uid, _serialize_runs(runs))
        return runs
    except Exception:
        return []
    finally:
        os.unlink(tmp.name)

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


# ── App pages (all protected) ─────────────────────────────────────────────────

@app.route("/")
@login_required
def dashboard():
    uid      = current_user_id()
    profile  = _get_profile(uid)
    if not profile:
        return redirect(url_for("setup"))

    runs     = _load_runs(uid)
    feedback = load_feedback(uid)
    coach    = _get_coach(uid, profile)

    if not runs:
        return render_template("dashboard.html",
            profile=profile, error="No runs found on Strava yet.",
            no_runs=True, user_name=current_user_name(),
            user_avatar=current_user_avatar())

    if len(runs) >= 10 and not coach.trainer.fatigue_predictor.is_trained:
        coach.train_models(runs)

    # Auto-detect fitness level (change 12)
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

    return render_template("dashboard.html",
        profile=profile, analysis=analysis, recommendation=rec,
        zones=profile.get_hr_zones(), prs=_compute_prs(runs),
        weeks_to_race=profile.weeks_to_race(), run_count=len(runs),
        ml_active=coach.trainer.fatigue_predictor.is_trained,
        summary=summary,
        fitness_result=fitness_result,
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
    recent   = sorted(feedback.values(), key=lambda x: x.date, reverse=True)[:7]
    return render_template("log.html", profile=profile,
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

    runs     = _load_runs(uid)
    feedback = load_feedback(uid)
    coach    = _get_coach(uid, profile)
    analysis = coach.analyze(runs, feedback)

    from running_coach.ml.models.next_run_predictor import NextRunPredictor
    rec = (coach.predict_next_run(runs, feedback)
           if len(runs) >= NextRunPredictor.MIN_RUNS_FOR_PERSONALISATION
           else coach.recommend(analysis))

    summary = build_daily_summary(runs, profile, analysis, rec, feedback)
    save_cached_summary(uid, summary)  # overwrites today's cache
    invalidate_runs_cache(uid)  # force fresh Strava fetch next open

    return redirect(url_for("dashboard"))


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
