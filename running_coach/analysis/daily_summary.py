"""
Daily summary — generates the "Today's briefing" card shown on the dashboard.

Key change: smart recovery logic. After a run, minimum rest is computed
from the intensity (HR) and volume (distance) of the last run.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict

from ..schemas import NormalizedRun, RunnerProfile, AnalysisResult, WorkoutRecommendation
from ..schemas.feedback import ManualFeedback
from .insights import injury_risk


def _smart_recovery_days(last_run: NormalizedRun, profile: RunnerProfile) -> int:
    """
    Compute minimum rest days needed after a run.

    Factors:
      - HR intensity relative to max HR
      - Distance relative to profile average
      - Runs per week target (higher frequency = shorter rest windows)

    Returns 0, 1, or 2 days minimum rest.
    """
    if not last_run:
        return 0

    max_hr = profile.max_hr or 185

    # HR intensity score 0-1
    hr_intensity = 0.0
    if last_run.avg_hr:
        hr_intensity = min(1.0, last_run.avg_hr / max_hr)

    # Distance score 0-1 (relative to a "typical" run of 6 km)
    typical_km  = 6.0
    dist_score  = min(1.0, last_run.distance_km / (typical_km * 2))

    # Combined effort score
    effort = (hr_intensity * 0.6) + (dist_score * 0.4)

    # Adjust for runs per week — more frequent runner needs shorter gaps
    rpw = max(1, profile.runs_per_week)
    if rpw >= 5:
        threshold_hard = 0.85
        threshold_mod  = 0.70
    elif rpw >= 3:
        threshold_hard = 0.80
        threshold_mod  = 0.65
    else:
        threshold_hard = 0.75
        threshold_mod  = 0.60

    if effort >= threshold_hard:
        return 2   # hard session — 2 rest days
    elif effort >= threshold_mod:
        return 1   # moderate — 1 rest day
    return 0       # easy run — can run tomorrow


def build_daily_summary(
    runs:           List[NormalizedRun],
    profile:        RunnerProfile,
    analysis:       AnalysisResult,
    recommendation: WorkoutRecommendation,
    feedback:       Dict[str, ManualFeedback] = None,
) -> Dict:
    now      = datetime.now()
    feedback = feedback or {}

    # ── Last run + recovery ───────────────────────────────────────────────────
    if runs:
        sorted_runs   = sorted(runs, key=lambda r: r.date, reverse=True)
        last_run      = sorted_runs[0]
        days_inactive = max(0, (now - last_run.date).days)
        min_rest_days = _smart_recovery_days(last_run, profile)
    else:
        last_run      = None
        days_inactive = 999
        min_rest_days = 0

    in_recovery = days_inactive < min_rest_days

    # ── Streak ────────────────────────────────────────────────────────────────
    streak_days = 0
    if runs:
        run_dates = {r.date.date() for r in runs}
        d = now.date()
        while d in run_dates:
            streak_days += 1
            d -= timedelta(days=1)
        if streak_days == 0:
            d = now.date() - timedelta(days=1)
            while d in run_dates:
                streak_days += 1
                d -= timedelta(days=1)

    # ── This week ─────────────────────────────────────────────────────────────
    week_start = now - timedelta(days=now.weekday())
    week_runs  = [r for r in runs if r.date >= week_start] if runs else []
    week_km    = round(sum(r.distance_km for r in week_runs), 1)
    goal_km    = profile.goal_weekly_km
    week_pct   = round((week_km / goal_km) * 100) if goal_km else None

    # ── Injury risk ───────────────────────────────────────────────────────────
    risk       = injury_risk(runs, profile) if runs else {"score": 0, "level": "unknown"}
    risk_level = risk["level"]
    risk_score = risk["score"]

    # ── Recommendation label ──────────────────────────────────────────────────
    from ..schemas.enums import WorkoutType
    type_labels = {
        WorkoutType.EASY:     "Easy run",
        WorkoutType.MODERATE: "Aerobic run",
        WorkoutType.TEMPO:    "Tempo run",
        WorkoutType.INTERVAL: "Interval session",
        WorkoutType.LONG_RUN: "Long run",
        WorkoutType.RECOVERY: "Recovery run",
        WorkoutType.REST:     "Rest day",
    }
    rec_type     = type_labels.get(recommendation.workout_type, "Run")
    rec_distance = (f"{recommendation.target_distance_km:.1f} km"
                    if recommendation.target_distance_km else None)

    # ── Status + headline ─────────────────────────────────────────────────────

    # Smart recovery overrides everything else
    if in_recovery and last_run:
        days_to_next = min_rest_days - days_inactive
        intensity_label = _intensity_label(last_run, profile)
        if days_inactive == 0:
            status   = "rest"
            headline = f"Good work today — rest now."
            subline  = (f"Your last run was {intensity_label}. "
                        f"{'Tomorrow' if min_rest_days == 1 else f'In {min_rest_days} days'} "
                        f"you'll be ready for the next session.")
        else:
            status   = "rest"
            headline = (f"Rest day — next run in "
                        f"{'1 day' if days_to_next == 1 else f'{days_to_next} days'}.")
            subline  = (f"Your last run was {intensity_label}. "
                        f"Recovery is part of training.")

    elif risk_level == "high" or risk_score >= 70:
        status   = "warning"
        headline = "Slow down — your body needs a break."
        subline  = _risk_subline(risk_score, days_inactive)

    elif recommendation.workout_type == WorkoutType.REST:
        status   = "rest"
        headline = "Rest day today — well earned."
        subline  = _rest_subline(streak_days, analysis.consistency_score)

    elif days_inactive >= 14:
        status   = "warning"
        headline = f"It's been {days_inactive} days since your last run."
        subline  = "Starting back easy is the right call — your fitness holds longer than you think."

    elif days_inactive >= 7:
        status   = "gentle"
        headline = f"{days_inactive} days since your last run."
        subline  = "A gentle session today will get you back on track."

    elif analysis.readiness_score >= 70 and analysis.fatigue_score < 40:
        status   = "go"
        headline = "You're ready — great day to run."
        subline  = _go_subline(week_km, goal_km, week_pct, streak_days)

    else:
        status   = "gentle"
        headline = _gentle_headline(analysis.readiness_score, analysis.fatigue_score)
        subline  = _gentle_subline(week_km, goal_km, week_pct)

    return {
        "status":        status,
        "headline":      headline,
        "subline":       subline,
        "streak_days":   streak_days,
        "days_inactive": days_inactive,
        "min_rest_days": min_rest_days,
        "in_recovery":   in_recovery,
        "week_km":       week_km,
        "week_goal_km":  goal_km,
        "week_pct":      week_pct,
        "risk_level":    risk_level,
        "risk_score":    risk_score,
        "rec_type":      rec_type,
        "rec_distance":  rec_distance,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _intensity_label(run: NormalizedRun, profile: RunnerProfile) -> str:
    if not run.avg_hr:
        return "a moderate effort"
    max_hr = profile.max_hr or 185
    pct    = run.avg_hr / max_hr
    if pct >= 0.88:   return f"a hard effort ({int(run.avg_hr)} bpm)"
    elif pct >= 0.78: return f"a moderate-hard effort ({int(run.avg_hr)} bpm)"
    elif pct >= 0.68: return f"a moderate effort ({int(run.avg_hr)} bpm)"
    return f"an easy effort ({int(run.avg_hr)} bpm)"

def _risk_subline(risk_score: float, days_inactive: int) -> str:
    if days_inactive == 0:
        return "You ran today — take tomorrow fully off and let your body recover."
    return (f"Injury risk is {risk_score:.0f}/100. "
            "Rest, stretch, and come back easier in 1–2 days.")

def _rest_subline(streak: int, consistency: float) -> str:
    if streak >= 3:
        return f"{streak} days in a row — rest is part of training, not a break from it."
    if consistency >= 70:
        return "Your consistency is strong. Recovery today means better runs tomorrow."
    return "Use today to stretch, hydrate, and sleep well."

def _go_subline(week_km, goal_km, week_pct, streak) -> str:
    if goal_km and week_pct is not None:
        remaining = max(0, round(goal_km - week_km, 1))
        if week_pct >= 100:
            return f"Weekly goal hit — {week_km} km done. Anything extra is a bonus."
        return f"{week_km} km done this week, {remaining} km to go."
    if streak >= 3:
        return f"{streak}-day streak — keep the momentum going."
    return "Fatigue is low and readiness is high — make the most of it."

def _gentle_headline(readiness: float, fatigue: float) -> str:
    if fatigue >= 55:   return "Feeling the effort from recent runs."
    if readiness < 50:  return "Energy is a bit low today."
    return "A moderate day — listen to your body."

def _gentle_subline(week_km, goal_km, week_pct) -> str:
    if goal_km and week_pct is not None and week_pct < 50:
        remaining = round(goal_km - week_km, 1)
        return (f"{week_km} km done, {remaining} km left. "
                "An easy run still counts toward your goal.")
    return "Keep the effort controlled and enjoy the run."
