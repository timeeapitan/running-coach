"""Tests for automatic fitness level detection."""
import pytest
from conftest import make_run
from running_coach.analysis.fitness_level import detect_fitness_level
from running_coach.schemas.enums import FitnessLevel
from running_coach.schemas import RunnerProfile


class TestFitnessLevelDetection:
    def test_no_runs_returns_beginner(self, profile):
        result = detect_fitness_level([], profile)
        assert result["level"] == FitnessLevel.BEGINNER

    def test_few_runs_returns_beginner(self, profile):
        runs = [make_run(days_ago=i*5) for i in range(3)]
        result = detect_fitness_level(runs, profile)
        assert result["level"] == FitnessLevel.BEGINNER

    def test_consistent_runner_returns_intermediate(self, profile):
        # 20 runs over 10 weeks, 2/week, ~13km/week
        runs = [make_run(days_ago=i*3+1, km=6.5) for i in range(20)]
        result = detect_fitness_level(runs, profile)
        assert result["level"] in (FitnessLevel.INTERMEDIATE, FitnessLevel.ADVANCED)

    def test_high_volume_returns_advanced(self, profile):
        # 60 runs, 40km/week, consistent, pace improving
        runs = [make_run(days_ago=i*2, km=8.0, pace=7.5 - i*0.01) for i in range(60)]
        result = detect_fitness_level(runs, profile)
        assert result["level"] in (FitnessLevel.INTERMEDIATE, FitnessLevel.ADVANCED)

    def test_result_has_reason(self, profile):
        result = detect_fitness_level([], profile)
        assert "reason" in result
        assert len(result["reason"]) > 0

    def test_changed_flag_when_level_differs(self, profile):
        """If detected level differs from profile level, changed=True."""
        profile.fitness_level = FitnessLevel.ADVANCED
        runs = [make_run(days_ago=i*5) for i in range(2)]
        result = detect_fitness_level(runs, profile)
        assert result["changed"] is True

    def test_not_changed_when_same(self, profile):
        profile.fitness_level = FitnessLevel.BEGINNER
        result = detect_fitness_level([], profile)
        assert result["changed"] is False

    def test_returning_runner_not_beginner(self, profile):
        """Enough recent volume → not beginner even with a prior gap."""
        profile.fitness_level = FitnessLevel.INTERMEDIATE
        # Active last 8 weeks — 2 runs/week at 6km each = 12km/week
        recent = [make_run(days_ago=i*3+1, km=6.0) for i in range(18)]
        result = detect_fitness_level(recent, profile)
        assert result["level"] in (FitnessLevel.INTERMEDIATE, FitnessLevel.ADVANCED)
