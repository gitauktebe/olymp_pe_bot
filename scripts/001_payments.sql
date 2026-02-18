-- Таблица платежей
create table if not exists public.payments (
  id bigserial primary key,
  tg_id bigint not null references public.users(tg_id) on delete cascade,
  provider text not null default 'telegram_stars',
  currency text not null,
  total_amount integer not null,
  invoice_payload text not null,
  telegram_payment_charge_id text not null unique,
  status text not null default 'success' check (status in ('success','refunded')),
  created_at timestamptz not null default now()
);

create index if not exists idx_payments_tg_id_created_at on public.payments(tg_id, created_at desc);

-- (опционально) Для пакетов +10 можно использовать user_settings.paid_packs_available (уже есть)
-- Для безлимита используется таблица subscriptions (уже есть) с unlimited_until timestamptz
