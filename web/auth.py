"""
Strava OAuth2 authentication helpers.

The login flow:
  1. User visits /login  → redirected to Strava
  2. Strava redirects to /auth/callback?code=XXX
  3. We exchange code for token, get athlete ID
  4. Store token in DB under athlete_id as username
  5. Set session["user_id"] = athlete_id
  6. Redirect to dashboard

Session keys:
  user_id      — Strava athlete ID (string), the DB username
  user_name    — display name for the nav bar
  user_avatar  — Strava profile picture URL
"""

import json, os, urllib.parse, urllib.request
from functools import wraps
from flask import session, redirect, url_for, request

STRAVA_AUTH_URL  = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"
STRAVA_API_BASE  = "https://www.strava.com/api/v3"

# Read from environment — set these in Render dashboard
CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID", "")
CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")


def get_redirect_uri():
    """
    Build the callback URL from the current request host.
    This works automatically on localhost, Render, Railway, or any host.
    """
    from flask import request as flask_request
    try:
        # Use the actual incoming request host — works everywhere
        base = flask_request.host_url.rstrip("/")
    except RuntimeError:
        # Fallback when called outside a request context
        base = os.environ.get("RENDER_EXTERNAL_URL", "http://localhost:5000")
    return f"{base}/auth/callback"


def login_required(f):
    """Decorator — redirects to /login if no session."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


def current_user_id() -> str:
    """Return the logged-in athlete ID, or empty string."""
    return str(session.get("user_id", ""))


def current_user_name() -> str:
    return session.get("user_name", "Runner")


def current_user_avatar() -> str:
    return session.get("user_avatar", "")


def build_auth_url() -> str:
    params = {
        "client_id":     CLIENT_ID,
        "redirect_uri":  get_redirect_uri(),
        "response_type": "code",
        "approval_prompt":"auto",
        "scope":         "activity:read_all",
    }
    return STRAVA_AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str) -> dict:
    """Exchange auth code for token + athlete info. Returns full token dict."""
    payload = urllib.parse.urlencode({
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code":          code,
        "grant_type":    "authorization_code",
    }).encode()
    req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def refresh_token(token: dict) -> dict:
    """Refresh an expired access token. Returns updated token dict."""
    payload = urllib.parse.urlencode({
        "client_id":     CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": token["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        new = json.loads(resp.read())
    # Preserve client credentials for future refreshes
    new["client_id"]     = CLIENT_ID
    new["client_secret"] = CLIENT_SECRET
    return new


def is_configured() -> bool:
    """True if Strava app credentials are set in environment."""
    return bool(CLIENT_ID and CLIENT_SECRET)
