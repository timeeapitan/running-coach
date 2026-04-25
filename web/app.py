"""
Running Coach — Flask web application.

Routes:
  GET  /          Dashboard: status + next run + Garmin steps + PRs
  GET  /history   Run history + pace trend + zone 2 drift + weekly chart
  GET  /insights  Injury risk + race predictor
  GET  /log       Feedback form
  POST /log       Save feedback
  GET  /race      Race goal + periodised training plan
  POST /race      Save race goal
  GET  /setup     Profile editor
  POST /setup     Save profile
  GET  /api/status JSON health check
"""

import json, os, sys
from datetime import datetime, timedelta
from flask import Flask, render_template, request, redirect, url_for, jsonify, session

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from running_coach.schemas.profile  import RunnerProfile
from running_coach.schemas.feedback import ManualFeedback
from running_coach.coaching.coach   import RunningCoach
from running_coach.ml.models.next_run_predictor import NextRunPredictor
from running_coach.analysis.insights import (
    zone2_drift, injury_risk, predict_race_time, generate_race_plan
)

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "running-coach-dev-key")

DATA_DIR = os.path.join(ROOT, "user_data")
os.makedirs(DATA_DIR, exist_ok=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_dir(u):
    safe = "".join(c for c in u.lower() if c.isalnum() or c == "_")
    d = os.path.join(DATA_DIR, safe)
    os.makedirs(os.path.join(d, "models"), exist_ok=True)
    return d

def _profile_path(u):  return os.path.join(_user_dir(u), "profile.json")
def _model_dir(u):     return os.path.join(_user_dir(u), "models")
def _token_path(u):    return os.path.join(_user_dir(u), "strava_token.json")
def _feedback_path(u): return os.path.join(_user_dir(u), "feedback.json")
def _current_user():   return session.get("user", "me")

def _load_profile(u):
    p = _profile_path(u)
    return RunnerProfile.load(p) if os.path.exists(p) else None

def _load_feedback(u) -> dict:
    p = _feedback_path(u)
    if not os.path.exists(p):
        return {}
    with open(p) as f:
        raw = json.load(f)
    result = {}
    for key, val in raw.items():
        val["date"] = datetime.fromisoformat(val["date"])
        result[key] = ManualFeedback(**{k: v for k, v in val.items()
                                        if k in ManualFeedback.__dataclass_fields__})
    return result

def _save_feedback(u, feedback: dict):
    out = {}
    for key, fb in feedback.items():
        out[key] = {
            "date": fb.date.isoformat(), "rpe": fb.rpe, "mood": fb.mood,
            "sleep_hours": fb.sleep_hours, "sleep_quality": fb.sleep_quality,
            "hrv_ms": fb.hrv_ms, "pain_flag": fb.pain_flag,
            "pain_location": fb.pain_location, "notes": fb.notes,
        }
        out[key] = {k: v for k, v in out[key].items() if v is not None}
        out[key]["date"] = fb.date.isoformat()
        out[key]["pain_flag"] = fb.pain_flag
    with open(_feedback_path(u), "w") as f:
        json.dump(out, f, indent=2)

def _load_runs(u):
    tp = _token_path(u)
    if os.path.exists(tp):
        from running_coach.parsers.strava import StravaParser
        try:
            return StravaParser(tp).fetch_runs()
        except Exception:
            pass
    return []

def _get_coach(u, profile):
    return RunningCoach(profile, model_dir=_model_dir(u))

def _fmt_pace(pace_float):
    if not pace_float: return "—"
    m = int(pace_float)
    s = int((pace_float % 1) * 60)
    return f"{m}:{s:02d}/km"


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    u        = _current_user()
    profile  = _load_profile(u)
    if not profile:
        return redirect(url_for("setup"))

    runs     = _load_runs(u)
    feedback = _load_feedback(u)
    coach    = _get_coach(u, profile)

    if not runs:
        return render_template("dashboard.html", profile=profile,
            error="No runs found. Connect Strava or complete setup.", no_runs=True)

    if len(runs) >= 10 and not coach.trainer.fatigue_predictor.is_trained:
        coach.train_models(runs)

    analysis = coach.analyze(runs, feedback)
    rec = (coach.predict_next_run(runs, feedback)
           if len(runs) >= NextRunPredictor.MIN_RUNS_FOR_PERSONALISATION
           else coach.recommend(analysis))

    return render_template("dashboard.html",
        profile=profile, analysis=analysis, recommendation=rec,
        zones=profile.get_hr_zones(), prs=_compute_prs(runs),
        weeks_to_race=profile.weeks_to_race(), run_count=len(runs),
        ml_active=coach.trainer.fatigue_predictor.is_trained,
        no_runs=False, error=None,
    )


@app.route("/history")
def history():
    u       = _current_user()
    profile = _load_profile(u)
    if not profile: return redirect(url_for("setup"))

    runs = _load_runs(u)
    if not runs:
        return render_template("history.html", profile=profile,
            runs=[], chart_data="[]", weekly="[]", drift={})

    recent = sorted(runs, key=lambda r: r.date, reverse=True)[:60]
    chart_data = [
        {"date": r.date.strftime("%d %b"), "pace": round(r.avg_pace_min_per_km,2)
         if r.avg_pace_min_per_km else None,
         "hr": int(r.avg_hr) if r.avg_hr else None,
         "distance": round(r.distance_km,1)}
        for r in reversed(recent)
    ]
    weekly  = _weekly_summary(runs, 8)
    drift   = zone2_drift(runs, profile)

    return render_template("history.html",
        profile=profile, runs=recent,
        chart_data=json.dumps(chart_data),
        weekly=json.dumps(weekly),
        drift=drift, zones=profile.get_hr_zones(),
    )


@app.route("/insights")
def insights():
    u       = _current_user()
    profile = _load_profile(u)
    if not profile: return redirect(url_for("setup"))

    runs = _load_runs(u)
    risk = injury_risk(runs, profile) if runs else {"score":0,"level":"unknown","factors":{},"advice":[]}

    race_pred = None
    if profile.race_distance_km and runs:
        race_pred = predict_race_time(runs, profile, profile.race_distance_km)

    return render_template("insights.html",
        profile=profile, risk=risk, race_pred=race_pred,
        runs_count=len(runs),
    )


@app.route("/log", methods=["GET", "POST"])
def log_feedback():
    u       = _current_user()
    profile = _load_profile(u)
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
        feedback = _load_feedback(u)
        feedback[date] = fb
        _save_feedback(u, feedback)
        return redirect(url_for("dashboard"))

    feedback = _load_feedback(u)
    recent   = sorted(feedback.values(), key=lambda x: x.date, reverse=True)[:7]
    return render_template("log.html", profile=profile,
        recent_feedback=recent, today=datetime.now().date().isoformat())


@app.route("/race", methods=["GET", "POST"])
def race():
    u       = _current_user()
    profile = _load_profile(u)
    if not profile: return redirect(url_for("setup"))

    if request.method == "POST":
        f = request.form
        profile.race_date              = f.get("race_date") or None
        profile.race_distance_km       = float(f.get("race_distance_km") or 0) or None
        profile.race_goal_time_minutes = float(f.get("race_goal_minutes") or 0) or None
        profile.save(_profile_path(u))
        return redirect(url_for("race"))

    runs = _load_runs(u)
    plan = generate_race_plan(profile, runs) if profile.race_date else None
    pred = (predict_race_time(runs, profile, profile.race_distance_km)
            if profile.race_distance_km and runs else None)

    return render_template("race.html",
        profile=profile, plan=plan, pred=pred,
        weeks_to_race=profile.weeks_to_race(), fmt_pace=_fmt_pace,
    )


@app.route("/setup", methods=["GET", "POST"])
def setup():
    u = _current_user()
    if request.method == "POST":
        f = request.form
        def _i(k): v=f.get(k,"").strip(); return int(v) if v.isdigit() else None
        def _f(k):
            v=f.get(k,"").strip()
            try: return float(v)
            except: return None
        from running_coach.schemas.enums import FitnessLevel
        p = RunnerProfile(
            name=f.get("name","Runner").strip(), age=_i("age"),
            max_hr=_i("max_hr"), resting_hr=_i("resting_hr"),
            runs_per_week=_i("runs_per_week") or 3,
            fitness_level=FitnessLevel(f.get("fitness_level","intermediate")),
            goal_weekly_km=_f("goal_weekly_km"),
        )
        p.save(_profile_path(u))
        return redirect(url_for("dashboard"))

    return render_template("setup.html", profile=_load_profile(u))


@app.route("/api/status")
def api_status():
    u    = _current_user()
    prof = _load_profile(u)
    return jsonify({"profile": bool(prof), "strava": os.path.exists(_token_path(u))})


# ── Business logic ────────────────────────────────────────────────────────────

def _compute_prs(runs):
    if not runs: return {}
    with_pace = [r for r in runs if r.avg_pace_min_per_km]
    with_elev = [r for r in runs if r.elevation_gain_m]
    return {
        "longest_km":   round(max(r.distance_km for r in runs), 1),
        "fastest_pace": min(r.avg_pace_min_per_km for r in with_pace) if with_pace else None,
        "fastest_pace_str": _fmt_pace(min(r.avg_pace_min_per_km for r in with_pace)) if with_pace else None,
        "most_elev":    int(max(r.elevation_gain_m for r in with_elev)) if with_elev else None,
        "total_runs":   len(runs),
        "total_km":     round(sum(r.distance_km for r in runs), 1),
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
            "label":  start.strftime("%d %b"),
            "km":     round(sum(r.distance_km for r in wr), 1),
            "runs":   len(wr),
            "avg_hr": round(sum(hrs)/len(hrs), 0) if hrs else 0,
        })
    return result


if __name__ == "__main__":
    app.run(debug=True, port=5000)
