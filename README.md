# olymp_pe_bot

Telegram-бот для тренировки вопросов с дневными лимитами, рейтингом и оплатой Telegram Stars.

## Важно: откат PR #16 и #17

PR #16 и PR #17 были откачены, потому что `/admin` падал из-за несовпадения схемы `admins`: код ожидал колонку `admins.telegram_id`, которой нет в текущей базе.

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
python src/bot.py
```

## Запуск через Docker

```bash
docker compose up -d --build
```

`.env` создаётся на сервере вручную и не коммитится в репозиторий.

## Переменные окружения
См. `.env.example`.

## Первичная подготовка БД

1. Убедитесь, что есть таблицы:
   - `topics`, `questions`, `users`, `user_day`, `answers`, `subscriptions`, `admins`, `payments`
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
- `/my_payments` — мои покупки и последние платежи
- `/admin` — админ-меню (добавление вопроса, импорт пачкой, последние 10, toggle активности, выдача админки)
- `/admin_stats`, `/grant_admin`, `/revoke_admin`, `/add_question`, `/toggle_question` — служебные админ-команды
- `/test_pay_pack10`, `/test_pay_unlimited30` — тестовые платежи (только при `TEST_MODE=true` и только для админов)

## Добавление вопросов
- Через `/admin` → «Добавить вопрос» (FSM с опциональными topic/difficulty)
- Через `/admin` → «Импорт вопросов (пачкой)»
- Через `/add_question` (быстрый вход в тот же FSM)
- Через Supabase SQL editor (используйте поля `topic_id`, `difficulty`, `q`, `a1..a4`, `correct`, `is_active`)

### Пример импорта вопросов пачкой

```text
Q: Сколько будет 2 + 2?
A) 3
B) 4
C) 5
D) 22
ANS: B
TOPIC_ID: 1
DIFF: 1
ACTIVE: true
---

В: Столица Франции?
A) Берлин
B) Мадрид
C) Париж
D) Рим
ANS: C
---

Q: Какой оператор в Python делает возведение в степень?
A) //
B) **
C) ==
D) ->
ANS: B
```

Во втором блоке `TOPIC_ID`, `DIFF` и `ACTIVE` не указаны — вопрос сохранится с `topic_id = null`, `difficulty = null` и `is_active = true`.

## Монетизация Telegram Stars

1. Примените SQL-миграцию `scripts/001_payments.sql` в Supabase SQL Editor.
2. В `.env` есть переключатель `MONETIZATION_ENABLED`:
   - `false` (по умолчанию) — кнопки покупок скрыты, `successful_payment` не начисляет доступ;
   - `true` — кнопки покупок показываются и обработка Stars-платежей включается.
3. Инвойсы Stars создаются в `buy_handler` (`src/bot.py`) с фиксированным `invoice_payload`:
   - `PACK10` для пакета +10 (стоимость из `PACK10_STARS`)
   - `UNLIMITED30` для безлимита 30 дней (стоимость из `UNLIMITED30_STARS`)
4. Начисления выполняются через единый сервис `grant_purchase` (`src/logic/entitlements.py`):
   - идемпотентная запись платежа в `public.payments` по `telegram_payment_charge_id`
   - начисление пакета (`paid_packs_available + 1`) или продление `subscriptions.unlimited_until` на 30 дней
5. Пользователь может открыть `/my_payments` (или кнопку «Мои покупки») и увидеть баланс пакетов, статус безлимита и последние 3 платежа.

## Тестовый режим платежей

Для тестирования сценария успешной оплаты без Telegram Stars:

1. В `.env` включите режим и задайте админов:

```env
TEST_MODE=true
ADMIN_TG_IDS=123456789,987654321
```

2. Перезапустите бота.
3. В Telegram (под админ-аккаунтом) используйте команды:
   - `/test_pay_pack10` — симулирует покупку `PACK10`
   - `/test_pay_unlimited30` — симулирует покупку `UNLIMITED30`

Обе команды вызывают тот же общий сервис начисления, что и реальная `successful_payment`, поэтому тестовый и боевой флоу синхронизированы.

## Ручной деплой с телефона
GitHub → Actions → **Deploy (manual)** → **Run workflow**.

