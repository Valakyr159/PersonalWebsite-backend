-- Genshin guide: current meta snapshot + daily usage counters.
-- Run once in the shared "PersonalWebsite" Supabase project (SQL Editor). Additive and safe to re-run: it only
-- creates objects prefixed `genshin_` / `pw_` and never touches any other table.
--
-- ACCESS MODEL (least privilege). This project is SHARED with other apps that hold real data, so the backend
-- does NOT get the service_role key. It uses the public anon key, which can do nothing on its own:
--   * every table has RLS on and NO policies, and anon/authenticated have no grants on them;
--   * the only way in is the `genshin_*` functions below (SECURITY DEFINER, fixed search_path);
--   * each one calls pw_check_access(), which refuses unless it receives the app's secret. Only the SHA-256 of
--     the secret is stored here.
-- If the backend's environment ever leaked, an attacker could tamper with the Genshin meta/counters and nothing else.
--
-- CONVENTION FOR FUTURE PERSONAL PROJECTS on this Supabase project: prefix tables and functions with the app's
-- name (`foo_*`), add a row to pw_app_access with its own secret, and expose only SECURITY DEFINER functions that
-- call pw_check_access('foo', p_secret) first. Never hand an app the service_role key.

-- ---------- shared access control ----------

create table if not exists public.pw_app_access (
  app          text primary key,
  secret_hash  text not null            -- hex SHA-256 of the app's secret, never the secret itself
);
alter table public.pw_app_access enable row level security;
revoke all on public.pw_app_access from anon, authenticated;

create or replace function public.pw_check_access(p_app text, p_secret text)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  if p_secret is null or not exists (
    select 1 from public.pw_app_access
     where app = p_app and secret_hash = encode(sha256(convert_to(p_secret, 'UTF8')), 'hex')
  ) then
    raise exception 'forbidden' using errcode = '42501';
  end if;
end;
$$;
-- Internal helper: not callable through the API (the genshin_* functions run it as their owner).
revoke all on function public.pw_check_access(text, text) from public, anon, authenticated;

-- ---------- genshin tables ----------

create table if not exists public.genshin_meta (
  id          text primary key default 'current' check (id = 'current'),  -- single shared row
  payload     jsonb       not null,                                        -- teams, characters, banners, sources, fetchedAt...
  model       text        not null default '',
  degraded    boolean     not null default false,                          -- produced by the backup model
  updated_at  timestamptz not null default now()
);

create table if not exists public.genshin_usage (
  day    date not null,
  kind   text not null check (kind in ('chat', 'meta')),
  count  integer not null default 0,
  primary key (day, kind)
);

alter table public.genshin_meta  enable row level security;
alter table public.genshin_usage enable row level security;
revoke all on public.genshin_meta  from anon, authenticated;
revoke all on public.genshin_usage from anon, authenticated;

-- ---------- genshin functions (the only entry points) ----------

create or replace function public.genshin_get_meta(p_secret text)
returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  r public.genshin_meta;
begin
  perform public.pw_check_access('genshin', p_secret);
  select * into r from public.genshin_meta where id = 'current';
  if not found then
    return null;
  end if;
  return jsonb_build_object('payload', r.payload, 'degraded', r.degraded);
end;
$$;

create or replace function public.genshin_save_meta(p_secret text, p_payload jsonb, p_model text, p_degraded boolean)
returns void
language plpgsql
security definer
set search_path = public
as $$
begin
  perform public.pw_check_access('genshin', p_secret);
  if p_payload is null or octet_length(p_payload::text) > 200000 then
    raise exception 'invalid payload' using errcode = '22023';
  end if;
  insert into public.genshin_meta (id, payload, model, degraded, updated_at)
  values ('current', p_payload, coalesce(p_model, ''), coalesce(p_degraded, false), now())
  on conflict (id) do update
    set payload = excluded.payload, model = excluded.model, degraded = excluded.degraded, updated_at = now();
end;
$$;

-- Counts one use of `p_kind` today (UTC) unless `p_limit` was already reached. One atomic statement, so concurrent
-- requests can't overshoot the cap. Returns true when the use was allowed.
create or replace function public.genshin_bump_usage(p_secret text, p_kind text, p_limit integer)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare
  v_day date := (now() at time zone 'utc')::date;
  v_count integer;
begin
  perform public.pw_check_access('genshin', p_secret);
  if p_limit is null or p_limit < 1 or p_limit > 100000 then
    raise exception 'invalid limit' using errcode = '22023';
  end if;
  insert into public.genshin_usage (day, kind, count) values (v_day, p_kind, 0) on conflict do nothing;
  update public.genshin_usage set count = count + 1
   where day = v_day and kind = p_kind and count < p_limit
  returning count into v_count;
  delete from public.genshin_usage where day < v_day - 7;  -- old counters are noise: keep a week
  return v_count is not null;
end;
$$;

-- The functions are callable by the anon role (the key the backend uses) and by nobody else.
revoke all on function public.genshin_get_meta(text)                        from public, authenticated;
revoke all on function public.genshin_save_meta(text, jsonb, text, boolean) from public, authenticated;
revoke all on function public.genshin_bump_usage(text, text, integer)       from public, authenticated;
grant execute on function public.genshin_get_meta(text)                        to anon;
grant execute on function public.genshin_save_meta(text, jsonb, text, boolean) to anon;
grant execute on function public.genshin_bump_usage(text, text, integer)       to anon;

-- ---------- register the secret (run ONCE; store only the hash) ----------
-- Generate a secret:  python3 -c "import secrets; print(secrets.token_urlsafe(32))"
-- Put the secret in the backend's GENSHIN_DB_SECRET (.env and Render), then run:
--
--   insert into public.pw_app_access (app, secret_hash)
--   values ('genshin', encode(sha256(convert_to('<THE SECRET>', 'UTF8')), 'hex'))
--   on conflict (app) do update set secret_hash = excluded.secret_hash;
