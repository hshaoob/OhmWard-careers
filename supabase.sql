-- Run once in Supabase: SQL Editor -> New query -> paste -> Run.
-- A row exists only after someone clicks the emailed sign-in link, so every subscriber has confirmed their address.
create table subscribers (
  id uuid primary key references auth.users on delete cascade,
  email text not null,
  prefs jsonb not null default '{}',             -- the board's filters, saved: sectors, states, favorites, skills, frequency, ...
  active boolean not null default true,
  unsub_token uuid not null unique default gen_random_uuid(),
  last_sent timestamptz not null default now(),  -- alerts include roles found after this
  created_at timestamptz not null default now()
);
alter table subscribers enable row level security;
-- Signed-in visitors can see and change only their own row, under their own verified email.
create policy "own row" on subscribers for all to authenticated
  using (id = auth.uid())
  with check (id = auth.uid() and email = auth.jwt() ->> 'email');

-- One-click unsubscribe from an email link, no sign-in needed. The token is unguessable and only reachable via that email.
create function unsubscribe(token uuid) returns void language sql security definer set search_path = public as $$
  update subscribers set active = false where unsub_token = token;
$$;
revoke execute on function unsubscribe(uuid) from public;
grant execute on function unsubscribe(uuid) to anon, authenticated;
