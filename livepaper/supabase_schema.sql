-- Schema for the Supabase cloud mirror of data_shared/portfolio.db.
-- Mirrors the SQLite tables in livepaper/shared_portfolio.py exactly.
-- Paste into the Supabase SQL editor (Dashboard -> SQL -> New query -> Run),
-- or run via psql against the project's connection string.

create table if not exists public.portfolio (
  id            int primary key check (id = 1),
  balance       double precision not null,
  updated_ts_ms bigint not null
);

create table if not exists public.settlements (
  key    text primary key,            -- idempotency key: one row per settled window/pathway
  ticker text not null,
  kind   text not null,               -- fade | strong
  asset  text,                         -- BTC | ETH
  net    double precision not null,    -- realized PnL for the window (USD)
  ts_ms  bigint not null
);

create table if not exists public.events (
  ts_ms  bigint not null,
  kind   text not null,
  detail text not null
);

create index if not exists settlements_ts_idx on public.settlements (ts_ms);

-- Lock the tables down: enable RLS with NO policies so the anon/publishable key
-- cannot read or write. The secret (service) key bypasses RLS, which is what the
-- bot, backfill, and reconcile use.
alter table public.portfolio   enable row level security;
alter table public.settlements enable row level security;
alter table public.events      enable row level security;
