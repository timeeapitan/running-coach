# Running Coach — Garmin Connect personal version

This version syncs activities from Garmin Connect using the unofficial `garminconnect` Python package, so it does not depend on the Strava API subscription change.

## Render setup

Add these environment variables on Render if you want one-click login:

```
GARMIN_EMAIL=your_garmin_email
GARMIN_PASSWORD=your_garmin_password
```

Then deploy and open `/refresh` to force a new Garmin sync.

> Note: this is intended for personal use. Garmin does not provide a public free personal API; `garminconnect` uses an unofficial Garmin Connect login flow and may break if Garmin changes their site.

---

# Running Coach

Your personal running coach. Connects to Strava, analyses your training, and recommends your next run — accessible from your phone browser.

---

## What it does

- Pulls your runs automatically from Strava (no manual CSV export)
- Calculates fatigue, consistency, and readiness from your actual data
- Recommends the next run with distance, duration, HR zone, and step-by-step Garmin entry instructions
- Tracks your personal records
- Shows zone 2 drift — whether your aerobic base is improving over time
- Calculates injury risk from workload patterns (ACWR, monotony, consecutive days)
- Predicts your race finish time using the Riegel formula
- Generates a periodised week-by-week training plan for your race goal
- Three ML models that personalise predictions as your data grows
- Works as a web app on your phone and as a CLI in your terminal

---

## Requirements

Python 3.9 or newer. Check: `python3 --version`

Install dependencies:
```bash
pip install flask gunicorn
```

---

## Run locally (terminal → browser on phone on same wifi)

```bash
cd running_coach_project
python web/app.py
```

Open `http://localhost:5000` in your browser. On the same wifi network, other devices can reach it at `http://YOUR_COMPUTER_IP:5000`.

---

## CLI (terminal only — no web browser needed)

```bash
python run.py setup                         # first-time profile setup
python run.py connect-strava                # connect Strava (one-time)
python run.py advise                        # get today's recommendation
python run.py history [N]                   # last N runs
python run.py train                         # retrain ML models
python run.py status                        # ML model status
python run.py users                         # list all users
```

All commands support `--user name` for multiple users.

---

## Connect Strava (one-time setup, then automatic)

1. Go to [strava.com/settings/api](https://www.strava.com/settings/api)
2. Create a free API app:
   - Name: `Running Coach`
   - Category: `Other`
   - Website: `http://localhost`
   - Callback Domain: `localhost`
3. Run: `python run.py connect-strava`
4. Paste your Client ID and Client Secret when asked
5. Click Authorize in the browser that opens

From then on, `python run.py advise` and the web app pull your runs automatically.

---

## Deploy to the web (free — access from anywhere on your phone)

### Render.com (recommended)

1. Push this project to a GitHub repository
2. Sign up at [render.com](https://render.com)
3. New → Web Service → connect your GitHub repo
4. Render detects `render.yaml` automatically
5. After deploy, upload your `user_data/` folder to Render's persistent disk
6. Your app is live at `https://your-app-name.onrender.com`

**Bookmark it on your phone:** Safari → Share → Add to Home Screen. It behaves like a native app.

**Important for deployment:** The Strava token (`user_data/me/strava_token.json`) must be on the server's persistent disk. After first deploy, connect Strava locally and copy the token file up, or run the connect command via Render's shell.

---

## Pages

| Page | URL | What it shows |
|---|---|---|
| Dashboard | `/` | Today's recommendation, status scores, HR zones, personal records |
| History | `/history` | Last 60 runs, pace trend chart, weekly volume, zone 2 drift |
| Insights | `/insights` | Injury risk score, race time prediction |
| Log | `/log` | Form to log RPE, sleep, HRV after each run |
| Race | `/race` | Set a race goal, get a periodised training plan |
| Setup | `/setup` | Edit your profile (max HR, resting HR, fitness level) |

---

## Project structure

```
run.py                      ← CLI entry point
requirements.txt            ← Flask + gunicorn
Procfile                    ← deployment start command
render.yaml                 ← Render deployment config

running_coach/              ← core engine (no web dependencies)
  schemas/                  ← data types (Run, Profile, Feedback, etc.)
  parsers/                  ← Garmin CSV + Strava API parsers
  analysis/
    fatigue.py              ← HR-based TSS, ATL/CTL/TSB
    consistency.py          ← training regularity
    readiness.py            ← energy + HRV + sleep
    insights.py             ← zone 2 drift, injury risk, race predictor
  coaching/
    rules.py                ← rule-based workout logic
    coach.py                ← RunningCoach main class
  ml/
    models/                 ← Ridge regression, GBM, KNN
    training/trainer.py     ← auto-labels + trains all 3 models
  config.py                 ← tuning constants

web/                        ← Flask web application
  app.py                    ← routes and business logic
  static/css/style.css      ← mobile-friendly stylesheet
  templates/
    base.html               ← shared nav layout
    dashboard.html          ← main page
    history.html            ← run list + charts + zone 2 drift
    insights.html           ← injury risk + race predictor
    log.html                ← feedback form
    race.html               ← race goal + training plan
    setup.html              ← profile editor

user_data/                  ← created automatically
  me/
    profile.json            ← your personal settings
    strava_token.json       ← Strava OAuth token
    feedback.json           ← logged RPE/sleep/HRV entries
    models/                 ← trained ML model files
```

---

## The ML models

Three models train on your run history. They need no manual labelling — the training data is generated automatically from your runs.

**Fatigue predictor** (Gradient Boosting): learns to predict fatigue from training load features. Uses HR-based TSS, volume ratios, and consecutive days. Improves with load variation — easy days followed by hard days give it something to learn from.

**Pace predictor** (Ridge Regression): learns to predict your optimal pace from training history, fatigue, and sleep signals. Most useful once your paces vary (some easy, some hard sessions).

**Workout recommender** (k-Nearest Neighbours): looks at your current situation and finds the 5 most similar past training days. Recommends what worked those days. Needs at least 3 different workout types in your history to add value over the rule engine.

Models activate after 10 runs and save to `user_data/me/models/`. Delete that folder to reset.

---

## How to enter workouts on your Garmin

The dashboard prints the exact steps each time. General approach:

**Easy / moderate / long runs:** Start Running activity → set a Distance alert at the target km → run at the HR zone shown.

**Tempo runs:** Garmin Connect app → Calendar → + → Workout → add 3 steps (warm-up, work, cool-down with HR zones) → Save & sync.

**Intervals:** Same as tempo but use the Repeat step: 6× Work 800 m (max zone) / Rest 400 m (recovery zone).

---

## Multiple users

```bash
python run.py setup --user sister
python run.py advise --user sister
```

Each user has separate profile, Strava token, feedback, and ML models. Nothing is shared.

---

## Troubleshooting

**Web app shows "No runs found"** — Connect Strava: `python run.py connect-strava`

**Zones look wrong** — Check `max_hr` and `resting_hr` in Setup. Max HR from your hardest Garmin run; resting HR from Garmin app → Health Stats → Heart Rate.

**ML badge not showing** — Run `python run.py train` to retrain. Need 10+ runs.

**Deployed app loses data on restart** — Make sure `user_data/` is on Render's persistent disk (configured in `render.yaml`).
