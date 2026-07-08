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


def _typical_run_km(runs: List[NormalizedRun]) -> float:
    """Return a robust typical run distance from recent runs."""
    recent = sorted(runs, key=lambda r: r.date, reverse=True)[:12]
    distances = sorted([r.distance_km for r in recent if r.distance_km])
    if not distances:
        return 6.0
    mid = len(distances) // 2
    return distances[mid] if len(distances) % 2 else (distances[mid - 1] + distances[mid]) / 2


def _recovery_context(
    last_run: NormalizedRun,
    profile: RunnerProfile,
    runs: List[NormalizedRun],
    feedback: Dict[str, ManualFeedback],
    now: datetime,
) -> Dict:
    """
    Decide how many full recovery days are needed after the latest run.

    This is intentionally conservative: after a hard effort, the app should not
    suggest another run too soon. Garmin health values, when available, can add
    an extra recovery day.
    """
    if not last_run:
        return {"min_rest_days": 0, "effort_score": 0, "reasons": []}

    reasons = []
    max_hr = profile.max_hr or 185

    hr_intensity = 0.0
    if last_run.avg_hr:
        hr_intensity = min(1.0, last_run.avg_hr / max_hr)
        if hr_intensity >= 0.88:
            reasons.append("high heart-rate effort")
        elif hr_intensity >= 0.78:
            reasons.append("moderate-hard heart-rate effort")

    typical_km = max(3.0, _typical_run_km(runs))
    dist_ratio = last_run.distance_km / typical_km if typical_km else 1.0
    dist_score = min(1.0, dist_ratio / 2.0)
    if dist_ratio >= 1.6:
        reasons.append("longer than your usual run")
    elif dist_ratio >= 1.25:
        reasons.append("above your usual distance")

    # If HR is missing, distance and duration still carry the decision.
    effort = (hr_intensity * 0.6) + (dist_score * 0.4) if hr_intensity else dist_score

    rpw = max(1, profile.runs_per_week)
    if rpw >= 5:
        threshold_hard, threshold_mod = 0.85, 0.70
    elif rpw >= 3:
        threshold_hard, threshold_mod = 0.80, 0.65
    else:
        threshold_hard, threshold_mod = 0.75, 0.60

    if effort >= threshold_hard:
        min_rest_days = 2
    elif effort >= threshold_mod:
        min_rest_days = 1
    else:
        min_rest_days = 0

    # Same-day feedback/watch data can add caution.
    today_fb = feedback.get(now.date().isoformat())
    if today_fb:
        if today_fb.pain_flag:
            min_rest_days = max(min_rest_days, 2)
            reasons.append("pain was logged")
        if today_fb.sleep_quality is not None and today_fb.sleep_quality <= 2:
            min_rest_days = max(min_rest_days, 1)
            reasons.append("low sleep quality")
        if today_fb.hrv_ms is not None and today_fb.hrv_ms < 35:
            min_rest_days = max(min_rest_days, 1)
            reasons.append("low HRV")

    if not reasons:
        reasons.append("recent training load")

    return {
        "min_rest_days": min_rest_days,
        "effort_score": round(effort * 100),
        "reasons": reasons[:3],
    }


def _smart_recovery_days(last_run: NormalizedRun, profile: RunnerProfile) -> int:
    """Backward-compatible wrapper used by older callers/tests."""
    return _recovery_context(last_run, profile, [last_run] if last_run else [], {}, datetime.now())["min_rest_days"]


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
        days_inactive = max(0, (now.date() - last_run.date.date()).days)
        recovery_ctx  = _recovery_context(last_run, profile, runs, feedback, now)
        min_rest_days = recovery_ctx["min_rest_days"]
    else:
        last_run      = None
        days_inactive = 999
        min_rest_days = 0
        recovery_ctx  = {"min_rest_days": 0, "effort_score": 0, "reasons": []}

    in_recovery = days_inactive < min_rest_days
    days_until_next_run = max(0, min_rest_days - days_inactive)
    next_run_date = (now.date() + timedelta(days=days_until_next_run)) if last_run else now.date()
    should_run_today = not in_recovery

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
        days_to_next = days_until_next_run
        intensity_label = _intensity_label(last_run, profile)
        reason_text = ", ".join(recovery_ctx.get("reasons", [])[:2])
        if days_inactive == 0:
            status   = "rest"
            headline = "Good work today — recover now."
            subline  = (f"Your last run was {intensity_label}. "
                        f"Next run: {'tomorrow' if min_rest_days == 1 else f'in {min_rest_days} days'}.")
        else:
            status   = "rest"
            headline = (f"No run today — next run "
                        f"{'tomorrow' if days_to_next == 1 else f'in {days_to_next} days'}.")
            subline  = (f"Reason: {reason_text}. Recovery is part of training.")

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
        "should_run_today": should_run_today,
        "days_until_next_run": days_until_next_run,
        "next_run_date": next_run_date.isoformat(),
        "recovery_reasons": recovery_ctx.get("reasons", []),
        "last_run_effort_score": recovery_ctx.get("effort_score", 0),
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
