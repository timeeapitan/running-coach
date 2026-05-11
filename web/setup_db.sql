-- Running Coach — Supabase database setup
-- Run this once in the Supabase SQL editor

-- Users table
-- username = Strava athlete ID (e.g. "12345678")
create table if not exists users (
  username      text primary key,
  profile       jsonb,
  strava_token  jsonb,
  display_name  text,
  avatar_url    text,
  created_at    timestamp with time zone default now(),
  updated_at    timestamp with time zone default now()
);

-- Feedback table
create table if not exists feedback (
  id          bigserial primary key,
  username    text not null references users(username) on delete cascade,
  date        text not null,
  data        jsonb not null,
  created_at  timestamp with time zone default now(),
  unique(username, date)
);

create index if not exists feedback_username_idx on feedback(username);

-- Row Level Security
alter table users    enable row level security;
alter table feedback enable row level security;

create policy "service role full access on users"
  on users for all using (true);

create policy "service role full access on feedback"
  on feedback for all using (true);

-- Auto-update updated_at
create or replace function update_updated_at()
returns trigger as $$
begin new.updated_at = now(); return new; end;
$$ language plpgsql;

create or replace trigger users_updated_at
  before update on users
  for each row execute function update_updated_at();

-- Daily summary cache — one row per user per day
-- Old rows (previous days) are kept for historical reference but never served
create table if not exists daily_cache (
  id          bigserial primary key,
  username    text not null references users(username) on delete cascade,
  date        text not null,
  summary     jsonb not null,
  created_at  timestamp with time zone default now(),
  unique(username, date)
);

create index if not exists daily_cache_username_date_idx on daily_cache(username, date);

alter table daily_cache enable row level security;
create policy "service role full access on daily_cache"
  on daily_cache for all using (true);

-- Runs cache — stores Strava runs per user, TTL managed in app code
create table if not exists runs_cache (
  id          bigserial primary key,
  username    text not null references users(username) on delete cascade,
  runs        jsonb not null,
  cached_at   timestamp with time zone default now(),
  unique(username)
);
alter table runs_cache enable row level security;
create policy "service role full access on runs_cache"
  on runs_cache for all using (true);
