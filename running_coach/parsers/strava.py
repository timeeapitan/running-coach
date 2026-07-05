"""
Strava API parser.

Fetches your runs directly from Strava and converts them to NormalizedRun
objects — the same format the rest of the app already understands.

Authentication flow (one-time setup):
  1. You create a free Strava API app at strava.com/settings/api
  2. Run: python run.py connect-strava --user me
  3. A browser opens, you click Authorize
  4. Token is saved to user_data/me/strava_token.json
  5. All future runs use that token automatically (auto-refreshed)

The token is stored only on your machine, never sent anywhere except
back to Strava's own servers.
"""

import json
import os
import time
import urllib.parse
import urllib.request
import urllib.error
import webbrowser
import http.server
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..schemas import NormalizedRun, ActivityType


STRAVA_AUTH_URL    = "https://www.strava.com/oauth/authorize"
STRAVA_TOKEN_URL   = "https://www.strava.com/oauth/token"
STRAVA_API_BASE    = "https://www.strava.com/api/v3"
REDIRECT_PORT      = 8765
REDIRECT_URI       = f"http://localhost:{REDIRECT_PORT}/callback"

STRAVA_TYPE_MAP = {
    "Run":           ActivityType.OUTDOOR_RUN,
    "TrailRun":      ActivityType.TRAIL_RUN,
    "Treadmill":     ActivityType.TREADMILL_RUN,
    "VirtualRun":    ActivityType.TREADMILL_RUN,
}


class StravaAuth:
    """Handles OAuth2 authentication and token storage/refresh."""

    def __init__(self, token_path: str):
        self.token_path = token_path

    def is_connected(self) -> bool:
        return os.path.exists(self.token_path)

    def load_token(self) -> Optional[Dict]:
        if not self.is_connected():
            return None
        with open(self.token_path) as f:
            return json.load(f)

    def save_token(self, token: Dict) -> None:
        with open(self.token_path, "w") as f:
            json.dump(token, f, indent=2)

    def get_valid_access_token(self) -> Optional[str]:
        """Return a valid access token, refreshing if expired."""
        token = self.load_token()
        if not token:
            return None

        # Refresh if expiring within 5 minutes
        if token.get("expires_at", 0) < time.time() + 300:
            token = self._refresh_token(
                token["refresh_token"],
                token["client_id"],
                token["client_secret"],
            )
            if token:
                self.save_token(token)

        return token.get("access_token")

    def authorize(self, client_id: str, client_secret: str) -> bool:
        """
        Run the full OAuth2 flow:
          1. Open browser to Strava auth page
          2. Catch the redirect on localhost
          3. Exchange code for token
          4. Save token to disk
        Returns True on success.
        """
        # Build auth URL
        params = {
            "client_id":     client_id,
            "redirect_uri":  REDIRECT_URI,
            "response_type": "code",
            "scope":         "read,activity:read",
        }
        auth_url = STRAVA_AUTH_URL + "?" + urllib.parse.urlencode(params)

        # Local server to catch the redirect
        auth_code = {"value": None, "error": None}
        server_ready = threading.Event()

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                qs     = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    auth_code["value"] = qs["code"][0]
                    msg = b"<html><body><h2>Connected! You can close this tab.</h2></body></html>"
                else:
                    auth_code["error"] = qs.get("error", ["unknown"])[0]
                    msg = b"<html><body><h2>Authorization failed. Please try again.</h2></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(msg)

            def log_message(self, *args):
                pass  # suppress request logs

        server = http.server.HTTPServer(("localhost", REDIRECT_PORT), Handler)
        thread = threading.Thread(target=server.serve_forever)
        thread.daemon = True
        thread.start()

        print(f"\n  Opening Strava in your browser...")
        print(f"  If it doesn't open automatically, visit:\n  {auth_url}\n")
        webbrowser.open(auth_url)

        # Wait up to 120 seconds for the callback
        deadline = time.time() + 120
        while not auth_code["value"] and not auth_code["error"] and time.time() < deadline:
            time.sleep(0.2)
        server.shutdown()

        if not auth_code["value"]:
            return False

        # Exchange code for token
        token = self._exchange_code(auth_code["value"], client_id, client_secret)
        if not token:
            return False

        token["client_id"]     = client_id
        token["client_secret"] = client_secret
        self.save_token(token)
        return True

    def _exchange_code(self, code: str, client_id: str, client_secret: str) -> Optional[Dict]:
        return self._post_token({
            "client_id":     client_id,
            "client_secret": client_secret,
            "code":          code,
            "grant_type":    "authorization_code",
        })

    def _refresh_token(self, refresh_token: str, client_id: str, client_secret: str) -> Optional[Dict]:
        data = self._post_token({
            "client_id":     client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type":    "refresh_token",
        })
        if data:
            # Preserve credentials for future refreshes
            data["client_id"]     = client_id
            data["client_secret"] = client_secret
        return data

    def _post_token(self, payload: Dict) -> Optional[Dict]:
        data = urllib.parse.urlencode(payload).encode()
        req  = urllib.request.Request(STRAVA_TOKEN_URL, data=data, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Strava token request failed with HTTP {e.code}: {body}")
        except Exception as e:
            raise RuntimeError(f"Strava token request failed: {e}")


class StravaParser:
    """Fetches runs from Strava API and converts to NormalizedRun objects."""

    def __init__(self, token_path: str):
        self.auth = StravaAuth(token_path)

    def is_connected(self) -> bool:
        return self.auth.is_connected()

    def fetch_runs(self, max_runs: int = 200) -> List[NormalizedRun]:
        """
        Fetch all runs from Strava (up to max_runs), newest first.
        Returns sorted oldest → newest.
        """
        token = self.auth.get_valid_access_token()
        if not token:
            raise RuntimeError("Not connected to Strava. Run: python run.py connect-strava")

        activities = self._fetch_activities(token, max_runs)
        runs = []
        for a in activities:
            run = self._parse_activity(a)
            if run:
                runs.append(run)

        runs.sort(key=lambda r: r.date)
        return runs

    def _fetch_activities(self, token: str, max_runs: int) -> List[Dict]:
        """Page through the Strava activities endpoint."""
        activities = []
        page = 1
        per_page = min(100, max_runs)

        while len(activities) < max_runs:
            url = (
                f"{STRAVA_API_BASE}/athlete/activities"
                f"?per_page={per_page}&page={page}"
            )
            req = urllib.request.Request(url)
            req.add_header("Authorization", f"Bearer {token}")

            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    page_data = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                if e.code == 401:
                    raise RuntimeError("Strava token invalid or expired. Please log out and connect Strava again.")
                if e.code == 403:
                    raise RuntimeError("Strava denied access. Check that activity:read_all scope is allowed.")
                if e.code == 429:
                    raise RuntimeError("Strava rate limit reached. Try again later.")
                raise RuntimeError(f"Strava activities request failed with HTTP {e.code}: {body}")
            except Exception as e:
                raise RuntimeError(f"Strava activities request failed: {e}")

            if not page_data:
                break

            activities.extend(page_data)
            if len(page_data) < per_page:
                break
            page += 1

        return activities

    def _parse_activity(self, data: Dict[str, Any]) -> Optional[NormalizedRun]:
        sport_type = data.get("sport_type") or data.get("type", "")
        activity_type = STRAVA_TYPE_MAP.get(sport_type)
        if not activity_type:
            return None

        try:
            date_str = data.get("start_date_local") or data.get("start_date", "")
            date     = datetime.strptime(date_str[:19], "%Y-%m-%dT%H:%M:%S")

            dist_km  = float(data.get("distance", 0)) / 1000.0
            dur_min  = float(data.get("moving_time", 0)) / 60.0

            if dist_km <= 0 or dur_min <= 0:
                return None

            avg_hr   = data.get("average_heartrate")
            max_hr   = data.get("max_heartrate")
            elevation= data.get("total_elevation_gain")
            cadence  = data.get("average_cadence")
            if cadence:
                cadence = int(cadence * 2)  # Strava gives per-leg, multiply by 2 for spm

            pace = (dur_min / dist_km) if dist_km > 0 else None

            return NormalizedRun(
                date=date,
                activity_type=activity_type,
                distance_km=round(dist_km, 2),
                duration_minutes=round(dur_min, 2),
                avg_pace_min_per_km=round(pace, 3) if pace else None,
                avg_hr=float(avg_hr) if avg_hr else None,
                max_hr=float(max_hr) if max_hr else None,
                elevation_gain_m=float(elevation) if elevation else None,
                cadence=cadence,
                source="strava",
                external_id=str(data.get("id", "")),
            )
        except Exception as e:
            print(f"[STRAVA] skipped one activity because it could not be parsed: {e}", flush=True)
            return None

    def describe(self, runs: List[NormalizedRun]) -> str:
        if not runs:
            return "  No runs found on Strava."
        total_km = sum(r.distance_km for r in runs)
        with_hr  = sum(1 for r in runs if r.avg_hr)
        return (
            f"  {len(runs)} runs fetched from Strava\n"
            f"  Date range : {runs[0].date.date()} → {runs[-1].date.date()}\n"
            f"  Total km   : {total_km:.1f} km\n"
            f"  With HR    : {with_hr}/{len(runs)} runs"
        )
