from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, LabeledPrice, Message, PreCheckoutQuery

from src.config import settings
from src.db import db
from src.logic import admin as admin_logic
from src.logic import entitlements, payments, quiz, rating
from src.ui.keyboards import (
    admin_menu_kb,
    answers_kb,
    buy_kb,
    next_question_kb,
    rating_type_kb,
    start_kb,
    unlimited_settings_kb,
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




def _split_bulk_blocks(raw_text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    for line in raw_text.splitlines():
        normalized = line.strip()
        if not normalized:
            current.append(line)
            continue

        if normalized == "---":
            block = "\n".join(current).strip()
            if block:
                blocks.append(block)
            current = []
            continue
        current.append(line)
    tail = "\n".join(current).strip()
    if tail:
        blocks.append(tail)
    return blocks


def _parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y", "да"}:
        return True
    if normalized in {"false", "0", "no", "n", "нет"}:
        return False
    return None


def _resolve_topic_id(topic_raw: str) -> int:
    topic_value = topic_raw.strip()
    if not topic_value:
        raise ValueError("TOPIC_ID: пустое значение")
    if not topic_value.isdigit():
        raise ValueError("TOPIC_ID должен быть числом")
    return int(topic_value)


def _parse_bulk_block(block: str) -> dict:
    payload: dict = {"is_active": True}
    options: dict[str, str] = {}

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        option_match = re.match(r"^([ABCD])\)\s*(.+)$", line, flags=re.IGNORECASE)
        if option_match:
            letter = option_match.group(1).upper()
            options[letter] = option_match.group(2).strip()
            continue

        q_match = re.match(r"^(?:Q|В)\s*:\s*(.+)$", line, flags=re.IGNORECASE)
        if q_match:
            payload["q"] = q_match.group(1).strip()
            continue

        field_match = re.match(r"^([A-ZА-Я_]+)\s*:\s*(.*)$", line, flags=re.IGNORECASE)
        if not field_match:
            raise ValueError(f"непонятная строка: {line}")

        key = field_match.group(1).upper()
        value = field_match.group(2).strip()

        if key == "ANS":
            answer_letter = value.upper()
            if answer_letter not in {"A", "B", "C", "D"}:
                raise ValueError("ANS должен быть A/B/C/D")
            payload["correct"] = {"A": 1, "B": 2, "C": 3, "D": 4}[answer_letter]
        elif key == "TOPIC_ID":
            if value:
                payload["topic_id"] = _resolve_topic_id(value)
        elif key == "DIFF":
            if not value:
                continue
            if not value.isdigit() or not (1 <= int(value) <= 5):
                raise ValueError("DIFF должен быть числом 1..5")
            payload["difficulty"] = int(value)
        elif key == "ACTIVE":
            if not value:
                continue
            bool_value = _parse_bool(value)
            if bool_value is None:
                raise ValueError("ACTIVE должен быть true/false")
            payload["is_active"] = bool_value
        elif key in {"Q", "В"}:
            payload["q"] = value
        else:
            raise ValueError(f"неизвестное поле {key}")

    if not payload.get("q"):
        raise ValueError("не заполнен Q")

    for letter in ("A", "B", "C", "D"):
        if not options.get(letter):
            raise ValueError(f"отсутствует вариант {letter}")

    if "correct" not in payload:
        raise ValueError("не заполнен ANS")

    payload.update(
        {
            "a1": options["A"],
            "a2": options["B"],
            "a3": options["C"],
            "a4": options["D"],
        }
    )
    return payload


def _bulk_import_report(ok_count: int, skipped_count: int, errors: list[str]) -> str:
    lines = [
        f"Импорт: добавлено {ok_count}, ошибок {len(errors)}",
        f"Пропущено дублей: {skipped_count}",
    ]
    if errors:
        lines.append("")
        lines.append("Первые ошибки:")
        lines.extend(errors[:5])
    return "\n".join(lines)

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
    _, qid_s, answer_s = callback.data.split(":")
    qid = int(qid_s)
    answer = int(answer_s)
    tg_id = callback.from_user.id

    question = quiz.get_question_by_id(qid)
    if not question:
        await callback.answer("Вопрос не найден", show_alert=True)
        return

    ok, status = quiz.save_answer(tg_id, question, answer)
    if not ok and status == "already_answered":
        await callback.answer("Ответ уже принят")
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
        await callback.message.answer("Верно ✅", reply_markup=next_question_kb())
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
        "Отправь текст импорта. Один блок = один вопрос, разделитель блоков: ---\n\n"
        "Формат:\n"
        "Q: <текст вопроса> (или В:)\n"
        "A) <вариант 1>\nB) <вариант 2>\nC) <вариант 3>\nD) <вариант 4>\n"
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
    skipped_count = 0
    errors: list[str] = []

    for idx, block in enumerate(blocks, start=1):
        try:
            payload = _parse_bulk_block(block)
            existing = db.client.table("questions").select("id").eq("q", payload["q"]).limit(1).execute().data or []
            if existing:
                skipped_count += 1
                continue
            db.client.table("questions").insert(payload).execute()
            ok_count += 1
        except Exception as exc:
            errors.append(f"Блок {idx}: {exc}")

    await state.clear()
    await message.answer(_bulk_import_report(ok_count=ok_count, skipped_count=skipped_count, errors=errors))


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
