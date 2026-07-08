-- Running Coach v2 — clean Garmin-only Supabase schema
-- Intended for a fresh start. If you want to reset completely, run the DROP
-- statements at the top, then run this whole file.

create extension if not exists pgcrypto;

-- Optional full reset for the clean Garmin version:
-- drop table if exists feedback cascade;
-- drop table if exists daily_cache cascade;
-- drop table if exists runs_cache cascade;
-- drop table if exists users cascade;

-- Users / app identity
create table if not exists users (
  username        text primary key,
  provider        text not null default 'garmin',
  profile         jsonb not null default '{}'::jsonb,
  activity_token  jsonb,
  settings        jsonb not null default '{}'::jsonb,
  display_name    text,
  avatar_url      text,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- Clean migration helpers from older experimental schemas.
alter table users add column if not exists provider text not null default 'garmin';
alter table users add column if not exists profile jsonb not null default '{}'::jsonb;
alter table users add column if not exists activity_token jsonb;
alter table users add column if not exists settings jsonb not null default '{}'::jsonb;
alter table users add column if not exists display_name text;
alter table users add column if not exists avatar_url text;
alter table users add column if not exists created_at timestamptz not null default now();
alter table users add column if not exists updated_at timestamptz not null default now();

-- Manual notes / optional check-in. Garmin supplies sleep/HRV, but pain/notes are manual.
create table if not exists feedback (
  username    text not null references users(username) on delete cascade,
  entry_date  date not null,
  data        jsonb not null default '{}'::jsonb,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  primary key (username, entry_date)
);

-- Running history cache: one row per Garmin run. This is append/update, not daily replacement.
create table if not exists runs_cache (
  username                text not null references users(username) on delete cascade,
  activity_id             text not null,
  activity_date           timestamptz not null,
  activity_type           text,
  distance_km             double precision,
  duration_minutes        double precision,
  avg_pace_min_per_km     double precision,
  avg_hr                  double precision,
  max_hr                  double precision,
  elevation_gain_m        double precision,
  cadence                 double precision,
  training_load           double precision,
  source                  text not null default 'garmin_connect',
  raw_json                jsonb not null default '{}'::jsonb,
  created_at              timestamptz not null default now(),
  updated_at              timestamptz not null default now(),
  primary key (username, activity_id)
);

create index if not exists runs_cache_username_date_idx on runs_cache(username, activity_date desc);
create index if not exists runs_cache_activity_type_idx on runs_cache(activity_type);

-- Daily coach/recovery cache: one row per day. Watch metrics live here.
create table if not exists daily_cache (
  username              text not null references users(username) on delete cascade,
  cache_date            date not null,
  watch_health          jsonb,
  summary               jsonb not null default '{}'::jsonb,
  should_run_today      boolean,
  next_run_date         date,
  recommended_session   text,
  coach_message         text,
  coach_reason          text,
  risk_level            text,
  last_sync             timestamptz,
  sync_status           text,
  sync_error            text,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  primary key (username, cache_date)
);

create index if not exists daily_cache_username_date_idx on daily_cache(username, cache_date desc);
create index if not exists daily_cache_sync_idx on daily_cache(sync_status, last_sync desc);

-- If previous experimental columns/tables exist, keep the project Garmin-clean.
-- Triggers
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists users_updated_at on users;
create trigger users_updated_at before update on users
for each row execute function update_updated_at();

drop trigger if exists feedback_updated_at on feedback;
create trigger feedback_updated_at before update on feedback
for each row execute function update_updated_at();

drop trigger if exists runs_cache_updated_at on runs_cache;
create trigger runs_cache_updated_at before update on runs_cache
for each row execute function update_updated_at();

drop trigger if exists daily_cache_updated_at on daily_cache;
create trigger daily_cache_updated_at before update on daily_cache
for each row execute function update_updated_at();

-- RLS. The Flask backend should use the Supabase service-role key.
alter table users enable row level security;
alter table feedback enable row level security;
alter table runs_cache enable row level security;
alter table daily_cache enable row level security;

drop policy if exists "service role full access on users" on users;
create policy "service role full access on users" on users for all using (true) with check (true);

drop policy if exists "service role full access on feedback" on feedback;
create policy "service role full access on feedback" on feedback for all using (true) with check (true);

drop policy if exists "service role full access on runs_cache" on runs_cache;
create policy "service role full access on runs_cache" on runs_cache for all using (true) with check (true);

drop policy if exists "service role full access on daily_cache" on daily_cache;
create policy "service role full access on daily_cache" on daily_cache for all using (true) with check (true);
