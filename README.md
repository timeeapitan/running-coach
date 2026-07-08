# Running Coach v2

Personal Garmin-based running coach for mobile web.

## What changed in v2

- Garmin-only project.
- Garmin session files are the only activity provider integration.
- `runs_cache` stores one row per Garmin running activity.
- `daily_cache` stores one row per day with watch health + coach advice.
- Dashboard auto-syncs only when today has no daily cache.
- Other pages read Supabase cache only.
- `/refresh` manually syncs Garmin with a cooldown to avoid Garmin rate limits.

## Deploy checklist

1. Upload Garmin session files to Render Secret Files:
   - `oauth1_token.json`
   - `oauth2_token.json`
2. Set `SUPABASE_URL`, `SUPABASE_KEY`, and `SECRET_KEY` in Render.
3. Run `web/setup_db.sql` in Supabase.
4. Deploy on Render.
5. Open `/login` and choose **Sign in with Garmin session**.

## Clean database reset

If you want a clean Garmin-only start, run the DROP statements at the top of `web/setup_db.sql`, then run the full file.

## Tables

- `users` — profile/settings/activity provider metadata.
- `runs_cache` — one row per run, upserted by `(username, activity_id)`.
- `daily_cache` — one row per user/day with coach summary and watch metrics.
- `feedback` — optional manual pain/soreness/free notes.
