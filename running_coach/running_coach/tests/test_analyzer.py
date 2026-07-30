"""
Tests for the analysis engine.
"""

import pytest
from datetime import datetime, timedelta
from statistics import mean

from running_coach.analysis import RunningAnalyzer
from running_coach.analysis.fatigue import FatigueCalculator
from running_coach.analysis.consistency import ConsistencyCalculator
from running_coach.analysis.readiness import ReadinessCalculator
from running_coach.schemas import (
    NormalizedRun,
    ManualFeedback,
    RunnerProfile,
    ActivityType,
)


class TestRunnerProfile:
    """Test RunnerProfile functionality."""
    
    def test_hr_zones_with_resting_hr(self):
        """Should calculate Karvonen-based HR zones."""
        profile = RunnerProfile(
            max_hr=185,
            resting_hr=55,
        )
        
        zones = profile.get_hr_zones()
        
        # With HRR of 130, easy zone (60-70%) should be:
        # Low: 55 + 130 * 0.60 = 133
        # High: 55 + 130 * 0.70 = 146
        assert zones["easy"][0] == 133
        assert zones["easy"][1] == 146
    
    def test_hr_zones_without_resting_hr(self):
        """Should calculate percentage-based HR zones."""
        profile = RunnerProfile(
            max_hr=185,
            resting_hr=None,
        )
        
        zones = profile.get_hr_zones()
        
        # Easy zone (60-70%) of 185
        assert zones["easy"][0] == 111
        assert zones["easy"][1] == 129
    
    def test_estimate_max_hr(self):
        """Should estimate max HR from age."""
        profile = RunnerProfile(age=35)
        
        zones = profile.get_hr_zones()
        
        # Tanaka formula: 208 - 0.7 * 35 = 183.5 -> 183
        # Easy zone at 60-70%
        assert zones["easy"][0] == int(183 * 0.60)


class TestFatigueCalculator:
    """Test fatigue score calculations."""
    
    @pytest.fixture
    def profile(self):
        return RunnerProfile(
            max_hr=185,
            resting_hr=55,
            runs_per_week=2,
        )
    
    @pytest.fixture
    def calculator(self, profile):
        return FatigueCalculator(profile)
    
    def create_runs(self, count: int, days_apart: int = 3, base_distance: float = 5.0) -> list:
        """Helper to create test runs."""
        base_date = datetime.now() - timedelta(days=count * days_apart)
        return [
            NormalizedRun(
                date=base_date + timedelta(days=i * days_apart),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=base_distance,
                duration_minutes=base_distance * 6.5,
                avg_hr=145,
                avg_pace_min_per_km=6.5,
            )
            for i in range(count)
        ]
    
    def test_low_fatigue_with_normal_load(self, calculator):
        """Normal consistent training should show low fatigue."""
        runs = self.create_runs(8, days_apart=3, base_distance=5.0)
        
        score, factors = calculator.calculate(runs, {})
        
        # With consistent training, fatigue should be moderate
        assert score < 50
    
    def test_high_fatigue_with_volume_spike(self, calculator):
        """Volume spike should increase fatigue."""
        # 4 weeks of normal running
        normal_runs = self.create_runs(8, days_apart=3, base_distance=5.0)
        
        # Add a big week (volume spike)
        recent_date = datetime.now() - timedelta(days=3)
        spike_runs = [
            NormalizedRun(
                date=recent_date + timedelta(days=i),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=10.0,  # Double the normal distance
                duration_minutes=65,
                avg_hr=155,
                avg_pace_min_per_km=6.5,
            )
            for i in range(3)
        ]
        
        all_runs = normal_runs + spike_runs
        score, factors = calculator.calculate(all_runs, {})
        
        # Acute load factor should be elevated
        assert factors.get("acute_load", 0) > 20
    
    def test_fatigue_with_high_rpe_feedback(self, calculator):
        """High RPE feedback should increase fatigue."""
        runs = self.create_runs(4, days_apart=3)
        
        # Add high RPE feedback for recent runs
        feedback = {}
        for run in runs[-2:]:
            date_key = run.date.date().isoformat()
            feedback[date_key] = ManualFeedback(
                date=run.date,
                rpe=9,  # Very hard
            )
        
        score, factors = calculator.calculate(runs, feedback)
        
        assert factors.get("rpe_feedback", 0) > 0
    
    def test_no_runs_returns_zero_fatigue(self, calculator):
        """Empty run list should return zero fatigue."""
        score, factors = calculator.calculate([], {})
        
        assert score == 0
        assert factors == {}


class TestConsistencyCalculator:
    """Test consistency score calculations."""
    
    @pytest.fixture
    def profile(self):
        return RunnerProfile(runs_per_week=2)
    
    @pytest.fixture
    def calculator(self, profile):
        return ConsistencyCalculator(profile)
    
    def test_high_consistency_with_regular_runs(self, calculator):
        """Regular runs matching target frequency should score high."""
        # 4 weeks of exactly 2 runs per week
        runs = []
        base_date = datetime.now() - timedelta(weeks=4)
        
        for week in range(4):
            for run_num in range(2):
                runs.append(NormalizedRun(
                    date=base_date + timedelta(weeks=week, days=run_num * 3),
                    activity_type=ActivityType.OUTDOOR_RUN,
                    distance_km=5.0,
                    duration_minutes=32,
                ))
        
        score, factors = calculator.calculate(runs, {})
        
        assert score > 60
        assert factors.get("regularity", 0) > 30
    
    def test_low_consistency_with_irregular_runs(self, calculator):
        """Irregular runs should score lower."""
        runs = []
        base_date = datetime.now() - timedelta(weeks=4)
        
        # Only 1 run in first week, 4 runs in last week
        runs.append(NormalizedRun(
            date=base_date,
            activity_type=ActivityType.OUTDOOR_RUN,
            distance_km=5.0,
            duration_minutes=32,
        ))
        
        for i in range(4):
            runs.append(NormalizedRun(
                date=datetime.now() - timedelta(days=i),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=5.0,
                duration_minutes=32,
            ))
        
        score, factors = calculator.calculate(runs, {})
        
        # Volume stability should be low due to variance
        assert factors.get("volume_stability", 0) < 30
    
    def test_insufficient_data(self, calculator):
        """Should return neutral score with minimal data."""
        runs = [
            NormalizedRun(
                date=datetime.now(),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=5.0,
                duration_minutes=32,
            )
        ]
        
        score, factors = calculator.calculate(runs, {})
        
        # Should return baseline/neutral score
        assert score == 50


class TestReadinessCalculator:
    """Test readiness score calculations."""
    
    @pytest.fixture
    def profile(self):
        return RunnerProfile(
            max_hr=185,
            resting_hr=55,
        )
    
    @pytest.fixture
    def calculator(self, profile):
        return ReadinessCalculator(profile)
    
    def test_high_readiness_with_low_fatigue(self, calculator):
        """Low fatigue should contribute to high readiness."""
        runs = [
            NormalizedRun(
                date=datetime.now() - timedelta(days=3),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=5.0,
                duration_minutes=32,
                avg_hr=145,
            )
        ]
        
        fatigue_score = 20.0
        consistency_score = 70.0
        
        score, factors = calculator.calculate(
            runs, {}, fatigue_score, consistency_score
        )
        
        # Low fatigue means high energy available
        assert factors.get("energy_available", 0) > 25
        assert score > 50
    
    def test_low_readiness_with_high_fatigue(self, calculator):
        """High fatigue should reduce readiness."""
        runs = [
            NormalizedRun(
                date=datetime.now() - timedelta(days=1),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=10.0,
                duration_minutes=65,
                avg_hr=165,  # High HR indicates hard effort
            )
        ]
        
        fatigue_score = 80.0
        consistency_score = 70.0
        
        score, factors = calculator.calculate(
            runs, {}, fatigue_score, consistency_score
        )
        
        # High fatigue means low energy available
        assert factors.get("energy_available", 0) < 10


class TestRunningAnalyzer:
    """Integration tests for the full analyzer."""
    
    @pytest.fixture
    def profile(self):
        return RunnerProfile(
            max_hr=185,
            resting_hr=55,
            runs_per_week=2,
        )
    
    @pytest.fixture
    def analyzer(self, profile):
        return RunningAnalyzer(profile)
    
    def test_complete_analysis(self, analyzer):
        """Should return complete analysis result."""
        base_date = datetime.now() - timedelta(weeks=4)
        runs = [
            NormalizedRun(
                date=base_date + timedelta(days=i * 3),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=5.0,
                duration_minutes=32,
                avg_hr=145,
                avg_pace_min_per_km=6.4,
            )
            for i in range(8)
        ]
        
        result = analyzer.analyze(runs)
        
        # Should have all scores
        assert 0 <= result.fatigue_score <= 100
        assert 0 <= result.consistency_score <= 100
        assert 0 <= result.readiness_score <= 100
        
        # Should have metrics
        assert result.recent_volume_km >= 0
        assert result.average_weekly_volume_km >= 0
        assert result.trend in ["increasing", "stable", "decreasing"]
    
    def test_generates_pain_warning(self, analyzer):
        """Should generate warning when pain is reported."""
        runs = [
            NormalizedRun(
                date=datetime.now() - timedelta(days=3),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=5.0,
                duration_minutes=32,
            )
        ]
        
        feedback = {
            runs[0].date.date().isoformat(): ManualFeedback(
                date=runs[0].date,
                pain_flag=True,
                pain_location="left knee",
            )
        }
        
        result = analyzer.analyze(runs, feedback)
        
        assert any("pain" in w.lower() for w in result.warnings)
    
    def test_generates_volume_spike_warning(self, analyzer):
        """Should warn about sudden volume increases."""
        base_date = datetime.now() - timedelta(weeks=4)
        
        # Normal volume for 3 weeks
        normal_runs = [
            NormalizedRun(
                date=base_date + timedelta(days=i * 4),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=5.0,
                duration_minutes=32,
            )
            for i in range(6)
        ]
        
        # Big spike in last week
        spike_runs = [
            NormalizedRun(
                date=datetime.now() - timedelta(days=i),
                activity_type=ActivityType.OUTDOOR_RUN,
                distance_km=12.0,  # More than double
                duration_minutes=75,
            )
            for i in range(3)
        ]
        
        runs = normal_runs + spike_runs
        result = analyzer.analyze(runs)
        
        assert any("spike" in w.lower() for w in result.warnings)
    
    def test_empty_runs_returns_neutral(self, analyzer):
        """Should return neutral analysis for empty data."""
        result = analyzer.analyze([])
        
        assert result.fatigue_score == 0
        assert result.readiness_score == 50
        assert "no running data" in result.warnings[0].lower()
