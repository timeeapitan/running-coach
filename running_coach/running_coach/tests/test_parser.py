"""
Tests for the Garmin parser.
"""

import pytest
from datetime import datetime

from running_coach.parsers import GarminParser
from running_coach.schemas import ActivityType


@pytest.fixture
def parser():
    return GarminParser()


def make_activity(**overrides):
    base = {
        "activityId": "12345",
        "activityType": {"typeKey": "running"},
        "startTimeLocal": "2024-03-15 08:30:00",
        "distance": 5000,       # metres
        "duration": 1950,       # seconds (32.5 min)
        "averageHR": 148,
        "maxHR": 172,
        "elevationGain": 45.0,
        "averageRunningCadenceInStepsPerMinute": 172,
    }
    base.update(overrides)
    return base


class TestGarminParserSingleActivity:

    def test_parses_outdoor_run(self, parser):
        run = parser.parse_activity(make_activity())
        assert run is not None
        assert run.activity_type == ActivityType.OUTDOOR_RUN

    def test_parses_treadmill_run(self, parser):
        run = parser.parse_activity(
            make_activity(activityType={"typeKey": "treadmill_running"})
        )
        assert run is not None
        assert run.activity_type == ActivityType.TREADMILL_RUN

    def test_parses_trail_run(self, parser):
        run = parser.parse_activity(
            make_activity(activityType={"typeKey": "trail_running"})
        )
        assert run is not None
        assert run.activity_type == ActivityType.TRAIL_RUN

    def test_ignores_cycling(self, parser):
        run = parser.parse_activity(
            make_activity(activityType={"typeKey": "cycling"})
        )
        assert run is None

    def test_distance_converted_to_km(self, parser):
        run = parser.parse_activity(make_activity(distance=10000))
        assert run.distance_km == pytest.approx(10.0)

    def test_duration_converted_to_minutes(self, parser):
        run = parser.parse_activity(make_activity(duration=3600))
        assert run.duration_minutes == pytest.approx(60.0)

    def test_hr_fields_parsed(self, parser):
        run = parser.parse_activity(make_activity(averageHR=152, maxHR=175))
        assert run.avg_hr == 152.0
        assert run.max_hr == 175.0

    def test_missing_hr_is_none(self, parser):
        data = make_activity()
        data.pop("averageHR")
        data.pop("maxHR")
        run = parser.parse_activity(data)
        assert run.avg_hr is None
        assert run.max_hr is None

    def test_elevation_parsed(self, parser):
        run = parser.parse_activity(make_activity(elevationGain=120.5))
        assert run.elevation_gain_m == pytest.approx(120.5)

    def test_cadence_parsed(self, parser):
        run = parser.parse_activity(
            make_activity(averageRunningCadenceInStepsPerMinute=168)
        )
        assert run.cadence == 168

    def test_pace_calculated(self, parser):
        # 5 km in 32.5 min → 6.5 min/km
        run = parser.parse_activity(make_activity(distance=5000, duration=1950))
        assert run.avg_pace_min_per_km == pytest.approx(6.5)

    def test_source_is_garmin(self, parser):
        run = parser.parse_activity(make_activity())
        assert run.source == "garmin"

    def test_external_id_stored(self, parser):
        run = parser.parse_activity(make_activity(activityId="abc-999"))
        assert run.external_id == "abc-999"

    def test_date_parsed_from_local(self, parser):
        run = parser.parse_activity(make_activity(startTimeLocal="2024-06-01 07:15:00"))
        assert run.date == datetime(2024, 6, 1, 7, 15, 0)

    def test_date_parsed_iso_format(self, parser):
        run = parser.parse_activity(
            make_activity(startTimeLocal="2024-06-01T07:15:00")
        )
        assert run.date == datetime(2024, 6, 1, 7, 15, 0)

    def test_zero_distance_returns_none(self, parser):
        run = parser.parse_activity(make_activity(distance=0))
        assert run is None

    def test_zero_duration_returns_none(self, parser):
        run = parser.parse_activity(make_activity(duration=0))
        assert run is None

    def test_malformed_date_returns_none(self, parser):
        run = parser.parse_activity(make_activity(startTimeLocal="not-a-date"))
        assert run is None


class TestGarminParserBatch:

    def test_parse_activities_filters_non_runs(self, parser):
        activities = [
            make_activity(activityId="1"),
            make_activity(activityId="2", activityType={"typeKey": "cycling"}),
            make_activity(activityId="3"),
        ]
        runs = parser.parse_activities(activities)
        assert len(runs) == 2

    def test_parse_activities_skips_malformed(self, parser):
        activities = [
            make_activity(activityId="1"),
            {"activityType": {"typeKey": "running"}},  # missing distance/duration
            make_activity(activityId="3"),
        ]
        runs = parser.parse_activities(activities)
        assert len(runs) == 2

    def test_parse_empty_list(self, parser):
        assert parser.parse_activities([]) == []
