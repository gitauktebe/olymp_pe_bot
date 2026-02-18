create table if not exists public.subscriptions (
  tg_id bigint primary key references public.users(tg_id) on delete cascade,
  unlimited_until timestamptz not null
);
