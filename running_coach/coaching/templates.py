"""
Output templates — formats the recommendation as something you can
directly enter on your Garmin watch.
"""

from datetime import datetime
from typing import Optional

from ..schemas import WorkoutRecommendation, AnalysisResult, WorkoutType


def format_full_report(
    recommendation: WorkoutRecommendation,
    analysis: AnalysisResult,
    profile_name: str = "Runner",
) -> str:
    """
    Full terminal report: dashboard + next run + garmin entry instructions.
    """
    sections = [
        _header(profile_name),
        _dashboard(analysis),
        _next_run(recommendation),
        _garmin_entry(recommendation),
    ]
    if analysis.warnings:
        sections.append(_warnings(analysis.warnings))
    return "\n\n".join(sections)


# Alias kept for backward compat
def format_workout_message(recommendation, analysis):
    return format_full_report(recommendation, analysis)


# ------------------------------------------------------------------
# Sections
# ------------------------------------------------------------------

def _header(name: str) -> str:
    now = datetime.now().strftime("%A %d %b %Y, %H:%M")
    return f"{'─'*52}\n  Running Coach — {name}\n  {now}\n{'─'*52}"


def _dashboard(a: AnalysisResult) -> str:
    lines = ["  STATUS DASHBOARD"]
    lines.append(f"  Fatigue      {_bar(a.fatigue_score)}  {a.fatigue_score:.0f}/100")
    lines.append(f"  Consistency  {_bar(a.consistency_score)}  {a.consistency_score:.0f}/100")
    lines.append(f"  Readiness    {_bar(a.readiness_score)}  {a.readiness_score:.0f}/100")
    lines.append("")

    # ATL/CTL/TSB if available
    ff = a.fatigue_factors
    if "atl" in ff and "ctl" in ff:
        tsb  = ff.get("tsb", ff["ctl"] - ff["atl"])
        sign = "+" if tsb >= 0 else ""
        tsb_label = _tsb_label(tsb)
        lines.append(f"  Fatigue (ATL) {ff['atl']:.0f}  |  Fitness (CTL) {ff['ctl']:.0f}  |  Form (TSB) {sign}{tsb:.0f}  {tsb_label}")
        lines.append("")

    lines.append(f"  This week   {a.recent_volume_km:.1f} km   |   "
                 f"4-week avg {a.average_weekly_volume_km:.1f} km   |   "
                 f"Trend: {a.trend}")
    return "\n".join(lines)


def _next_run(r: WorkoutRecommendation) -> str:
    lines = ["  NEXT RUN"]
    if r.is_rest:
        lines.append("  REST DAY — no running today.")
        lines.append(f"  {r.rationale}")
        return "\n".join(lines)

    lines.append(f"  {r.description}")
    lines.append(f"  {r.rationale}")
    lines.append("")
    lines.append("  TARGETS")
    if r.target_distance_km:
        lines.append(f"    Distance  {r.target_distance_km:.1f} km")
    if r.target_duration_minutes:
        lines.append(f"    Duration  ~{int(r.target_duration_minutes)} min")
    if r.target_hr_zone:
        lines.append(f"    HR zone   {r.target_hr_zone}")
    lines.append(f"    Effort    {r.intensity.value.replace('_',' ')}")
    return "\n".join(lines)


def _garmin_entry(r: WorkoutRecommendation) -> str:
    """
    Step-by-step instructions to enter the recommended run on a Garmin watch.
    Garmin Forerunner / Fenix / Venu series — the path is the same on all.
    """
    if r.is_rest:
        return "  GARMIN ENTRY\n  Nothing to enter — rest day."

    lines = ["  ENTER ON YOUR GARMIN"]
    lines.append("  ─────────────────────────────────────")

    if r.workout_type.value in ("easy", "recovery", "moderate"):
        lines += [
            "  Option A — simple alert (recommended)",
            "    1. Start a Running activity on your watch",
            "    2. During the run: set a Distance alert",
            f"       → Alert when distance reaches {r.target_distance_km:.1f} km",
            "    3. Run at the HR zone shown below",
            "",
            "  Option B — structured workout",
            "    On Garmin Connect app (phone):",
            "    1. Calendar → + → Workout",
            "    2. Add step: Running",
            f"       Duration: Distance → {r.target_distance_km:.1f} km",
        ]
        if r.target_hr_zone:
            lines.append(f"       Intensity: Heart Rate → {r.target_hr_zone} zone")
        lines += [
            "    3. Save → send to device",
        ]

    elif r.workout_type.value == "tempo":
        d = r.target_distance_km or 5.0
        warmup = min(1.5, d * 0.2)
        work   = round(d - warmup * 2, 1)
        lines += [
            "  Structured workout (Garmin Connect app → send to watch):",
            "    1. Calendar → + → Workout",
            f"    2. Step 1: Warm-up  {warmup:.1f} km  — HR: easy zone",
            f"    3. Step 2: Work     {work:.1f} km  — HR: threshold zone",
            f"    4. Step 3: Cool-down {warmup:.1f} km — HR: easy zone",
            "    5. Save & sync to watch",
        ]

    elif r.workout_type.value == "interval":
        lines += [
            "  Interval workout (Garmin Connect app → send to watch):",
            "    1. Calendar → + → Workout",
            "    2. Step 1: Warm-up   1.0 km — HR: easy zone",
            "    3. Step 2: Repeat block — set Repeat count",
        ]
        if r.description and "×" in r.description:
            part = r.description.split("×")[0].strip().split()[-1]
            reps = part if part.isdigit() else "6"
            lines.append(f"       Repeats: {reps}")
        lines += [
            "       Work:     800 m — HR: max zone",
            "       Recovery: 400 m — HR: recovery zone",
            "    4. Step 3: Cool-down 1.0 km — HR: easy zone",
            "    5. Save & sync to watch",
        ]

    elif r.workout_type.value == "long_run":
        lines += [
            "  Long run — set a distance alert on your watch:",
            "    1. Start Running activity",
            f"    2. Set alert: Distance → {r.target_distance_km:.1f} km",
            "    3. Keep HR in easy zone throughout",
            "    Tip: run as an out-and-back so you finish near home",
        ]

    lines.append("  ─────────────────────────────────────")

    # HR zone numbers
    return "\n".join(lines)


def _warnings(warnings) -> str:
    lines = ["  WARNINGS"]
    for w in warnings:
        lines.append(f"  ⚠  {w}")
    return "\n".join(lines)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _bar(value: float, width: int = 12) -> str:
    filled = round((value / 100.0) * width)
    return "█" * filled + "░" * (width - filled)


def _tsb_label(tsb: float) -> str:
    if tsb > 25:  return "(peak / race ready)"
    if tsb > 5:   return "(fresh)"
    if tsb > -10: return "(optimal training)"
    if tsb > -25: return "(fatigued)"
    return "(overreached)"
