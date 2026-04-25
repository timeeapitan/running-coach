#!/usr/bin/env python3
"""
Running Coach — personal training advisor powered by your Garmin/Strava data.

USAGE:
  First time setup:
    python run.py setup                          # set up your profile
    python run.py setup --user sister            # set up another user

  Connect Strava (one-time, then runs are fetched automatically):
    python run.py connect-strava                 # default user
    python run.py connect-strava --user sister

  Daily use — with Strava connected (no file needed):
    python run.py advise                         # pulls latest from Strava
    python run.py advise --user sister

  Daily use — with CSV file (Garmin export fallback):
    python run.py advise Activities.csv
    python run.py advise Activities.csv --user sister

  Other commands:
    python run.py history [N] [--user name]      # last N runs (default 10)
    python run.py train   [file.csv] [--user name]
    python run.py status  [--user name]          # ML model status
    python run.py users                          # list all users
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "user_data")
os.makedirs(DATA_DIR, exist_ok=True)

GREEN = "\033[92m"; AMBER = "\033[93m"; RED = "\033[91m"
BOLD  = "\033[1m";  RESET = "\033[0m"
def c(t, col): return f"{col}{t}{RESET}"


# ── User / path helpers ───────────────────────────────────────────────────────

def _user_dir(username: str) -> str:
    safe = "".join(ch for ch in username.lower() if ch.isalnum() or ch == "_")
    d = os.path.join(DATA_DIR, safe)
    os.makedirs(os.path.join(d, "models"), exist_ok=True)
    return d

def _profile_path(username: str) -> str:
    return os.path.join(_user_dir(username), "profile.json")

def _model_dir(username: str) -> str:
    return os.path.join(_user_dir(username), "models")

def _strava_token_path(username: str) -> str:
    return os.path.join(_user_dir(username), "strava_token.json")

def _list_users():
    if not os.path.exists(DATA_DIR):
        return []
    return [d for d in os.listdir(DATA_DIR)
            if os.path.isfile(os.path.join(DATA_DIR, d, "profile.json"))]

def _parse_user(args) -> str:
    if "--user" in args:
        idx = args.index("--user")
        if idx + 1 < len(args):
            return args[idx + 1]
    return "me"

def _load_profile(username: str):
    from running_coach.schemas.profile import RunnerProfile
    path = _profile_path(username)
    if not os.path.exists(path):
        if username == "me":
            print(c("No profile found. Run:  python run.py setup", RED))
        else:
            print(c(f"No profile for '{username}'. Run:  python run.py setup --user {username}", RED))
        sys.exit(1)
    return RunnerProfile.load(path)

def _load_runs(garmin_file: str = None, username: str = "me"):
    """
    Load runs from Strava (if connected) or a Garmin CSV file.
    Strava is always tried first when connected and no file is given.
    """
    strava_path = _strava_token_path(username)

    # Strava path: connected and no explicit file given
    if os.path.exists(strava_path) and not garmin_file:
        from running_coach.parsers.strava import StravaParser
        parser = StravaParser(strava_path)
        print(c("  Fetching runs from Strava ...", AMBER), end=" ", flush=True)
        try:
            runs = parser.fetch_runs()
            print(c("done", GREEN))
            print(parser.describe(runs))
            print()
            return runs
        except Exception as e:
            print(c(f"failed ({e})", RED))
            print(c("  Falling back — pass a CSV file instead.", AMBER))
            sys.exit(1)

    # CSV path
    if not garmin_file:
        print(c("No Strava connection found.", AMBER))
        print("Either connect Strava:  python run.py connect-strava")
        print("Or pass a CSV file:     python run.py advise Activities.csv")
        sys.exit(1)

    from running_coach.parsers.garmin import GarminParser
    if not os.path.exists(garmin_file):
        print(c(f"File not found: {garmin_file}", RED))
        print("Export from garminconnect.com → Activities → Export CSV")
        sys.exit(1)

    parser = GarminParser()
    print(c(f"  Loading {os.path.basename(garmin_file)} ...", AMBER), end=" ", flush=True)
    runs = parser.load(garmin_file)
    print(c("done", GREEN))
    print(parser.describe(runs))
    print()
    return runs


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_setup(username: str):
    from running_coach.schemas.profile import RunnerProfile
    print(c(f"\n=== Setup: {username} ===", BOLD))
    if os.path.exists(_profile_path(username)):
        print(c("Profile already exists. Answers will overwrite it.\n", AMBER))
    profile = RunnerProfile.from_interactive()
    profile.save(_profile_path(username))
    print(c(f"\nProfile saved for '{username}'.", GREEN))
    print(f"\nNext step — connect Strava so runs are fetched automatically:")
    print(f"  python run.py connect-strava --user {username}")
    print(f"\nOr use a CSV file directly:")
    print(f"  python run.py advise Activities.csv --user {username}")


def cmd_connect_strava(username: str):
    """One-time Strava OAuth2 setup."""
    from running_coach.parsers.strava import StravaAuth

    print(c(f"\n=== Connect Strava for '{username}' ===", BOLD))
    print("""
You need a free Strava API app to connect. This takes about 2 minutes:

  1. Go to: https://www.strava.com/settings/api
  2. Fill in:
       Application Name: Running Coach (or anything you like)
       Category:         Other
       Website:          http://localhost
       Authorization Callback Domain: localhost
  3. Click Create
  4. Copy the Client ID and Client Secret shown on that page
  5. Paste them here when prompted
""")

    client_id = input("  Client ID: ").strip()
    if not client_id:
        print(c("Cancelled.", AMBER)); return

    client_secret = input("  Client Secret: ").strip()
    if not client_secret:
        print(c("Cancelled.", AMBER)); return

    auth = StravaAuth(_strava_token_path(username))
    print()
    success = auth.authorize(client_id, client_secret)

    if success:
        print(c(f"\n  Connected! Strava runs will now load automatically.", GREEN))
        print(f"\n  Try it:  python run.py advise --user {username}")
    else:
        print(c("\n  Connection failed. Check your Client ID and Client Secret and try again.", RED))


def cmd_users():
    users = _list_users()
    if not users:
        print(c("No users set up yet. Run:  python run.py setup", AMBER))
        return
    print(c(f"\n  Users ({len(users)})", BOLD))
    for u in users:
        try:
            from running_coach.schemas.profile import RunnerProfile
            p = RunnerProfile.load(os.path.join(DATA_DIR, u, "profile.json"))
            models  = os.path.join(DATA_DIR, u, "models")
            trained = any(f.endswith(".json") for f in os.listdir(models)) if os.path.exists(models) else False
            strava  = os.path.exists(_strava_token_path(u))
            ml_tag  = c("ML trained", GREEN) if trained else c("no ML yet", AMBER)
            st_tag  = c("Strava connected", GREEN) if strava else c("CSV only", AMBER)
            print(f"  {u:20s}  {p.name:20s}  {st_tag}  {ml_tag}")
        except Exception:
            print(f"  {u}")
    print()


def cmd_advise(garmin_file: str, username: str):
    from running_coach.coaching.coach import RunningCoach
    from running_coach.coaching.templates import format_full_report
    from running_coach.ml.models.next_run_predictor import NextRunPredictor

    profile = _load_profile(username)
    runs    = _load_runs(garmin_file, username)
    coach   = RunningCoach(profile, model_dir=_model_dir(username))

    if len(runs) >= 10 and not coach.trainer.fatigue_predictor.is_trained:
        print(c("  Training ML models on your data (first time)...", AMBER))
        coach.train_models(runs)

    analysis = coach.analyze(runs)

    if len(runs) >= NextRunPredictor.MIN_RUNS_FOR_PERSONALISATION:
        recommendation = coach.predict_next_run(runs)
    else:
        recommendation = coach.recommend(analysis)
        remaining = NextRunPredictor.MIN_RUNS_FOR_PERSONALISATION - len(runs)
        print(c(f"  Note: personalised predictions start in {remaining} more run(s).", AMBER))

    print("\n" + format_full_report(recommendation, analysis, profile.name))
    _print_hr_zones(profile)


def cmd_train(garmin_file: str, username: str):
    from running_coach.coaching.coach import RunningCoach

    profile = _load_profile(username)
    runs    = _load_runs(garmin_file, username)

    if len(runs) < 10:
        print(c(f"Need at least 10 runs to train (have {len(runs)}).", RED)); return

    print(c(f"  Training ML models for '{username}' on {len(runs)} runs...", AMBER))
    coach   = RunningCoach(profile, model_dir=_model_dir(username))
    metrics = coach.train_models(runs)

    print(c("\n  Results:", GREEN))
    for model, m in metrics.items():
        if not isinstance(m, dict): continue
        if "error"   in m: print(f"    {model:10s}  {c('error: ' + m['error'], RED)}")
        elif "skipped" in m: print(f"    {model:10s}  {c('skipped — ' + m['skipped'][:60], AMBER)}")
        else:
            mae = m.get("mae", "—"); r2 = m.get("r2", m.get("accuracy", "—"))
            key = "acc" if "accuracy" in m else "R²"
            print(f"    {model:10s}  MAE={mae}  {key}={r2}")
    print()


def cmd_status(username: str):
    from running_coach.coaching.coach import RunningCoach
    profile = _load_profile(username)
    coach   = RunningCoach(profile, model_dir=_model_dir(username))
    strava  = os.path.exists(_strava_token_path(username))
    print(c(f"\n  Status for '{username}' ({profile.name})", BOLD))
    print(f"  Strava: {c('connected', GREEN) if strava else c('not connected', AMBER)}")
    print("  " + coach.model_status().replace("\n", "\n  "))
    print()


def cmd_history(garmin_file: str, username: str, n: int = 10):
    _load_profile(username)
    runs   = _load_runs(garmin_file, username)
    recent = sorted(runs, key=lambda r: r.date, reverse=True)[:n]

    print(c(f"\n  Last {len(recent)} runs\n", BOLD))
    print(f"  {'Date':<12} {'Type':<14} {'km':>5} {'Time':>8} {'Pace':>8} {'HR':>5} {'Elev':>6}")
    print("  " + "─" * 62)
    for r in recent:
        h = int(r.duration_minutes // 60)
        m = int(r.duration_minutes % 60)
        s = int((r.duration_minutes * 60) % 60)
        t = f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
        hr   = f"{int(r.avg_hr)}" if r.avg_hr else "—"
        elev = f"{int(r.elevation_gain_m)}m" if r.elevation_gain_m else "—"
        kind = r.activity_type.value.replace("_", " ")[:13]
        print(f"  {str(r.date.date()):<12} {kind:<14} {r.distance_km:>5.1f} "
              f"{t:>8} {r.pace_str:>8} {hr:>5} {elev:>6}")
    print()


# ── HR zones ──────────────────────────────────────────────────────────────────

def _print_hr_zones(profile):
    zones = profile.get_hr_zones()
    print("  YOUR HR ZONES (for Garmin entry)")
    print("  " + "─" * 36)
    labels = {
        "recovery": "Zone 1  Recovery ",
        "easy":     "Zone 2  Easy     ",
        "aerobic":  "Zone 3  Aerobic  ",
        "threshold":"Zone 4  Threshold",
        "max":      "Zone 5  Max      ",
    }
    for key, (lo, hi) in zones.items():
        print(f"  {labels.get(key, key):<20}  {lo}–{hi} bpm")
    print()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__); return

    # Strip --user NAME from args to get clean positional args
    clean = []
    skip  = False
    for i, a in enumerate(args):
        if skip:
            skip = False; continue
        if a == "--user":
            skip = True; continue
        clean.append(a)

    cmd      = clean[0] if clean else ""
    username = _parse_user(args)

    if cmd == "setup":
        cmd_setup(username)

    elif cmd == "connect-strava":
        cmd_connect_strava(username)

    elif cmd == "users":
        cmd_users()

    elif cmd == "advise":
        # Optional positional arg: CSV file
        garmin_file = clean[1] if len(clean) > 1 else None
        cmd_advise(garmin_file, username)

    elif cmd == "train":
        garmin_file = clean[1] if len(clean) > 1 else None
        cmd_train(garmin_file, username)

    elif cmd == "history":
        garmin_file = None
        n = 10
        if len(clean) > 1:
            if clean[1].endswith(".csv"):
                garmin_file = clean[1]
                n = int(clean[2]) if len(clean) > 2 and clean[2].isdigit() else 10
            elif clean[1].isdigit():
                n = int(clean[1])
        cmd_history(garmin_file, username, n)

    elif cmd == "status":
        cmd_status(username)

    else:
        print(c(f"Unknown command: {cmd}", RED))
        print("Commands: setup | connect-strava | advise | train | history | status | users")
        sys.exit(1)


if __name__ == "__main__":
    main()
