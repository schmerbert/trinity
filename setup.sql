-- Trinity — Supabase Setup
-- Run this in the Supabase SQL editor for a fresh instance.
-- Execute in order: profiles first, then dependent tables, then ALTER TABLE additions.

-- ─── Core identity ────────────────────────────────────────────────────────────

create table profiles (
  id               uuid primary key default gen_random_uuid(),
  name             text,
  risk_tolerance   text,
  interests        jsonb default '[]',
  feedback_history jsonb default '[]',
  created_at       timestamp default now()
);

alter table profiles enable row level security;
create policy "allow all" on profiles for all using (true);

-- ─── Profiles — extended columns (added incrementally) ───────────────────────

alter table profiles add column if not exists shelf                 jsonb       default '[]';
alter table profiles add column if not exists queued_thoughts       jsonb       default '[]';
alter table profiles add column if not exists pending_discord_writes jsonb      default '[]';
alter table profiles add column if not exists last_seen             timestamp;
alter table profiles add column if not exists wake_history          jsonb       default '[]';
alter table profiles add column if not exists wake_requested_at     timestamp;
alter table profiles add column if not exists scratchpad_text       text        default '';
alter table profiles add column if not exists current_state         text        default 'asleep';
alter table profiles add column if not exists last_heartbeat        timestamptz;
alter table profiles add column if not exists last_clean_close      timestamptz;
alter table profiles add column if not exists queued_self_thoughts  jsonb       default '[]';

-- ─── Conversation summaries ───────────────────────────────────────────────────

create table conversations (
  id                  uuid primary key default gen_random_uuid(),
  profile_id          uuid references profiles(id),
  themes              jsonb,
  sentiment           text,
  new_thinking        text,
  open_threads        jsonb,
  communication_style text,
  created_at          timestamp default now()
);

alter table conversations enable row level security;
create policy "allow all" on conversations for all using (true);

-- ─── Eyes system — scored signals ────────────────────────────────────────────

create table alerts (
  id              uuid primary key default gen_random_uuid(),
  profile_id      uuid references profiles(id),
  headline        text,
  relevance_score float default 0,
  seen            boolean default false,
  created_at      timestamp default now()
);

alter table alerts enable row level security;
create policy "allow all" on alerts for all using (true);

-- ─── Calendar ─────────────────────────────────────────────────────────────────

create table trinity_calendar (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid references profiles(id),
  title       text not null,
  event_date  timestamp not null,
  notes       text,
  triggered   boolean default false,
  created_at  timestamp default now()
);

alter table trinity_calendar enable row level security;
create policy "allow all" on trinity_calendar for all using (true);

-- ─── Keyword watches ─────────────────────────────────────────────────────────

create table trinity_watches (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid references profiles(id),
  keyword     text not null,
  note        text,
  active      boolean default true,
  created_at  timestamp default now(),
  unique(profile_id, keyword)
);

alter table trinity_watches enable row level security;
create policy "allow all" on trinity_watches for all using (true);

-- ─── RSS feed sources ─────────────────────────────────────────────────────────

create table trinity_feeds (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid references profiles(id),
  name        text not null,
  url         text not null,
  active      boolean default true,
  created_at  timestamp default now(),
  unique(profile_id, url)
);

alter table trinity_feeds enable row level security;
create policy "allow all" on trinity_feeds for all using (true);

-- ─── Self-scheduled triggers ──────────────────────────────────────────────────

create table trinity_triggers (
  id               uuid primary key default gen_random_uuid(),
  profile_id       uuid references profiles(id),
  note             text not null,
  fire_at          timestamp not null,
  recurring        boolean default false,
  interval_minutes integer,
  active           boolean default true,
  created_at       timestamp default now()
);

alter table trinity_triggers enable row level security;
create policy "allow all" on trinity_triggers for all using (true);

-- ─── Prompt modules (admin-defined context blocks) ───────────────────────────

create table prompt_modules (
  id          uuid primary key default gen_random_uuid(),
  name        text unique not null,
  description text,
  content     text not null,
  category    text,
  keywords    jsonb default '[]',
  active      boolean default true,
  created_at  timestamp default now()
);

alter table prompt_modules enable row level security;
create policy "allow all" on prompt_modules for all using (true);

-- ─── Self-authored rules (Trinity writes these herself) ───────────────────────

create table trinity_prompts (
  id          uuid primary key default gen_random_uuid(),
  profile_id  uuid references profiles(id),
  name        text not null,
  content     text not null,
  trigger     text default '',
  category    text default 'general',
  active      boolean default true,
  usage_count int default 0,
  created_at  timestamp default now(),
  last_used   timestamp,
  unique(profile_id, name)
);

alter table trinity_prompts enable row level security;
create policy "allow all" on trinity_prompts for all using (true);
