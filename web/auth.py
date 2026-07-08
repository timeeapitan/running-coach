"""
Authentication helpers for the personal Garmin Connect version.

For personal use, the app can authenticate with Garmin credentials either from:
  - Render environment variables: GARMIN_EMAIL and GARMIN_PASSWORD, or
  - the login form, which stores the credentials in Supabase/local storage.

Note: `garminconnect` is an unofficial library. This is suitable for a personal
portfolio/private app, not a public production integration.
"""

import os
from functools import wraps
from flask import session, redirect, url_for

_HOST_GARMIN_EMAIL = os.environ.get("GARMIN_EMAIL", "")
_HOST_GARMIN_PASSWORD = os.environ.get("GARMIN_PASSWORD", "")
_GARTH_SESSION_DIR = os.environ.get("GARTH_SESSION_DIR", "/etc/secrets")


def secret_session_set() -> bool:
    return (
        os.path.exists(os.path.join(_GARTH_SESSION_DIR, "oauth1_token.json"))
        and os.path.exists(os.path.join(_GARTH_SESSION_DIR, "oauth2_token.json"))
    )


def host_credentials_set() -> bool:
    # Kept for local fallback only. In Render, prefer secret_session_set().
    return bool(_HOST_GARMIN_EMAIL and _HOST_GARMIN_PASSWORD)


def get_host_garmin_credentials() -> tuple[str, str]:
    return _HOST_GARMIN_EMAIL, _HOST_GARMIN_PASSWORD


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def current_user_id() -> str:
    return str(session.get("user_id", ""))


def current_user_name() -> str:
    return session.get("user_name", "Runner")


def current_user_avatar() -> str:
    return session.get("user_avatar", "")
