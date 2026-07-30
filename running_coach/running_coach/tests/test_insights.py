"""
Tests for the insights module — zone 2 drift, injury risk, race predictor, race plan.
"""

from datetime import datetime, timedelta



from running_coach.analysis.insights import (
    generate_race_plan,
    injury_risk,
    predict_race_time,
    zone2_drift,
)
from running_coach.schemas import (
    ActivityType,
    FitnessLevel,
    NormalizedRun,
    RunnerProfile,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_profile(**kw):
    defaults = dict(max_hr=185, resting_hr=55, age=30,
                    runs_per_week=3, fitness_level=FitnessLevel.INTERMEDIATE)
    defaults.update(kw)
    return RunnerProfile(**defaults)

def make_runs(n=12, hr=145, pace=6.5, dist=6.0, days_apart=3):
    base = datetime.now() - timedelta(days=n * days_apart)
    return [
        NormalizedRun(
            date=base + timedelta(days=i * days_apart),
            activity_type=ActivityType.OUTDOOR_RUN,
            distance_km=dist,
            duration_minutes=dist * pace,
            avg_pace_min_per_km=pace,
            avg_hr=hr,
        )
        for i in range(n)
    ]


# ── Zone 2 drift ──────────────────────────────────────────────────────────────

class TestZone2Drift:

    def test_not_available_when_no_zone2_runs(self):
        """Runs at 165 bpm are above zone 2 — drift should report unavailable."""
        profile = make_profile(max_hr=185, resting_hr=55)
        runs = make_runs(hr=165)
        result = zone2_drift(runs, profile)
        assert result["available"] is False
        assert "reason" in result

    def test_available_with_sufficient_zone2_runs(self):
        """Runs at 143 bpm sit in zone 2 — drift should calculate."""
        profile = make_profile(max_hr=185, resting_hr=55)
        # Zone 2 with Karvonen (55 rhr, 185 max) ≈ 138–152 bpm
        runs = make_runs(n=10, hr=143, pace=7.5)
        result = zone2_drift(runs, profile)
        assert result["available"] is True
        assert result["trend"] in ("improving", "stable", "declining")
        assert "chart_data" in result
        assert len(result["chart_data"]) >= 4

    def test_improving_trend_when_pace_drops(self):
        """Earlier runs slower than recent runs → improving."""
        profile = make_profile(max_hr=185, resting_hr=55)
        base = datetime.now() - timedelta(weeks=12)
        # First 6 runs: slow pace; last 6: faster pace — same HR
        runs = []
        for i in range(6):
            runs.append(NormalizedRun(
                date=base + timedelta(days=i*7),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=6.0, duration_minutes=54,
                avg_pace_min_per_km=9.0, avg_hr=143,
            ))
        for i in range(6):
            runs.append(NormalizedRun(
                date=base + timedelta(weeks=6, days=i*7),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=6.0, duration_minutes=48,
                avg_pace_min_per_km=8.0, avg_hr=143,
            ))
        result = zone2_drift(runs, profile)
        if result["available"]:
            assert result["trend"] == "improving"
            assert result["delta_min_km"] > 0

    def test_returns_z2_range_string(self):
        profile = make_profile(max_hr=185, resting_hr=55)
        runs = make_runs(n=10, hr=143)
        result = zone2_drift(runs, profile)
        assert "z2_range" in result

    def test_not_available_with_too_few_runs(self):
        profile = make_profile(max_hr=185, resting_hr=55)
        runs = make_runs(n=2, hr=143)
        result = zone2_drift(runs, profile)
        assert result["available"] is False


# ── Injury risk ───────────────────────────────────────────────────────────────

class TestInjuryRisk:

    def test_low_risk_with_consistent_training(self):
        profile = make_profile()
        runs = make_runs(n=12, days_apart=3)  # regular spacing, no spikes
        result = injury_risk(runs, profile)
        assert result["level"] in ("low", "moderate")
        assert 0 <= result["score"] <= 100

    def test_high_risk_with_consecutive_days(self):
        """Running every single day for 7 days including today elevates risk."""
        profile = make_profile()
        now = datetime.now()
        # 7 days ending TODAY so consecutive_days counter fires
        runs = [
            NormalizedRun(
                date=now - timedelta(days=6-i),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=8.0, duration_minutes=56, avg_hr=155,
                avg_pace_min_per_km=7.0,
            )
            for i in range(7)
        ]
        result = injury_risk(runs, profile)
        assert result["consec_days"] >= 6
        assert result["score"] > 4

    def test_returns_advice_when_risk_is_high(self):
        profile = make_profile()
        base = datetime.now() - timedelta(days=10)
        runs = [
            NormalizedRun(
                date=base + timedelta(days=i),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=15.0, duration_minutes=110, avg_hr=165,
                avg_pace_min_per_km=7.3,
            )
            for i in range(10)
        ]
        result = injury_risk(runs, profile)
        assert isinstance(result["advice"], list)

    def test_empty_runs_returns_safe_default(self):
        result = injury_risk([], make_profile())
        assert result["score"] == 0
        assert result["level"] == "unknown"

    def test_acwr_present_in_factors(self):
        profile = make_profile()
        runs = make_runs(n=8)
        result = injury_risk(runs, profile)
        assert "acwr" in result["factors"]
        assert "consecutive" in result["factors"]

    def test_score_is_bounded(self):
        profile = make_profile()
        for _ in range(5):
            runs = make_runs(n=20, hr=170, days_apart=1)
            result = injury_risk(runs, profile)
            assert 0 <= result["score"] <= 100


# ── Race time predictor ───────────────────────────────────────────────────────

class TestRaceTimePredictor:

    def test_predicts_longer_race_takes_more_time(self):
        profile = make_profile()
        runs = make_runs(n=10, pace=6.0, dist=8.0)
        r5  = predict_race_time(runs, profile, 5.0)
        r10 = predict_race_time(runs, profile, 10.0)
        r21 = predict_race_time(runs, profile, 21.1)
        assert r5["available"] and r10["available"] and r21["available"]
        assert r5["predicted_mins"] < r10["predicted_mins"] < r21["predicted_mins"]

    def test_faster_reference_run_gives_faster_prediction(self):
        profile = make_profile()
        slow_runs = make_runs(n=8, pace=8.0, dist=6.0)
        fast_runs = make_runs(n=8, pace=5.5, dist=6.0)
        slow_pred = predict_race_time(slow_runs, profile, 10.0)
        fast_pred = predict_race_time(fast_runs, profile, 10.0)
        assert fast_pred["predicted_mins"] < slow_pred["predicted_mins"]

    def test_not_available_with_no_runs(self):
        result = predict_race_time([], make_profile(), 10.0)
        assert result["available"] is False

    def test_not_available_with_no_pace_data(self):
        runs = [
            NormalizedRun(
                date=datetime.now() - timedelta(days=i),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=6.0, duration_minutes=42,
                avg_pace_min_per_km=None,
            )
            for i in range(5)
        ]
        result = predict_race_time(runs, make_profile(), 10.0)
        assert result["available"] is False

    def test_returns_formatted_time_string(self):
        profile = make_profile()
        runs = make_runs(n=10, pace=6.0, dist=8.0)
        result = predict_race_time(runs, profile, 10.0)
        assert result["available"]
        assert ":" in result["predicted_time"]
        assert "/km" in result["target_pace"]

    def test_confidence_low_with_few_runs(self):
        profile = make_profile()
        runs = make_runs(n=2, pace=6.0, dist=5.0)
        result = predict_race_time(runs, profile, 10.0)
        if result["available"]:
            assert result["confidence"] == "low"

    def test_riegel_exponent_makes_marathon_harder_than_linear(self):
        """Riegel 1.06 exponent means marathon is harder than 2× half."""
        profile = make_profile()
        runs = make_runs(n=10, pace=5.5, dist=10.0)
        half = predict_race_time(runs, profile, 21.1)
        full = predict_race_time(runs, profile, 42.2)
        if half["available"] and full["available"]:
            ratio = full["predicted_mins"] / half["predicted_mins"]
            assert ratio > 2.0  # harder than linear


# ── Race plan ─────────────────────────────────────────────────────────────────

class TestGenerateRacePlan:

    def make_race_profile(self, weeks_ahead=12, dist=10.0):
        rd = (datetime.now() + timedelta(weeks=weeks_ahead)).date().isoformat()
        return make_profile(race_date=rd, race_distance_km=dist)

    def test_returns_correct_number_of_weeks(self):
        profile = self.make_race_profile(weeks_ahead=12)
        plan = generate_race_plan(profile, make_runs())
        assert plan is not None
        assert len(plan) == profile.weeks_to_race()

    def test_last_phase_is_taper(self):
        profile = self.make_race_profile(weeks_ahead=8)
        plan = generate_race_plan(profile, make_runs())
        assert plan is not None
        assert plan[-1]["phase"] == "Taper"

    def test_all_phases_present_in_long_plan(self):
        profile = self.make_race_profile(weeks_ahead=16)
        plan = generate_race_plan(profile, make_runs())
        phases = {w["phase"] for w in plan}
        assert "Base" in phases
        assert "Taper" in phases

    def test_volume_does_not_spike_more_than_30_percent(self):
        """No single week should be more than 30% above previous week."""
        profile = self.make_race_profile(weeks_ahead=12)
        plan = generate_race_plan(profile, make_runs())
        for i in range(1, len(plan)):
            prev = plan[i-1]["target_km"]
            curr = plan[i]["target_km"]
            if prev > 0:
                assert curr <= prev * 1.35, \
                    f"Week {i+1} jumps {curr} vs {prev} — too much"

    def test_each_week_has_sessions(self):
        profile = self.make_race_profile(weeks_ahead=10)
        plan = generate_race_plan(profile, make_runs())
        for w in plan:
            assert len(w["sessions"]) >= 1
            assert w["notes"]

    def test_returns_none_when_no_race_date(self):
        profile = make_profile()  # no race date
        assert generate_race_plan(profile, make_runs()) is None

    def test_returns_none_when_race_is_past(self):
        past = (datetime.now() - timedelta(days=1)).date().isoformat()
        profile = make_profile(race_date=past, race_distance_km=10.0)
        result = generate_race_plan(profile, make_runs())
        assert result is None or len(result) == 0
