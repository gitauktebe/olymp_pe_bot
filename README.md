# olymp_pe_bot

Telegram-бот для тренировки вопросов с дневными лимитами, рейтингом и оплатой Telegram Stars.

## Стек
- Python 3.12
- aiogram v3
- Supabase REST (`supabase-py`) + PostgreSQL

## Локальный запуск

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# заполните TELEGRAM_BOT_TOKEN и ключи Supabase
python -m src.bot
```

## Переменные окружения
См. `.env.example`.

## Первичная подготовка БД

1. Убедитесь, что есть таблицы:
   - `topics`, `questions`, `users`, `user_day`, `answers`, `subscriptions`, `purchases`, `admins`
2. Бот при старте пытается создать `user_settings` через Supabase SQL endpoint (`/pg/v1/query`).
3. Если endpoint недоступен, выполните SQL вручную из логов или создайте таблицу:

```sql
create table if not exists public.user_settings (
  tg_id bigint primary key references public.users(tg_id) on delete cascade,
  mode text not null default 'random' check (mode in ('random','topic','difficulty')),
  topic_id bigint null references public.topics(id),
  difficulty smallint null check (difficulty between 1 and 5),
  paid_packs_available integer not null default 0,
  updated_at timestamptz not null default now()
);
```

## Сидирование

```sql
-- Supabase SQL editor
\i scripts/seed_topics.sql
\i scripts/seed_questions.sql
```

## Команды
- `/start` — регистрация и запуск
- `/rating` — топ-50
- `/stats` — персональная статистика
- `/admin_stats`, `/grant_admin`, `/revoke_admin`, `/add_question`, `/toggle_question` — админка

## Добавление вопросов
- Через `/add_question` (диалог в боте)
- Через Supabase SQL editor (используйте поля `topic_id`, `difficulty`, `text`, `option1..4`, `correct_option`, `is_active`)
