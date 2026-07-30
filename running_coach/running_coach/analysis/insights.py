"""
Higher-level insights derived from run history.

Zone 2 drift detection  — measures aerobic adaptation over time
Injury risk score       — flags dangerous load patterns before they hurt
Race time predictor     — estimates finish time from current fitness
Weekly summary          — structured per-week breakdown
"""

from datetime import datetime, timedelta
from math import log, sqrt
from typing import Dict, List, Optional, Tuple

from ..schemas import NormalizedRun, RunnerProfile


# ── Zone 2 drift ─────────────────────────────────────────────────────────────

def zone2_drift(
    runs: List[NormalizedRun],
    profile: RunnerProfile,
    window_weeks: int = 4,
) -> Dict:
    """
    Detects aerobic adaptation by tracking pace at zone-2 HR over time.

    Aerobic improvement = same HR → faster pace (lower min/km).
    Returns a dict with trend direction, magnitude, and chart-ready data.

    The algorithm:
      1. Filter runs where avg_hr falls in zone 2 range
      2. Compute EWMA pace for two windows (recent vs earlier)
      3. Compare — a drop in pace = improvement
    """
    zones = profile.get_hr_zones()
    z2_lo, z2_hi = zones.get("easy", (0, 999))
    # Include slightly below aerobic top to get more data points
    z2_hi_ext = zones.get("aerobic", (0, 999))[0]  # bottom of zone 3

    zone2_runs = [
        r for r in runs
        if r.avg_hr and z2_lo <= r.avg_hr <= z2_hi_ext
        and r.avg_pace_min_per_km
        and r.distance_km >= 3.0
    ]

    if len(zone2_runs) < 4:
        return {
            "available": False,
            "reason": f"Need at least 4 runs in zone 2 ({z2_lo}–{z2_hi_ext} bpm). "
                      f"Found {len(zone2_runs)}. Try running more of your easy runs "
                      f"at genuinely easy effort.",
            "chart_data": [],
        }

    zone2_runs.sort(key=lambda r: r.date)

    # Build monthly rolling average pace
    chart_data = []
    for r in zone2_runs:
        chart_data.append({
            "date":  r.date.strftime("%d %b %y"),
            "pace":  round(r.avg_pace_min_per_km, 2),
            "hr":    int(r.avg_hr),
        })

    # Compare first-half vs second-half average pace
    mid = len(zone2_runs) // 2
    early_pace = sum(r.avg_pace_min_per_km for r in zone2_runs[:mid]) / mid
    recent_pace= sum(r.avg_pace_min_per_km for r in zone2_runs[mid:]) / (len(zone2_runs) - mid)

    delta = early_pace - recent_pace  # positive = improvement (faster)
    delta_pct = (delta / early_pace) * 100

    if delta > 0.15:
        trend   = "improving"
        summary = f"Your zone 2 pace has improved by {delta:.2f} min/km ({delta_pct:.1f}%) — aerobic base is building."
    elif delta < -0.15:
        trend   = "declining"
        summary = f"Zone 2 pace has slowed by {abs(delta):.2f} min/km. Check recent training load and recovery."
    else:
        trend   = "stable"
        summary = "Zone 2 pace is stable. Keep consistent easy running to see improvement over weeks."

    return {
        "available":   True,
        "trend":       trend,
        "delta_min_km": round(delta, 2),
        "delta_pct":   round(delta_pct, 1),
        "summary":     summary,
        "early_pace":  round(early_pace, 2),
        "recent_pace": round(recent_pace, 2),
        "z2_range":    f"{z2_lo}–{z2_hi_ext} bpm",
        "n_runs":      len(zone2_runs),
        "chart_data":  chart_data,
    }


# ── Injury risk ───────────────────────────────────────────────────────────────

def injury_risk(
    runs: List[NormalizedRun],
    profile: RunnerProfile,
) -> Dict:
    """
    Calculates injury risk (0-100) based on four evidence-backed factors:

    1. Acute:chronic workload ratio (ACWR) — the most predictive single metric.
       ACWR > 1.5 = "danger zone" in sports science literature.
       Uses distance × intensity as the load unit.

    2. Monotony — doing the same effort every day removes recovery stimulus.
       Measured as mean / stdev of daily loads.

    3. Consecutive days — more than 4 in a row without rest increases risk.

    4. Volume spike — week-on-week increase > 30% (the "10% rule" extended).
    """
    if len(runs) < 3:
        return {"score": 0, "level": "unknown", "factors": {}, "advice": []}

    now = datetime.now()

    def load(r: NormalizedRun) -> float:
        """Distance weighted by HR intensity."""
        zones = profile.get_hr_zones()
        thr = zones.get("threshold", (160, 999))[0]
        aer = zones.get("aerobic",   (140, 999))[0]
        if r.avg_hr:
            if r.avg_hr >= thr: m = 1.6
            elif r.avg_hr >= aer: m = 1.2
            else: m = 1.0
        else:
            m = 1.0
        return r.distance_km * m

    def week_load(days_ago_start: int, days_ago_end: int) -> float:
        start = now - timedelta(days=days_ago_start)
        end   = now - timedelta(days=days_ago_end)
        return sum(load(r) for r in runs if end <= r.date < start)

    acute  = week_load(7, 0)
    chronic= sum(week_load(7*(w+1), 7*w) for w in range(4)) / 4 if len(runs) >= 5 else acute

    # 1. ACWR
    acwr = acute / chronic if chronic > 0 else 1.0
    if acwr > 1.8:   acwr_score = 90
    elif acwr > 1.5: acwr_score = 65
    elif acwr > 1.3: acwr_score = 35
    elif acwr < 0.5: acwr_score = 20  # under-training also raises risk
    else:            acwr_score = 0

    # 2. Monotony (last 7 days)
    daily_loads = []
    for d in range(7):
        day = now.date() - timedelta(days=d)
        day_load = sum(load(r) for r in runs if r.date.date() == day)
        daily_loads.append(day_load)
    mean_load = sum(daily_loads) / 7
    stdev_load = (sum((x - mean_load)**2 for x in daily_loads) / 7) ** 0.5
    monotony = mean_load / stdev_load if stdev_load > 0 else 0
    monotony_score = min(40, max(0, (monotony - 1.5) * 20))

    # 3. Consecutive days
    run_dates = {r.date.date() for r in runs}
    today  = now.date()
    consec = 0
    d = today
    while d in run_dates:
        consec += 1
        d -= timedelta(days=1)
    consec_score = min(30, max(0, (consec - 4) * 10))

    # 4. Volume spike vs previous week
    prev_week = week_load(14, 7)
    spike_ratio = acute / prev_week if prev_week > 0 else 1.0
    spike_score = min(30, max(0, (spike_ratio - 1.3) * 50))

    # 5. HR intensity risk — running mostly in zone 3-4 every session
    zones = profile.get_hr_zones()
    aer_lo = zones.get("aerobic", (0, 999))[0]
    recent_runs = [r for r in runs if (now - r.date).days <= 28 and r.avg_hr]
    if recent_runs:
        high_hr_pct = sum(1 for r in recent_runs if r.avg_hr >= aer_lo) / len(recent_runs)
        # If >70% of runs are zone 3+, that is a real risk even at low volume
        intensity_score = min(35, max(0, (high_hr_pct - 0.5) * 70))
    else:
        intensity_score = 0
        high_hr_pct = 0.0

    # 6. Return-from-gap risk — running at normal volume after 2+ week break
    sorted_by_date = sorted(runs, key=lambda r: r.date, reverse=True)
    gap_score = 0
    if len(sorted_by_date) >= 2:
        last_run  = sorted_by_date[0]
        prev_runs = sorted_by_date[1:]
        days_since_prev = (last_run.date - prev_runs[0].date).days
        # Check if there was a gap > 14 days before the most recent run
        if days_since_prev > 14:
            # Risk scales with gap length and how hard the return run was
            gap_factor = min(1.0, (days_since_prev - 14) / 30)
            hr_factor  = min(1.0, last_run.avg_hr / 185.0) if last_run.avg_hr else 0.5
            gap_score  = min(40, 40 * gap_factor * hr_factor)

    # Rebalance weights to include new factors
    raw = (acwr_score    * 0.30
         + monotony_score * 0.15
         + consec_score   * 0.10
         + spike_score    * 0.10
         + intensity_score* 0.20
         + gap_score      * 0.15)
    score = min(100, max(0, raw))

    if score >= 70:   level, color = "high",    "red"
    elif score >= 40: level, color = "moderate", "amber"
    else:             level, color = "low",      "teal"

    advice = []
    if acwr > 1.5:
        advice.append(f"Workload ratio is {acwr:.2f} — too high. Take 1-2 easy days.")
    if monotony > 2.0:
        advice.append("Training is too monotonous. Mix easy and hard days more.")
    if consec >= 5:
        advice.append(f"{consec} consecutive days without rest. Take a rest day today.")
    if spike_ratio > 1.3:
        advice.append(f"Volume jumped {(spike_ratio-1)*100:.0f}% vs last week. Slow down.")
    if intensity_score > 15:
        advice.append(f"{int(high_hr_pct*100)}% of your recent runs are in zone 3+. Add more easy zone 2 runs to reduce injury risk.")
    if gap_score > 15:
        advice.append(f"You returned from a {days_since_prev}-day break — keep this week easy and build gradually.")

    return {
        "score":   round(score, 1),
        "level":   level,
        "color":   color,
        "acwr":    round(acwr, 2),
        "factors": {
            "acwr":         round(acwr_score, 1),
            "monotony":     round(monotony_score, 1),
            "consecutive":  round(consec_score, 1),
            "volume_spike": round(spike_score, 1),
            "hr_intensity":  round(intensity_score, 1),
            "return_gap":    round(gap_score, 1),
        },
        "advice":  advice,
        "consec_days": consec,
    }


# ── Race time predictor ───────────────────────────────────────────────────────

def predict_race_time(
    runs: List[NormalizedRun],
    profile: RunnerProfile,
    race_distance_km: float,
) -> Dict:
    """
    Estimates race finish time using the Riegel formula and current fitness.

    Riegel formula: T2 = T1 × (D2/D1)^1.06
    Where T1/D1 is a reference performance and T2/D2 is the target.

    We use the best recent effort as the reference, then adjust for:
      - Current fitness level (recent training load vs historical)
      - Freshness (TSB: more rested = slightly faster on race day)
    """
    if not runs:
        return {"available": False, "reason": "No runs to predict from."}

    # Find best recent effort — fastest pace over distance ≥ 3 km in last 90 days
    cutoff  = datetime.now() - timedelta(days=90)
    efforts = [
        r for r in runs
        if r.avg_pace_min_per_km and r.distance_km >= 3.0 and r.date >= cutoff
    ]

    if not efforts:
        efforts = [r for r in runs if r.avg_pace_min_per_km and r.distance_km >= 3.0]

    if not efforts:
        return {"available": False, "reason": "No runs with pace data found."}

    # Use the fastest pace effort as reference
    ref = min(efforts, key=lambda r: r.avg_pace_min_per_km)
    ref_time_min = ref.duration_minutes
    ref_dist_km  = ref.distance_km

    # Riegel projection
    riegel_time = ref_time_min * (race_distance_km / ref_dist_km) ** 1.06

    # Fitness adjustment: compare recent 4-week volume to historical
    now = datetime.now()
    recent_vol = sum(r.distance_km for r in runs if (now - r.date).days <= 28)
    hist_vol   = sum(r.distance_km for r in runs if 28 < (now - r.date).days <= 84) / 2
    if hist_vol > 0:
        fitness_ratio = min(1.05, max(0.95, recent_vol / hist_vol))
    else:
        fitness_ratio = 1.0

    adjusted_time = riegel_time * (2 - fitness_ratio)  # higher fitness → faster

    def fmt(minutes: float) -> str:
        h = int(minutes // 60)
        m = int(minutes % 60)
        s = int((minutes - int(minutes)) * 60)
        if h > 0:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    target_pace = adjusted_time / race_distance_km

    return {
        "available":      True,
        "predicted_time": fmt(adjusted_time),
        "predicted_mins": round(adjusted_time, 1),
        "target_pace":    f"{int(target_pace)}:{int((target_pace%1)*60):02d}/km",
        "ref_run_date":   ref.date.strftime("%d %b %Y"),
        "ref_run_dist":   round(ref_dist_km, 1),
        "ref_run_pace":   ref.pace_str,
        "confidence":     "good" if len(efforts) >= 5 else "low",
        "note": (
            "Based on your best recent effort using the Riegel formula. "
            "More race-specific training will improve this estimate."
        ),
    }


# ── Improved race plan ────────────────────────────────────────────────────────

def generate_race_plan(
    profile: RunnerProfile,
    runs: List[NormalizedRun],
) -> Optional[List[Dict]]:
    """
    Generates a periodised week-by-week training plan.

    Uses four classical phases:
      Base    (aerobic foundation — long easy runs, building volume)
      Build   (adding quality — tempo runs, progressive long runs)
      Peak    (race-specific intensity — threshold work, tune-up races)
      Taper   (reducing volume, maintaining intensity before race day)

    Volume targets follow the 10% rule.
    Long run = 30% of weekly volume.
    """
    weeks = profile.weeks_to_race()
    if not weeks or weeks < 1:
        return None

    dist = profile.race_distance_km or 10.0

    # Base weekly volume from recent history — minimum sensible for the race distance
    now = datetime.now()
    recent = [r for r in runs if (now - r.date).days <= 28]
    base_km = sum(r.distance_km for r in recent) / 4 if recent else max(dist * 1.5, 15.0)
    base_km = max(base_km, dist * 1.2, 12.0)  # at least 120% of race dist per week

    # Get HR zones for technical session descriptions
    zones = profile.get_hr_zones()
    z2_lo, z2_hi = zones.get("easy",      (130, 150))
    z3_lo, z3_hi = zones.get("aerobic",   (150, 165))
    z4_lo, z4_hi = zones.get("threshold", (165, 178))

    def fmt_zone(name, lo, hi):
        prefix = f"{name} " if name else ""
        return f"{prefix}zone ({lo}–{hi} bpm)"

    # Day suggestions based on runs_per_week
    rpw = max(1, profile.runs_per_week)
    if rpw >= 4:
        day_labels = ["Monday", "Wednesday", "Friday", "Sunday"]
    elif rpw == 3:
        day_labels = ["Tuesday", "Thursday", "Saturday"]
    elif rpw == 2:
        day_labels = ["Tuesday", "Saturday"]
    else:
        day_labels = ["Saturday"]

    # Phase splits
    if weeks >= 16:
        base_wks, build_wks, peak_wks, taper_wks = weeks-8, 5, 2, 1
    elif weeks >= 10:
        base_wks, build_wks, peak_wks, taper_wks = weeks-5, 2, 2, 1
    elif weeks >= 6:
        base_wks, build_wks, peak_wks, taper_wks = weeks-3, 1, 1, 1
    else:
        base_wks, build_wks, peak_wks, taper_wks = max(1,weeks-2), 0, 0, min(weeks,2)

    plan = []
    rpw = max(1, profile.runs_per_week)  # respect user's runs/week

    for w in range(1, weeks + 1):
        rel = w
        remaining = weeks - w

        if remaining >= (build_wks + peak_wks + taper_wks):
            phase = "Base"
            pct   = 1.0 + (w / max(1, base_wks)) * 0.15
            target_km = round(base_km * pct, 1)
            long_km   = round(max(target_km * 0.35, dist * 0.5), 1)
            easy_km   = round((target_km - long_km) / max(1, rpw - 1), 1)
            easy_mins = int(easy_km * 7.5)
            long_mins = int(long_km * 7.5)
            all_sessions = [
                f"Easy run — {easy_mins} min at {fmt_zone("", z2_lo, z2_hi)}. Start slow, keep HR steady. Focus on form.",
                f"Easy run — {easy_mins} min at {fmt_zone("", z2_lo, z2_hi)}. If you feel good, add 5 min.",
                f"Moderate run — {int(easy_km*6.5)} min at {fmt_zone("", z3_lo, z3_hi)}. Short sentences OK, no full conversation.",
                f"Long run — {long_mins} min at {fmt_zone("", z2_lo, z2_hi)}. Walk if HR climbs above {z2_hi+5}. Bring water.",
            ]
            notes = "Build your aerobic base. Every run at zone 2 counts more than you think."

        elif remaining >= (peak_wks + taper_wks):
            phase = "Build"
            target_km = round(base_km * 1.20, 1)
            long_km   = round(min(target_km * 0.38, dist * 0.9), 1)
            tempo_km  = round(target_km * 0.22, 1)
            easy_km   = round((target_km - long_km - tempo_km) / max(1, rpw - 2), 1)
            long_mins = int(long_km * 7.2)
            tempo_mins= int(tempo_km * 5.8)
            easy_mins = int(easy_km * 7.5)
            all_sessions = [
                f"Easy run — {easy_mins} min at {fmt_zone("", z2_lo, z2_hi)}. Recovery pace, full conversation.",
                f"Tempo run — warm up 10 min easy, then {tempo_mins} min at {fmt_zone("", z4_lo, z4_hi)}. Cool down 10 min.",
                f"Easy run — {easy_mins} min at {fmt_zone("", z2_lo, z2_hi)}. Keep it genuinely easy after tempo.",
                f"Long run — {long_mins} min at {fmt_zone("", z2_lo, z2_hi)}. Negative split — second half slightly faster.",
            ]
            notes = "One quality session per week. Everything else stays easy."

        elif remaining >= taper_wks:
            phase = "Peak"
            target_km = round(base_km * 1.10, 1)
            thresh_km = round(target_km * 0.22, 1)
            race_km   = round(dist * 0.5, 1)
            easy_km   = round((target_km - thresh_km - race_km) / max(1, rpw - 2), 1)
            thresh_mins = int(thresh_km * 5.5)
            easy_mins   = int(easy_km * 7.5)
            all_sessions = [
                f"Easy run — {easy_mins} min at {fmt_zone("", z2_lo, z2_hi)}. Legs should feel fresh.",
                f"Threshold run — 10 min warm-up, {thresh_mins} min at {fmt_zone("", z4_lo, z4_hi)}, 10 min cool-down.",
                f"Easy run — {easy_mins} min at {fmt_zone("", z2_lo, z2_hi)}.",
                f"Race-pace run — {race_km} km at your target race pace. This is a rehearsal.",
            ]
            notes = "Race-specific work. Do not add extra sessions — trust the plan."

        else:
            phase = "Taper"
            taper_pct = 0.55 if remaining == 1 else 0.35
            target_km = round(base_km * taper_pct, 1)
            shakeout_km = round(min(3.0, dist * 0.15), 1)
            easy_mins   = int((target_km - shakeout_km) * 7.5 / max(1, rpw - 1))
            all_sessions = [
                f"Easy run — {easy_mins} min at {fmt_zone("", z2_lo, z2_hi)}. Feel good, stay controlled.",
                f"Rest — walk, stretch, sleep well. No heroics this week.",
                f"Shakeout — {shakeout_km} km easy with 3× 30-sec race-pace strides. Legs should feel springy.",
            ]
            notes = "Cut volume, keep sharpness. Trust your training. Sleep 8+ hours."

        # Cap to runs_per_week — always keep the key session (last one)
        if len(all_sessions) > rpw:
            sessions = all_sessions[:rpw-1] + [all_sessions[-1]]
        else:
            sessions = all_sessions

        # Add day labels
        sessions_with_days = []
        for i, s in enumerate(sessions):
            day = day_labels[i] if i < len(day_labels) else f"Day {i+1}"
            sessions_with_days.append(f"{day}: {s}")
        sessions = sessions_with_days

        plan.append({
            "week":      w,
            "phase":     phase,
            "target_km": target_km,
            "sessions":  sessions,
            "notes":     notes,
        })

    return plan
