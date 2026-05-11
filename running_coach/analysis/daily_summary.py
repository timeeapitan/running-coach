"""
Daily summary — generates the "Today's briefing" card shown on the dashboard.

Combines:
  - Days since last run (streak or gap warning)
  - This week vs goal progress
  - Injury risk level
  - Today's recommendation headline
  - A short motivational or cautionary message
"""

from datetime import datetime, timedelta
from typing import List, Optional, Dict

from ..schemas import NormalizedRun, RunnerProfile, AnalysisResult, WorkoutRecommendation
from ..schemas.feedback import ManualFeedback
from .insights import injury_risk


def build_daily_summary(
    runs:           List[NormalizedRun],
    profile:        RunnerProfile,
    analysis:       AnalysisResult,
    recommendation: WorkoutRecommendation,
    feedback:       Dict[str, ManualFeedback] = None,
) -> Dict:
    """
    Returns a dict ready to pass to the dashboard template.

    Keys:
      status        — "rest" | "warning" | "go" | "gentle"
      headline      — one short sentence
      subline       — one supporting sentence
      streak_days   — consecutive days run (0 if resting)
      days_inactive — days since last run (0 if ran today)
      week_km       — km run this week
      week_goal_km  — weekly km goal (None if not set)
      week_pct      — % of weekly goal achieved (None if no goal)
      risk_level    — "low" | "moderate" | "high" | "unknown"
      risk_score    — 0-100
      rec_type      — workout type string e.g. "Easy run"
      rec_distance  — e.g. "5.5 km" or None
      days_to_next  — suggested days until next run
    """
    now = datetime.now()
    feedback = feedback or {}

    # ── Days since last run ───────────────────────────────────────────────────
    if runs:
        sorted_runs  = sorted(runs, key=lambda r: r.date, reverse=True)
        last_run     = sorted_runs[0]
        # Clamp to 0 — negative values happen when Strava stores UTC time
        # and the user is in a timezone ahead of UTC (e.g. Romania = UTC+3)
        days_inactive= max(0, (now - last_run.date).days)
    else:
        days_inactive = 999

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
    risk      = injury_risk(runs, profile) if runs else {"score": 0, "level": "unknown"}
    risk_level= risk["level"]
    risk_score= risk["score"]

    # ── Recommendation headline ───────────────────────────────────────────────
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

    # ── Status + headline logic ───────────────────────────────────────────────
    if risk_level == "high" or risk_score >= 70:
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
        "week_km":       week_km,
        "week_goal_km":  goal_km,
        "week_pct":      week_pct,
        "risk_level":    risk_level,
        "risk_score":    risk_score,
        "rec_type":      rec_type,
        "rec_distance":  rec_distance,
    }


# ── Message builders ──────────────────────────────────────────────────────────

def _risk_subline(risk_score: float, days_inactive: int) -> str:
    if days_inactive == 0:
        return "You ran today — take tomorrow fully off and let your body recover."
    return (f"Injury risk is {risk_score:.0f}/100. "
            "Rest, stretch, and come back easier in 1-2 days.")

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
        return f"{week_km} km done this week, {remaining} km to go for your {goal_km} km goal."
    if streak >= 3:
        return f"{streak}-day streak — keep the momentum going."
    return "Fatigue is low and readiness is high — make the most of it."

def _gentle_headline(readiness: float, fatigue: float) -> str:
    if fatigue >= 55:
        return "Feeling the effort from recent runs."
    if readiness < 50:
        return "Energy is a bit low today."
    return "A moderate day — listen to your body."

def _gentle_subline(week_km, goal_km, week_pct) -> str:
    if goal_km and week_pct is not None and week_pct < 50:
        remaining = round(goal_km - week_km, 1)
        return (f"{week_km} km done, {remaining} km left for the week. "
                "An easy run still counts toward your goal.")
    return "Keep the effort controlled and enjoy the run."
