from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Document, LabeledPrice, Message, PreCheckoutQuery
from postgrest.exceptions import APIError

from src.config import settings
from src.db import db
from src.logic import admin as admin_logic
from src.logic import entitlements, payments, quiz, rating
from src.logic.bulk_import import parse_bulk_block as _parse_bulk_block
from src.logic.bulk_import import split_bulk_blocks as _split_bulk_blocks
from src.ui.keyboards import (
    admin_menu_kb,
    answers_kb,
    buy_kb,
    rating_type_kb,
    start_kb,
    unlimited_settings_kb,
    admin_unlimited_days_kb,
)
from src.ui.texts import BLOCKED, DAILY_DONE, NO_QUESTIONS, WELCOME, WRONG_STOP, question_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=settings.telegram_bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


class AddQuestionFSM(StatesGroup):
    text = State()
    option1 = State()
    option2 = State()
    option3 = State()
    option4 = State()
    correct_option = State()
    topic = State()
    difficulty = State()


class AdminFSM(StatesGroup):
    toggle_question = State()
    grant_admin = State()
    bulk_import = State()
    file_import = State()
    grant_unlimited_tg_id = State()
    grant_unlimited_manual_days = State()
    revoke_unlimited_tg_id = State()


class UnlimitedFSM(StatesGroup):
    topic = State()
    difficulty = State()


def can_use_test_commands(tg_id: int) -> bool:
    return settings.test_mode and admin_logic.has_test_mode_access(tg_id)


async def process_test_payment(message: Message, payload: str, amount: int) -> None:
    tg_id = message.from_user.id
    charge_id = f"TEST-{tg_id}-{payload}-{int(datetime.now(timezone.utc).timestamp())}"

    result = entitlements.grant_purchase(
        tg_id=tg_id,
        payload=payload,
        amount=amount,
        currency="XTR",
        charge_id=charge_id,
        is_test=True,
    )
    if result.get("duplicate"):
        await message.answer("🧪 TEST MODE: тестовая оплата уже учтена")
        return

    if payload == payments.PACK10_PAYLOAD:
        await message.answer(
            "🧪 TEST MODE: начислено +10 вопросов.",
            reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)),
        )
        return

    until = datetime.fromisoformat(result["new_until"])
    until_local = until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    await message.answer(
        f"🧪 TEST MODE: начислен безлимит до {until_local}.",
        reply_markup=start_kb(has_unlimited=True),
    )


async def send_next_question(message: Message, tg_id: int) -> None:
    question = quiz.pick_question(tg_id)
    if not question:
        await message.answer(NO_QUESTIONS)
        return
    await message.answer(question_text(question), reply_markup=answers_kb(question["id"]))

def _bulk_import_report(ok_count: int, duplicate_count: int, errors: list[str]) -> str:
    lines = [f"Импорт: добавлено {ok_count}, дубликатов {duplicate_count}, ошибок {len(errors)}"]
    if errors:
        lines.append("")
        lines.append("Первые ошибки:")
        lines.extend(errors[:5])
    return "\n".join(lines)

def _is_duplicate_q_hash_error(exc: Exception) -> bool:
    if not isinstance(exc, APIError):
        return False
    return exc.code == "23505" and "q_hash" in (exc.details or "")


def _normalize_bool(value: str) -> bool | None:
    normalized = (value or "").strip().lower()
    if normalized in {"true", "1", "yes", "y", "да", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "нет", "off"}:
        return False
    return None


def _parse_correct(value: str) -> int:
    normalized = (value or "").strip()
    if not normalized.isdigit():
        raise ValueError("correct должен быть числом 1..4")
    parsed = int(normalized)
    if parsed < 1 or parsed > 4:
        raise ValueError("correct должен быть в диапазоне 1..4")
    return parsed


def _parse_difficulty(value: str) -> int | None:
    normalized = (value or "").strip()
    if not normalized:
        return None
    if not normalized.isdigit():
        raise ValueError("difficulty должен быть числом 1..5")
    parsed = int(normalized)
    if parsed < 1 or parsed > 5:
        raise ValueError("difficulty должен быть в диапазоне 1..5")
    return parsed


def _decode_csv_bytes(data: bytes) -> str:
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1251")


def _csv_delimiter(sample: str) -> str:
    semicolons = sample.count(";")
    commas = sample.count(",")
    return ";" if semicolons > commas else ","


def _topic_id_by_name(topic_name: str, cache: dict[str, int]) -> int:
    normalized = topic_name.strip()
    if not normalized:
        raise ValueError("topic пустой")

    key = normalized.lower()
    cached = cache.get(key)
    if cached is not None:
        return cached

    existing = db.client.table("topics").select("id,title").ilike("title", normalized).limit(1).execute().data or []

    if existing:
        topic_id = int(existing[0]["id"])
        cache[key] = topic_id
        return topic_id

    created = db.client.table("topics").insert({"title": normalized, "is_active": True}).execute().data or []
    if not created:
        raise ValueError("не удалось создать topic")
    topic_id = int(created[0]["id"])
    cache[normalized.lower()] = topic_id
    logger.info("Создана новая тема при импорте CSV: id=%s title=%s", topic_id, normalized)
    return topic_id


def _row_value(row: dict, key: str) -> str:
    value = row.get(key)
    return value.strip() if isinstance(value, str) else ""


def _build_question_from_csv_row(row: dict, row_number: int, topic_cache: dict[str, int]) -> dict | None:
    raw_values = [(value or "").strip() for value in row.values() if value is not None]
    if not any(raw_values):
        return None

    q = _row_value(row, "q")
    a1 = _row_value(row, "a1")
    a2 = _row_value(row, "a2")
    a3 = _row_value(row, "a3")
    a4 = _row_value(row, "a4")

    for field_name, field_value in (("q", q), ("a1", a1), ("a2", a2), ("a3", a3), ("a4", a4)):
        if not field_value:
            raise ValueError(f"пустое обязательное поле {field_name}")

    correct = _parse_correct(_row_value(row, "correct"))

    is_active_raw = _row_value(row, "is_active")
    is_active = True if not is_active_raw else _normalize_bool(is_active_raw)
    if is_active is None:
        raise ValueError("is_active должен быть boolean")

    payload: dict[str, object] = {
        "text": q,
        "option1": a1,
        "option2": a2,
        "option3": a3,
        "option4": a4,
        "correct_option": correct,
        "is_active": is_active,
    }

    topic_id_raw = _row_value(row, "topic_id")
    topic_name_raw = _row_value(row, "topic")
    if topic_id_raw:
        if not topic_id_raw.isdigit():
            raise ValueError("topic_id должен быть числом")
        payload["topic_id"] = int(topic_id_raw)
    elif topic_name_raw:
        payload["topic_id"] = _topic_id_by_name(topic_name_raw, topic_cache)

    difficulty = _parse_difficulty(_row_value(row, "difficulty"))
    if difficulty is not None:
        payload["difficulty"] = difficulty

    logger.info("CSV row %s подготовлен для вставки", row_number)
    return payload


def _iter_chunks(items: list[dict], chunk_size: int = 100):
    for i in range(0, len(items), chunk_size):
        yield items[i : i + chunk_size]


def _parse_csv_questions(csv_text: str) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    questions: list[dict] = []
    topic_cache: dict[str, int] = {}

    delimiter = _csv_delimiter(csv_text[:4096])
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
    if not reader.fieldnames:
        return [], ["CSV пустой или без заголовка"]

    normalized_fieldnames = [field.strip().lower() for field in reader.fieldnames]
    required = {"q", "a1", "a2", "a3", "a4", "correct", "is_active"}
    missing = sorted(required - set(normalized_fieldnames))
    if missing:
        return [], [f"Отсутствуют обязательные колонки: {', '.join(missing)}"]

    for row_index, raw_row in enumerate(reader, start=2):
        row = {str(k).strip().lower(): (v or "") for k, v in raw_row.items() if k is not None}
        try:
            payload = _build_question_from_csv_row(row, row_index, topic_cache)
            if payload is None:
                continue
            questions.append(payload)
        except Exception as exc:
            errors.append(f"Строка {row_index}: {exc}")

    return questions, errors


def _bulk_insert_questions(payloads: list[dict], errors: list[str]) -> tuple[int, int]:
    inserted = 0
    duplicates = 0
    for chunk in _iter_chunks(payloads, chunk_size=100):
        chunk_inserted = 0
        chunk_duplicates = 0
        for payload in chunk:
            try:
                db.client.table("questions").insert(payload).execute()
                inserted += 1
                chunk_inserted += 1
            except Exception as exc:
                if _is_duplicate_q_hash_error(exc):
                    duplicates += 1
                    chunk_duplicates += 1
                    continue
                errors.append(f"Вставка '{payload.get('text', '')[:80]}': {exc}")
        logger.info(
            "Импорт CSV: обработан чанк size=%s inserted=%s duplicates=%s",
            len(chunk),
            chunk_inserted,
            chunk_duplicates,
        )
    return inserted, duplicates


def _stats_message(st: dict) -> str:
    until = st["unlimited_until"].isoformat() if st["unlimited_until"] else "нет"
    progress_today = "безлимит" if st["unlimited_until"] else f"{st['correct_today']}/{quiz.DAILY_LIMIT}"
    return "\n".join(
        [
            f"Всего верных: {st['total_correct']}",
            f"Всего ошибок: {st['total_wrong']}",
            f"Лучшая серия: {st['best_streak']}",
            f"Серия сегодня: {st['streak_today']}",
            f"Прогресс за сегодня: {progress_today}",
            f"Безлимит до: {until}",
        ]
    )


@dp.message(Command("start"))
async def cmd_start(message: Message) -> None:
    user = message.from_user
    tg_id = user.id
    db.upsert_user(tg_id, user.first_name, user.username)
    db.ensure_user_settings(tg_id)
    quiz.ensure_day_row(tg_id)

    allowed, reason = quiz.can_start_quiz_now(tg_id)
    if not allowed:
        await message.answer(f"{WELCOME}\n\n{reason}", reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)))
        return

    quiz.reset_session(tg_id)
    await message.answer(WELCOME, reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)))
    await send_next_question(message, tg_id)


@dp.message(F.text == "Начать")
async def begin_quiz(message: Message) -> None:
    tg_id = message.from_user.id
    allowed, reason = quiz.can_start_quiz_now(tg_id)
    if not allowed:
        await message.answer(reason or BLOCKED, reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)))
        return
    quiz.reset_session(tg_id)
    await send_next_question(message, tg_id)


@dp.callback_query(F.data.startswith("ans:"))
async def answer_handler(callback: CallbackQuery) -> None:
    raw_data = callback.data or ""
    logger.info("Received answer callback: user_id=%s data=%s", callback.from_user.id, raw_data)

    parts = raw_data.split(":")
    if len(parts) != 3:
        logger.warning("Malformed answer callback_data format: data=%s", raw_data)
        await callback.answer("Некорректный ответ", show_alert=True)
        return

    _, qid_s, answer_s = parts
    if (not qid_s.isdigit()) or (not answer_s.isdigit()):
        logger.warning("Malformed answer callback_data values: data=%s", raw_data)
        await callback.answer("Некорректный ответ", show_alert=True)
        return

    qid = int(qid_s)
    answer = int(answer_s)
    if answer < 1 or answer > 4:
        logger.warning("Answer choice out of range: data=%s", raw_data)
        await callback.answer("Некорректный вариант", show_alert=True)
        return

    logger.info("Parsed answer callback: user_id=%s question_id=%s answer=%s", callback.from_user.id, qid, answer)
    tg_id = callback.from_user.id

    question = quiz.get_question_by_id(qid)
    if not question:
        logger.warning("Question not found for callback: user_id=%s question_id=%s", tg_id, qid)
        await callback.answer("Вопрос не найден", show_alert=True)
        return

    ok, status = quiz.save_answer(tg_id, question, answer)
    if not ok and status == "already_answered":
        await callback.answer("Ответ уже принят")
        return
    if not ok and status == "save_failed":
        await callback.answer("Не удалось сохранить ответ", show_alert=True)
        return
    if not ok:
        await callback.answer("Этот вопрос уже не активен")
        return

    await callback.answer("Принято")

    if status == "blocked":
        await callback.message.answer(WRONG_STOP, reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)))
        return

    if status == "daily_done":
        await callback.message.answer(DAILY_DONE, reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)))
        return

    if status == "correct":
        await callback.message.answer("✅ Верно")
        await send_next_question(callback.message, tg_id)
        return

    if status == "wrong":
        await callback.message.answer("❌ Неверно")
        await send_next_question(callback.message, tg_id)
        return

    await callback.message.answer("Есть ошибка.", reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)))


@dp.callback_query(F.data == "next")
async def next_handler(callback: CallbackQuery) -> None:
    tg_id = callback.from_user.id
    allowed, reason = quiz.can_start_quiz_now(tg_id)
    if not allowed:
        await callback.message.answer(reason or BLOCKED, reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)))
        await callback.answer()
        return
    await callback.answer()
    await send_next_question(callback.message, tg_id)


@dp.callback_query(F.data == "menu")
async def menu_handler(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer("Открыл меню", reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(callback.from_user.id)))


@dp.message(F.text == "Меню")
async def menu_button(message: Message) -> None:
    await message.answer("Выбери действие", reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(message.from_user.id)))


def _leaderboard_title(metric: str) -> str:
    return "Всего верных" if metric == "total_correct" else "Лучшая серия"


def _metric_emoji(metric: str) -> str:
    return "✅" if metric == "total_correct" else "🔥"


def _leaderboard_message(metric: str, rows: list[dict], current_rank: int) -> str:
    title = _leaderboard_title(metric)
    emoji = _metric_emoji(metric)
    lines = [f"<b>Рейтинг: {title}</b>"]

    if not rows:
        lines.append("Пока нет данных")
    else:
        for i, row in enumerate(rows, start=1):
            name = row.get("username") or row.get("first_name") or str(row["tg_id"])
            value = int(row.get(metric, 0))
            lines.append(f"{i}. {name}: {emoji} {value}")

    lines.append("")
    lines.append(f"Ваше место: {current_rank}")
    return "\n".join(lines)


@dp.message(Command("rating"))
async def cmd_rating(message: Message) -> None:
    await message.answer("Выбери тип рейтинга:", reply_markup=rating_type_kb())


@dp.message(F.text == "Рейтинг")
async def rating_button(message: Message) -> None:
    await message.answer("Выбери тип рейтинга:", reply_markup=rating_type_kb())


@dp.callback_query(F.data.startswith("rating:"))
async def rating_type_handler(callback: CallbackQuery) -> None:
    metric = callback.data.split(":", maxsplit=1)[1]
    if metric not in {"total_correct", "best_streak"}:
        await callback.answer("Неизвестный тип рейтинга", show_alert=True)
        return

    rows = rating.top10(metric)
    current_rank = rating.user_rank(callback.from_user.id, metric)
    await callback.message.answer(_leaderboard_message(metric, rows, current_rank))
    await callback.answer()


@dp.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    st = rating.user_stats(message.from_user.id)
    await message.answer(_stats_message(st))


@dp.message(F.text == "Моя статистика")
async def my_stats_button(message: Message) -> None:
    st = rating.user_stats(message.from_user.id)
    await message.answer(_stats_message(st))


@dp.callback_query(F.data.startswith("buy:"))
async def buy_handler(callback: CallbackQuery) -> None:
    kind = callback.data.split(":", maxsplit=1)[1]
    if not settings.monetization_enabled:
        await callback.answer("Покупки временно недоступны", show_alert=True)
        return

    if kind == payments.PACK10:
        title = "Пакет +10 вопросов"
        description = "Открывает +10 вопросов прямо сейчас"
        amount = settings.pack10_stars
    else:
        title = "Безлимит 30 дней"
        description = "Бесконечный доступ + гибкие режимы"
        amount = settings.unlimited30_stars

    payload = payments.payload_for_kind(kind)
    await bot.send_invoice(
        chat_id=callback.from_user.id,
        title=title,
        description=description,
        payload=payload,
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=title, amount=amount)],
    )
    await callback.answer()


@dp.pre_checkout_query()
async def pre_checkout(pre_checkout_query: PreCheckoutQuery) -> None:
    await bot.answer_pre_checkout_query(pre_checkout_query.id, ok=True)


@dp.message(F.successful_payment)
async def successful_payment(message: Message) -> None:
    payment = message.successful_payment
    tg_id = message.from_user.id
    payload = payment.invoice_payload
    kind = payments.kind_from_payload(payload)

    if not settings.monetization_enabled:
        logger.info("Ignoring successful_payment while monetization disabled: tg_id=%s payload=%s", tg_id, payload)
        return

    if kind is None:
        await message.answer("Не удалось определить тип покупки. Напиши администратору.")
        return

    expected_amount = settings.pack10_stars if kind == payments.PACK10 else settings.unlimited30_stars
    if payment.total_amount != expected_amount:
        logger.error(
            "Payment amount mismatch: tg_id=%s payload=%s expected=%s got=%s",
            tg_id,
            payload,
            expected_amount,
            payment.total_amount,
        )
        await message.answer("Ошибка при обработке оплаты, мы уже видим платеж. Напиши администратору.")
        return

    try:
        result = entitlements.grant_purchase(
            tg_id=tg_id,
            payload=payload,
            amount=payment.total_amount,
            currency=payment.currency,
            charge_id=payment.telegram_payment_charge_id,
            is_test=False,
        )
        if result.get("duplicate"):
            await message.answer("Оплата уже учтена ✅")
            return

        if kind == payments.PACK10:
            await message.answer("✅ Оплата принята. Добавлено +10 вопросов.", reply_markup=start_kb(has_unlimited=quiz.has_unlimited_now(tg_id)))
            return

        until = datetime.fromisoformat(result["new_until"])
        until_local = until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        await message.answer(
            f"✅ Безлимит активирован до {until_local}.",
            reply_markup=start_kb(has_unlimited=True),
        )
    except Exception:
        logger.exception("Payment processing failed: tg_id=%s payload=%s", tg_id, payload)
        await message.answer("Ошибка при обработке оплаты, мы уже видим платеж. Напиши администратору.")


if settings.test_mode:
    @dp.message(Command("test_pay_pack10"))
    async def cmd_test_pay_pack10(message: Message) -> None:
        if not can_use_test_commands(message.from_user.id):
            return
        await process_test_payment(message, payments.PACK10_PAYLOAD, settings.pack10_stars)


    @dp.message(Command("test_pay_unlimited30"))
    async def cmd_test_pay_unlimited30(message: Message) -> None:
        if not can_use_test_commands(message.from_user.id):
            return
        await process_test_payment(message, payments.UNLIMITED30_PAYLOAD, settings.unlimited30_stars)


@dp.message(Command("my_payments"))
async def cmd_my_payments(message: Message) -> None:
    summary = payments.get_user_purchases_summary(message.from_user.id)
    unlimited_until = summary["unlimited_until"]
    now = datetime.now(timezone.utc)

    if unlimited_until and unlimited_until > now:
        unlimited_line = f"активен до {unlimited_until.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
    else:
        unlimited_line = "не активен"

    lines = [
        "<b>Мои покупки</b>",
        f"Пакеты +10: {summary['packs_available']}",
        f"Безлимит: {unlimited_line}",
        "",
        "Последние платежи:",
    ]

    recent = summary["recent_payments"]
    if not recent:
        lines.append("— пока нет")
    else:
        for row in recent:
            created_at = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")).astimezone(timezone.utc)
            lines.append(
                f"— {created_at.strftime('%Y-%m-%d %H:%M')} | {row['invoice_payload']} | {row['total_amount']} {row['currency']}"
            )

    await message.answer("\n".join(lines), reply_markup=start_kb(has_unlimited=bool(unlimited_until and unlimited_until > now)))


@dp.message(F.text == "Мои покупки")
async def my_payments_button(message: Message) -> None:
    await cmd_my_payments(message)


@dp.message(F.text == "Настройки безлимита")
async def unlimited_settings(message: Message) -> None:
    if not quiz.has_unlimited_now(message.from_user.id):
        await message.answer("Опция доступна только при активном безлимите", reply_markup=buy_kb(settings.monetization_enabled))
        return
    await message.answer("Выбери режим выдачи:", reply_markup=unlimited_settings_kb())


@dp.callback_query(F.data.startswith("setmode:"))
async def setmode_handler(callback: CallbackQuery, state: FSMContext) -> None:
    mode = callback.data.split(":", maxsplit=1)[1]
    tg_id = callback.from_user.id
    if mode == "random":
        db.client.table("user_settings").update({"mode": "random", "topic_id": None, "difficulty": None}).eq("tg_id", tg_id).execute()
        await callback.message.answer("Режим random включён")
    elif mode == "topic":
        rows = db.client.table("topics").select("id,title").eq("is_active", True).limit(100).execute().data or []
        if not rows:
            await callback.message.answer("Нет активных тем")
            return
        listing = "\n".join([f"{r['id']}: {r['title']}" for r in rows])
        await state.set_state(UnlimitedFSM.topic)
        await callback.message.answer(f"Отправь ID темы:\n{listing}")
    else:
        await state.set_state(UnlimitedFSM.difficulty)
        await callback.message.answer("Отправь сложность 1..5")
    await callback.answer()


@dp.message(UnlimitedFSM.topic)
async def set_topic(message: Message, state: FSMContext) -> None:
    tg_id = message.from_user.id
    topic_id = int(message.text.strip())
    db.client.table("user_settings").update({"mode": "topic", "topic_id": topic_id, "difficulty": None}).eq("tg_id", tg_id).execute()
    await message.answer("Режим topic включён")
    await state.clear()


@dp.message(UnlimitedFSM.difficulty)
async def set_difficulty(message: Message, state: FSMContext) -> None:
    tg_id = message.from_user.id
    difficulty = int(message.text.strip())
    if difficulty < 1 or difficulty > 5:
        await message.answer("Нужно число 1..5")
        return
    db.client.table("user_settings").update({"mode": "difficulty", "difficulty": difficulty, "topic_id": None}).eq("tg_id", tg_id).execute()
    await message.answer("Режим difficulty включён")
    await state.clear()


@dp.message(Command("admin_stats"))
async def cmd_admin_stats(message: Message) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        return
    st = admin_logic.admin_stats()
    await message.answer(
        f"Пользователей: {st['total_users']}\nОтветов: {st['total_answers']}\nАктивных безлимитов: {st['active_unlimited']}"
    )


@dp.message(Command("admin"))
async def cmd_admin(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        return
    await state.clear()
    await message.answer("Админ-меню:", reply_markup=admin_menu_kb())


@dp.callback_query(F.data == "admin:add_question")
async def admin_add_question(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await state.set_state(AddQuestionFSM.text)
    await callback.message.answer("Текст вопроса?")
    await callback.answer()


@dp.callback_query(F.data == "admin:bulk_import")
async def admin_bulk_import_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminFSM.bulk_import)
    await callback.message.answer(
        "Отправь текст импорта. Один блок = один вопрос.\n"
        "Можно с разделителем --- или без него (тогда каждый новый блок начинается с Q:/В:).\n\n"
        "Формат:\n"
        "Q: <текст вопроса> (или В:)\n"
        "A) <вариант 1> / A: <вариант 1>\n"
        "B) <вариант 2> / B: <вариант 2>\n"
        "C) <вариант 3> / C: <вариант 3>\n"
        "D) <вариант 4> / D: <вариант 4>\n"
        "ANS: <A|B|C|D>\nTOPIC_ID: <число, необязательно>\nDIFF: <1-5 необязательно>\nACTIVE: <true|false необязательно>\n\n"
        "Если TOPIC_ID / DIFF не указаны — сохранятся как пустые. ACTIVE по умолчанию true.",
        parse_mode=None,
    )
    await callback.answer()


@dp.message(AdminFSM.bulk_import)
async def admin_bulk_import_input(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        await state.clear()
        return

    blocks = _split_bulk_blocks((message.text or "").strip())
    if not blocks:
        await message.answer("Не нашёл ни одного блока для импорта")
        return

    ok_count = 0
    duplicate_count = 0
    errors: list[str] = []
    valid_payloads: list[dict] = []

    for idx, block in enumerate(blocks, start=1):
        try:
            payload = _parse_bulk_block(block)
            valid_payloads.append(payload)
        except Exception as exc:
            errors.append(f"Блок {idx}: {exc}")

    for payload in valid_payloads:
        try:
            db.client.table("questions").insert(payload).execute()
            ok_count += 1
        except Exception as exc:
            if _is_duplicate_q_hash_error(exc):
                duplicate_count += 1
                continue
            errors.append(f"Вставка '{payload.get('q', '')[:80]}': {exc}")

    await state.clear()
    await message.answer(_bulk_import_report(ok_count=ok_count, duplicate_count=duplicate_count, errors=errors))


@dp.callback_query(F.data == "admin:file_import")
async def admin_file_import_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminFSM.file_import)
    await callback.message.answer(
        "Прикрепи CSV файлом (document) и отправь в чат. "
        "Поддерживаются разделители ',' и ';', кодировки UTF-8 и cp1251."
    )
    logger.info("Админ %s запустил импорт вопросов файлом", callback.from_user.id)
    await callback.answer()


@dp.message(AdminFSM.file_import)
async def admin_file_import_input(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        await state.clear()
        return

    document: Document | None = message.document
    if document is None:
        await message.answer("Нужен CSV файл как document")
        return

    if not document.file_name or not document.file_name.lower().endswith(".csv"):
        await message.answer("Нужен файл с расширением .csv")
        return

    logger.info(
        "Старт обработки CSV: admin=%s file_name=%s file_id=%s size=%s",
        message.from_user.id,
        document.file_name,
        document.file_id,
        document.file_size,
    )

    try:
        file = await bot.get_file(document.file_id)
        content = await bot.download_file(file.file_path)
        csv_bytes = content.read()
    except Exception as exc:
        logger.exception("Ошибка скачивания CSV из Telegram")
        await message.answer(f"Не удалось скачать файл: {exc}")
        return

    try:
        csv_text = _decode_csv_bytes(csv_bytes)
    except Exception as exc:
        logger.exception("Ошибка декодирования CSV")
        await message.answer(f"Не удалось прочитать CSV: {exc}")
        return

    payloads, errors = _parse_csv_questions(csv_text)
    inserted, duplicates = _bulk_insert_questions(payloads, errors) if payloads else (0, 0)

    logger.info(
        "Импорт CSV завершен: admin=%s inserted=%s duplicates=%s errors=%s",
        message.from_user.id,
        inserted,
        duplicates,
        len(errors),
    )

    await state.clear()
    await message.answer(
        _bulk_import_report(ok_count=inserted, duplicate_count=duplicates, errors=errors),
        parse_mode=None,
    )


@dp.callback_query(F.data == "admin:list_questions")
async def admin_list_questions(callback: CallbackQuery) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return

    rows = (
        db.client.table("questions")
        .select("id,text,is_active")
        .order("id", desc=True)
        .limit(10)
        .execute()
        .data
        or []
    )
    if not rows:
        await callback.message.answer("Вопросов пока нет")
    else:
        lines = ["Последние 10 вопросов:"]
        for row in rows:
            status = "✅" if row.get("is_active") else "⛔"
            text = (row.get("text") or "").replace("\n", " ").strip()
            short_text = text[:70] + "..." if len(text) > 70 else text
            lines.append(f"{row['id']}. {status} {short_text}")
        await callback.message.answer("\n".join(lines))
    await callback.answer()


@dp.callback_query(F.data == "admin:toggle_question")
async def admin_toggle_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.set_state(AdminFSM.toggle_question)
    await callback.message.answer("Отправь ID вопроса для переключения is_active")
    await callback.answer()


@dp.message(AdminFSM.toggle_question)
async def admin_toggle_question(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Нужен числовой ID вопроса")
        return

    qid = int(text)
    rows = db.client.table("questions").select("id,is_active").eq("id", qid).limit(1).execute().data or []
    if not rows:
        await message.answer("Вопрос не найден")
        return

    is_active = bool(rows[0]["is_active"])
    db.client.table("questions").update({"is_active": not is_active}).eq("id", qid).execute()
    await state.clear()
    await message.answer(f"Статус вопроса {qid}: {'активен' if not is_active else 'выключен'}")


@dp.callback_query(F.data == "admin:grant_admin")
async def admin_grant_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.set_state(AdminFSM.grant_admin)
    await callback.message.answer("Отправь tg_id нового админа")
    await callback.answer()


@dp.message(AdminFSM.grant_admin)
async def admin_grant_input(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Нужен числовой tg_id")
        return

    target = int(text)
    ok = admin_logic.grant_admin(message.from_user.id, target, "editor")
    await state.clear()
    await message.answer("Админка выдана (role=editor)" if ok else "Недостаточно прав")


@dp.callback_query(F.data == "admin:grant_unlimited")
async def admin_grant_unlimited_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminFSM.grant_unlimited_tg_id)
    await callback.message.answer("Отправь tg_id пользователя для выдачи безлимита")
    await callback.answer()


@dp.message(AdminFSM.grant_unlimited_tg_id)
async def admin_grant_unlimited_tg_id_input(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Нужен числовой tg_id")
        return

    await state.update_data(target_tg_id=int(text))
    await state.set_state(AdminFSM.grant_unlimited_manual_days)
    await message.answer("Выбери срок безлимита:", reply_markup=admin_unlimited_days_kb())


@dp.callback_query(F.data.startswith("admin:grant_unlimited_days:"))
async def admin_grant_unlimited_days_pick(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        await state.clear()
        return

    state_data = await state.get_data()
    target_tg_id = state_data.get("target_tg_id")
    if target_tg_id is None:
        await callback.answer("Сначала укажи tg_id", show_alert=True)
        await state.clear()
        return

    choice = callback.data.split(":")[-1]
    if choice == "manual":
        await state.set_state(AdminFSM.grant_unlimited_manual_days)
        await callback.message.answer("Введи число дней (1..365)")
        await callback.answer()
        return

    days = int(choice)
    new_until = entitlements.grant_unlimited_days(target_tg_id, days)
    until_utc = new_until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info(
        "Админ выдал безлимит: admin=%s target=%s days=%s until=%s",
        callback.from_user.id,
        target_tg_id,
        days,
        new_until.isoformat(),
    )
    await state.clear()
    await callback.message.answer(f"✅ Безлимит выдан пользователю {target_tg_id} до {until_utc} (добавлено {days} дней)")
    await callback.answer()


@dp.message(AdminFSM.grant_unlimited_manual_days)
async def admin_grant_unlimited_manual_days_input(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        await state.clear()
        return

    state_data = await state.get_data()
    target_tg_id = state_data.get("target_tg_id")
    if target_tg_id is None:
        await state.clear()
        await message.answer("Сначала выбери выдачу безлимита через админ-меню")
        return

    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Нужно число дней 1..365")
        return

    days = int(text)
    if days < 1 or days > 365:
        await message.answer("Нужно число дней 1..365")
        return

    new_until = entitlements.grant_unlimited_days(target_tg_id, days)
    until_utc = new_until.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    logger.info(
        "Админ выдал безлимит: admin=%s target=%s days=%s until=%s",
        message.from_user.id,
        target_tg_id,
        days,
        new_until.isoformat(),
    )
    await state.clear()
    await message.answer(f"✅ Безлимит выдан пользователю {target_tg_id} до {until_utc} (добавлено {days} дней)")


@dp.callback_query(F.data == "admin:revoke_unlimited")
async def admin_revoke_unlimited_prompt(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await state.set_state(AdminFSM.revoke_unlimited_tg_id)
    await callback.message.answer("Отправь tg_id пользователя для снятия безлимита")
    await callback.answer()


@dp.message(AdminFSM.revoke_unlimited_tg_id)
async def admin_revoke_unlimited_input(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        await state.clear()
        return

    text = (message.text or "").strip()
    if not text.isdigit():
        await message.answer("Нужен числовой tg_id")
        return

    target_tg_id = int(text)
    revoked_at = entitlements.revoke_unlimited(target_tg_id)
    logger.info(
        "Админ снял безлимит: admin=%s target=%s revoked_at=%s",
        message.from_user.id,
        target_tg_id,
        revoked_at.isoformat(),
    )
    await state.clear()
    await message.answer(f"✅ Безлимит снят у пользователя {target_tg_id}")


@dp.message(Command("grant_admin"))
async def cmd_grant_admin(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: /grant_admin <tg_id> <role>")
        return
    target = int(parts[1])
    role = parts[2].strip()
    ok = admin_logic.grant_admin(message.from_user.id, target, role)
    await message.answer("OK" if ok else "Недостаточно прав")


@dp.message(Command("revoke_admin"))
async def cmd_revoke_admin(message: Message) -> None:
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /revoke_admin <tg_id>")
        return
    target = int(parts[1])
    ok = admin_logic.revoke_admin(message.from_user.id, target)
    await message.answer("OK" if ok else "Недостаточно прав")


@dp.message(Command("add_question"))
async def cmd_add_question(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        return
    await state.clear()
    await state.set_state(AddQuestionFSM.text)
    await message.answer("Текст вопроса?")


@dp.message(AddQuestionFSM.text)
async def aq_text(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Текст не должен быть пустым")
        return
    await state.update_data(text=text)
    await state.set_state(AddQuestionFSM.option1)
    await message.answer("Вариант 1?")


@dp.message(AddQuestionFSM.option1)
async def aq_o1(message: Message, state: FSMContext) -> None:
    await state.update_data(option1=(message.text or "").strip())
    await state.set_state(AddQuestionFSM.option2)
    await message.answer("Вариант 2?")


@dp.message(AddQuestionFSM.option2)
async def aq_o2(message: Message, state: FSMContext) -> None:
    await state.update_data(option2=(message.text or "").strip())
    await state.set_state(AddQuestionFSM.option3)
    await message.answer("Вариант 3?")


@dp.message(AddQuestionFSM.option3)
async def aq_o3(message: Message, state: FSMContext) -> None:
    await state.update_data(option3=(message.text or "").strip())
    await state.set_state(AddQuestionFSM.option4)
    await message.answer("Вариант 4?")


@dp.message(AddQuestionFSM.option4)
async def aq_o4(message: Message, state: FSMContext) -> None:
    await state.update_data(option4=(message.text or "").strip())
    await state.set_state(AddQuestionFSM.correct_option)
    await message.answer("Правильный вариант (1..4)?")


@dp.message(AddQuestionFSM.correct_option)
async def aq_correct(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text not in {"1", "2", "3", "4"}:
        await message.answer("Нужно число 1..4")
        return
    await state.update_data(correct_option=int(text))
    rows = db.client.table("topics").select("id,title").eq("is_active", True).order("id").limit(100).execute().data or []
    if rows:
        topic_lines = [f"{row['id']}: {row['title']}" for row in rows]
        await message.answer(
            "Тема (опционально): отправь ID из списка или название новой темы. Для пропуска отправь -\n"
            + "\n".join(topic_lines)
        )
    else:
        await message.answer("Тема (опционально): отправь название новой темы или '-' для пропуска")
    await state.set_state(AddQuestionFSM.topic)


@dp.message(AddQuestionFSM.topic)
async def aq_topic(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    topic_id = None
    if text and text != "-":
        if text.isdigit():
            rows = db.client.table("topics").select("id").eq("id", int(text)).limit(1).execute().data or []
            if not rows:
                await message.answer("Тема с таким ID не найдена")
                return
            topic_id = int(text)
        else:
            created = db.client.table("topics").insert({"title": text, "is_active": True}).execute().data or []
            if not created:
                await message.answer("Не удалось создать тему")
                return
            topic_id = int(created[0]["id"])
    await state.update_data(topic_id=topic_id)
    await state.set_state(AddQuestionFSM.difficulty)
    await message.answer("Сложность 1..5 (опционально), или '-' для пропуска")


@dp.message(AddQuestionFSM.difficulty)
async def aq_done(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    difficulty = None
    if text and text != "-":
        if not text.isdigit() or not (1 <= int(text) <= 5):
            await message.answer("Нужно число 1..5 или '-' для пропуска")
            return
        difficulty = int(text)

    data = await state.get_data()
    payload = {
        "text": data["text"],
        "option1": data["option1"],
        "option2": data["option2"],
        "option3": data["option3"],
        "option4": data["option4"],
        "correct_option": data["correct_option"],
        "is_active": True,
    }
    if data.get("topic_id") is not None:
        payload["topic_id"] = data["topic_id"]
    if difficulty is not None:
        payload["difficulty"] = difficulty

    db.client.table("questions").insert(payload).execute()
    await state.clear()
    await message.answer("Вопрос добавлен")


@dp.message(Command("toggle_question"))
async def cmd_toggle_question(message: Message) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 2:
        await message.answer("Использование: /toggle_question <id>")
        return
    qid = int(parts[1])
    row = db.client.table("questions").select("id,is_active").eq("id", qid).single().execute().data
    db.client.table("questions").update({"is_active": not bool(row["is_active"])}).eq("id", qid).execute()
    await message.answer("Статус переключён")


async def main() -> None:
    db.ensure_schema()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
