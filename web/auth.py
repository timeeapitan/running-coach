"""
Strava OAuth2 authentication.

Per-user credentials flow:
  1. User visits /login — sees a form to enter their Strava API credentials
  2. They submit Client ID + Secret — stored in session temporarily
  3. Redirected to Strava OAuth using THEIR credentials
  4. Strava sends back a code — exchanged for token using THEIR credentials
  5. Token + credentials saved to DB under their athlete ID
  6. All future token refreshes use their stored credentials

Falls back to host env vars (STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET) if the
user has no stored credentials — so the host (you) can still log in without
filling out the form.
"""

import json, os, urllib.parse, urllib.request
from functools import wraps
from flask import session, redirect, url_for, request

STRAVA_AUTH_URL  = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL = "https://www.strava.com/oauth/token"

# Host-level fallback credentials (from Render env vars)
_HOST_CLIENT_ID     = os.environ.get("STRAVA_CLIENT_ID", "")
_HOST_CLIENT_SECRET = os.environ.get("STRAVA_CLIENT_SECRET", "")


# ── Credential resolution ─────────────────────────────────────────────────────

def get_client_credentials(uid: str = "") -> tuple:
    """
    Return (client_id, client_secret) for the given user.
    Priority:
      1. User's own stored credentials in DB (retrieved from their token)
      2. Session (set during the login flow before we know the athlete ID)
      3. Host env vars (fallback for the host user)
    """
    # Try DB first if we have a uid
    if uid:
        from web.db import load_strava_token
        token = load_strava_token(uid)
        if token and token.get("client_id") and token.get("client_secret"):
            return token["client_id"], token["client_secret"]

    # Try session (set when user submits the credentials form)
    session_id     = session.get("pending_client_id", "")
    session_secret = session.get("pending_client_secret", "")
    if session_id and session_secret:
        return session_id, session_secret

    # Fall back to host env vars
    return _HOST_CLIENT_ID, _HOST_CLIENT_SECRET


def host_credentials_set() -> bool:
    return bool(_HOST_CLIENT_ID and _HOST_CLIENT_SECRET)


# ── Redirect URI ──────────────────────────────────────────────────────────────

def get_redirect_uri() -> str:
    render_url = os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/")
    if render_url:
        return f"{render_url}/auth/callback"
    try:
        host = request.host
        return f"http://{host}/auth/callback"
    except RuntimeError:
        return "http://localhost:5000/auth/callback"


# ── Auth decorators ───────────────────────────────────────────────────────────

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


# ── OAuth helpers ─────────────────────────────────────────────────────────────

def build_auth_url(client_id: str) -> str:
    params = {
        "client_id":      client_id,
        "redirect_uri":   get_redirect_uri(),
        "response_type":  "code",
        "approval_prompt":"force",
        "scope":          "read,activity:read_all",
    }
    return STRAVA_AUTH_URL + "?" + urllib.parse.urlencode(params)


def exchange_code(code: str, client_id: str, client_secret: str) -> dict:
    payload = urllib.parse.urlencode({
        "client_id":     client_id,
        "client_secret": client_secret,
        "code":          code,
        "grant_type":    "authorization_code",
    }).encode()
    req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def refresh_token(token: dict) -> dict:
    client_id     = token.get("client_id",     _HOST_CLIENT_ID)
    client_secret = token.get("client_secret", _HOST_CLIENT_SECRET)
    payload = urllib.parse.urlencode({
        "client_id":     client_id,
        "client_secret": client_secret,
        "refresh_token": token["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(STRAVA_TOKEN_URL, data=payload, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        new = json.loads(resp.read())
    new["client_id"]     = client_id
    new["client_secret"] = client_secret
    return new
