-- Running Coach — Supabase database setup / migration
-- Safe to run multiple times in Supabase SQL Editor.
-- This version matches the current app code:
--   daily_cache(username, date, summary)
--   runs_cache(username, runs, cached_at)
-- Watch health is stored inside daily_cache.summary->'watch_health'.
-- There is NO watch_cache table required.

-- Extension for random UUIDs if needed later
create extension if not exists pgcrypto;

-- ─────────────────────────────────────────────────────────────────────────────
-- Users
-- ─────────────────────────────────────────────────────────────────────────────
create table if not exists users (
  username      text primary key,
  profile       jsonb,
  strava_token  jsonb,
  display_name  text,
  avatar_url    text,
  created_at    timestamptz default now(),
  updated_at    timestamptz default now()
);

alter table users add column if not exists profile jsonb;
alter table users add column if not exists strava_token jsonb;
alter table users add column if not exists display_name text;
alter table users add column if not exists avatar_url text;
alter table users add column if not exists created_at timestamptz default now();
alter table users add column if not exists updated_at timestamptz default now();

-- ─────────────────────────────────────────────────────────────────────────────
-- Feedback
-- ─────────────────────────────────────────────────────────────────────────────
create table if not exists feedback (
  id          bigserial primary key,
  username    text not null references users(username) on delete cascade,
  date        text not null,
  data        jsonb not null,
  created_at  timestamptz default now(),
  unique(username, date)
);

alter table feedback add column if not exists username text;
alter table feedback add column if not exists date text;
alter table feedback add column if not exists data jsonb;
alter table feedback add column if not exists created_at timestamptz default now();

create index if not exists feedback_username_idx on feedback(username);
create unique index if not exists feedback_username_date_uidx on feedback(username, date);

-- ─────────────────────────────────────────────────────────────────────────────
-- Daily cache
-- One row per user per day.
-- Stores BOTH:
--   1) coach/dashboard summary
--   2) Garmin watch health inside summary->'watch_health'
-- Example summary jsonb keys used by the app:
--   status, headline, recommendation, watch_health, watch_cached_at,
--   summary_cached_at, should_run_today, next_run_date, coach_reason
-- ─────────────────────────────────────────────────────────────────────────────
create table if not exists daily_cache (
  id          bigserial primary key,
  username    text not null references users(username) on delete cascade,
  date        text not null,
  summary     jsonb not null default '{}'::jsonb,
  created_at  timestamptz default now()
);

alter table daily_cache add column if not exists username text;
alter table daily_cache add column if not exists date text;
alter table daily_cache add column if not exists summary jsonb not null default '{}'::jsonb;
alter table daily_cache add column if not exists created_at timestamptz default now();

-- Keep only one daily cache row per user/date.
create unique index if not exists daily_cache_username_date_uidx on daily_cache(username, date);
create index if not exists daily_cache_username_date_idx on daily_cache(username, date);

-- ─────────────────────────────────────────────────────────────────────────────
-- Runs cache
-- Current app stores the serialised runs list as JSONB in a single row per user.
-- cached_at is used as the daily TTL marker.
-- ─────────────────────────────────────────────────────────────────────────────
create table if not exists runs_cache (
  id          bigserial primary key,
  username    text not null references users(username) on delete cascade,
  runs        jsonb not null default '[]'::jsonb,
  cached_at   timestamptz default now(),
  unique(username)
);

alter table runs_cache add column if not exists username text;
alter table runs_cache add column if not exists runs jsonb not null default '[]'::jsonb;
alter table runs_cache add column if not exists cached_at timestamptz default now();

create unique index if not exists runs_cache_username_uidx on runs_cache(username);
create index if not exists runs_cache_cached_at_idx on runs_cache(cached_at);

-- ─────────────────────────────────────────────────────────────────────────────
-- Row Level Security + service-role policies
-- The app uses SUPABASE_KEY server-side. Service role should have full access.
-- ─────────────────────────────────────────────────────────────────────────────
alter table users enable row level security;
alter table feedback enable row level security;
alter table daily_cache enable row level security;
alter table runs_cache enable row level security;

drop policy if exists "service role full access on users" on users;
create policy "service role full access on users" on users for all using (true) with check (true);

drop policy if exists "service role full access on feedback" on feedback;
create policy "service role full access on feedback" on feedback for all using (true) with check (true);

drop policy if exists "service role full access on daily_cache" on daily_cache;
create policy "service role full access on daily_cache" on daily_cache for all using (true) with check (true);

drop policy if exists "service role full access on runs_cache" on runs_cache;
create policy "service role full access on runs_cache" on runs_cache for all using (true) with check (true);

-- ─────────────────────────────────────────────────────────────────────────────
-- updated_at trigger for users
-- ─────────────────────────────────────────────────────────────────────────────
create or replace function update_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists users_updated_at on users;
create trigger users_updated_at
  before update on users
  for each row execute function update_updated_at();

-- Optional cleanup note:
-- If a previous experimental version created public.watch_cache, this app no longer uses it.
-- Do NOT drop it unless you are sure you do not need its old data.
-- drop table if exists watch_cache;
