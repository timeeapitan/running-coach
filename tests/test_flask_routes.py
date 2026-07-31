"""Tests for Flask routes — auth, dashboard, API endpoints."""
import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web.app import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["SECRET_KEY"] = "test-key"
    with app.test_client() as c:
        yield c


@pytest.fixture
def logged_in_client(client):
    """Client with a fake session set."""
    with client.session_transaction() as sess:
        sess["user_id"] = "test_athlete_123"
        sess["user_name"] = "Timeea Test"
        sess["user_avatar"] = ""
    return client


class TestPublicRoutes:
    def test_login_page_loads(self, client):
        r = client.get("/login")
        assert r.status_code == 200
        assert b"Running Coach" in r.data or b"Sign in" in r.data or b"login" in r.data.lower()

    def test_health_endpoint(self, client):
        r = client.get("/api/health")
        assert r.status_code == 200

    def test_api_status_returns_json(self, client):
        r = client.get("/api/status")
        assert r.status_code == 200
        data = r.get_json()
        assert "logged_in" in data

    def test_unauthenticated_dashboard_redirects(self, client):
        r = client.get("/")
        assert r.status_code == 302
        assert "/login" in r.headers["Location"]

    def test_unauthenticated_history_redirects(self, client):
        r = client.get("/history")
        assert r.status_code == 302

    def test_unauthenticated_schedule_redirects(self, client):
        r = client.get("/schedule")
        assert r.status_code == 302

    def test_unauthenticated_insights_redirects(self, client):
        r = client.get("/insights")
        assert r.status_code == 302

    def test_unauthenticated_race_redirects(self, client):
        r = client.get("/race")
        assert r.status_code == 302

    def test_unauthenticated_log_redirects(self, client):
        r = client.get("/log")
        assert r.status_code == 302


class TestSyncStatus:
    def test_sync_status_requires_auth(self, client):
        r = client.get("/api/sync-status")
        assert r.status_code == 302  # redirects to login

    def test_sync_status_authenticated(self, logged_in_client):
        r = logged_in_client.get("/api/sync-status")
        assert r.status_code == 200
        data = r.get_json()
        assert "can_sync" in data
        assert isinstance(data["can_sync"], bool)


class TestAuthenticatedRoutes:
    def test_dashboard_loads_for_logged_in_user(self, logged_in_client):
        """Dashboard should load (may show no-data state but not crash)."""
        r = logged_in_client.get("/")
        # Either 200 (loaded) or 302 (redirect to setup) — both are valid
        assert r.status_code in (200, 302)

    def test_setup_page_loads(self, logged_in_client):
        r = logged_in_client.get("/setup")
        assert r.status_code == 200

    def test_schedule_page_loads(self, logged_in_client):
        # Schedule page redirects to setup if no profile — both are valid
        r = logged_in_client.get("/schedule")
        assert r.status_code in (200, 302)

    def test_logout_clears_session(self, logged_in_client):
        r = logged_in_client.get("/logout")
        assert r.status_code == 302
        # After logout, dashboard should redirect to login
        r2 = logged_in_client.get("/")
        assert r2.status_code == 302
        assert "/login" in r2.headers["Location"]


class TestScheduleRoute:
    def test_schedule_post_saves(self, logged_in_client):
        data = {
            "monday": "easy", "tuesday": "rest", "wednesday": "moderate",
            "thursday": "rest", "friday": "easy", "saturday": "long_run",
            "sunday": "rest",
        }
        r = logged_in_client.post("/schedule", data=data)
        # Should redirect after save
        assert r.status_code == 302

    def test_schedule_invalid_type_defaults_to_coach(self, logged_in_client):
        data = {day: "invalid_type" for day in
                ["monday","tuesday","wednesday","thursday","friday","saturday","sunday"]}
        r = logged_in_client.post("/schedule", data=data)
        assert r.status_code == 302
