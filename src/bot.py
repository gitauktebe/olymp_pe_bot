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
    admin_question_active_filter_kb,
    admin_question_correct_kb,
    admin_question_preview_kb,
    admin_question_topic_filter_kb,
    admin_questions_item_kb,
    admin_questions_nav_kb,
    admin_topics_choose_kb,
    admin_topics_kb,
    admin_topics_manage_kb,
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
    if topic_value.isdigit():
        rows = db.client.table("topics").select("id").eq("id", int(topic_value)).limit(1).execute().data or []
        if not rows:
            raise ValueError("TOPIC: тема с таким ID не найдена")
        return int(topic_value)

    rows = db.client.table("topics").select("id").eq("title", topic_value).limit(1).execute().data or []
    if rows:
        return int(rows[0]["id"])

    created = db.client.table("topics").insert({"title": topic_value, "is_active": True}).execute().data or []
    if not created:
        raise ValueError("TOPIC: не удалось создать тему")
    return int(created[0]["id"])


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
            payload["text"] = q_match.group(1).strip()
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
            payload["correct_option"] = {"A": 1, "B": 2, "C": 3, "D": 4}[answer_letter]
        elif key == "TOPIC":
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
            payload["text"] = value
        else:
            raise ValueError(f"неизвестное поле {key}")

    if not payload.get("text"):
        raise ValueError("не заполнен Q")

    for letter in ("A", "B", "C", "D"):
        if not options.get(letter):
            raise ValueError(f"отсутствует вариант {letter}")

    if "correct_option" not in payload:
        raise ValueError("не заполнен ANS")

    options_list = [options["A"], options["B"], options["C"], options["D"]]
    payload.update(
        {
            "type": "single",
            "prompt": payload["text"],
            "options": options_list,
            "answer": {"correct": [payload["correct_option"]]},
            "option1": options["A"],
            "option2": options["B"],
            "option3": options["C"],
            "option4": options["D"],
        }
    )
    return payload


def _bulk_import_report(total: int, ok_count: int, errors: list[str]) -> str:
    lines = [
        "Импорт завершён.",
        f"Успешно добавлено: {ok_count}",
        f"С ошибками: {len(errors)} из {total}",
    ]
    if errors:
        lines.append("")
        lines.append("Первые ошибки:")
        lines.extend(errors[:3])
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


@dp.callback_query(F.data == "admin:menu")
async def admin_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.clear()
    await callback.message.answer("Админ-меню:", reply_markup=admin_menu_kb())
    await callback.answer()


@dp.callback_query(F.data == "admin:bulk_import")
async def admin_bulk_import_start(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await state.set_state(AdminFSM.bulk_import)
    await callback.message.answer(
        "Отправьте вопросы пачкой. Один блок = один вопрос, разделитель блоков — строка ---\n\n"
        "Поддерживаемый формат:\n"
        "Q: <текст вопроса>\n"
        "A) <вариант 1>\n"
        "B) <вариант 2>\n"
        "C) <вариант 3>\n"
        "D) <вариант 4>\n"
        "ANS: <A|B|C|D>\n"
        "TOPIC: <необязательно>\n"
        "DIFF: <1-5 необязательно>\n"
        "ACTIVE: <true|false необязательно, по умолчанию true>"
    )
    await callback.answer()


@dp.message(AdminFSM.bulk_import)
async def admin_bulk_import_input(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        await state.clear()
        return

    raw_text = message.text or ""
    if raw_text.strip().lower() in {"/cancel", "отмена"}:
        await state.clear()
        await message.answer("Импорт отменён")
        return

    blocks = _split_bulk_blocks(raw_text)
    if not blocks:
        await message.answer("Не найдено ни одного блока. Проверьте формат и разделитель ---")
        return

    ok_count = 0
    errors: list[str] = []
    for idx, block in enumerate(blocks, start=1):
        try:
            payload = _parse_bulk_block(block)
            db.client.table("questions").insert(payload).execute()
            ok_count += 1
        except Exception as exc:
            errors.append(f"#{idx}: {exc}")

    await message.answer(_bulk_import_report(total=len(blocks), ok_count=ok_count, errors=errors))
    await state.clear()


class AddQuestionTypeSingleFSM(StatesGroup):
    topic = State()
    new_topic = State()
    prompt = State()
    option_a = State()
    option_b = State()
    option_c = State()
    option_d = State()


class TopicsFSM(StatesGroup):
    create = State()


def _question_prompt(row: dict) -> str:
    return (row.get("prompt") or row.get("text") or "").strip()


def _question_options(row: dict) -> list[str]:
    opts = row.get("options") or []
    if isinstance(opts, list) and len(opts) == 4:
        return [str(x) for x in opts]
    return [row.get("option1") or "", row.get("option2") or "", row.get("option3") or "", row.get("option4") or ""]


def _question_correct_index(row: dict) -> int:
    answer = row.get("answer") or {}
    if isinstance(answer, dict) and isinstance(answer.get("correct"), list) and answer["correct"]:
        return int(answer["correct"][0])
    return int(row.get("correct_option") or 1)


def _add_preview(data: dict) -> str:
    options = data["options"]
    correct = data["correct"]
    letters = ["A", "B", "C", "D"]
    return (
        f"<b>Превью вопроса</b>\n"
        f"Тема ID: {data['topic_id']}\n"
        f"Тип: single\n\n"
        f"{data['prompt']}\n\n"
        f"A) {options[0]}\n"
        f"B) {options[1]}\n"
        f"C) {options[2]}\n"
        f"D) {options[3]}\n\n"
        f"Правильный вариант: {letters[correct - 1]}"
    )


async def _start_add_single_question(message: Message | CallbackQuery, state: FSMContext) -> None:
    rows = db.client.table("topics").select("id,title").order("id").limit(100).execute().data or []
    await state.clear()
    await state.set_state(AddQuestionTypeSingleFSM.topic)
    if isinstance(message, CallbackQuery):
        await message.message.answer("Выберите тему:", reply_markup=admin_topics_choose_kb(rows))
    else:
        await message.answer("Выберите тему:", reply_markup=admin_topics_choose_kb(rows))


@dp.callback_query(F.data == "admin:add_question")
async def admin_add_question(callback: CallbackQuery, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await _start_add_single_question(callback, state)
    await callback.answer()


@dp.callback_query(AddQuestionTypeSingleFSM.topic, F.data.startswith("admin:topic_pick:"))
async def add_pick_topic(callback: CallbackQuery, state: FSMContext) -> None:
    topic_id = int(callback.data.split(":")[-1])
    await state.update_data(topic_id=topic_id, type="single")
    await state.set_state(AddQuestionTypeSingleFSM.prompt)
    await callback.message.answer("Введите текст вопроса:")
    await callback.answer()


@dp.callback_query(AddQuestionTypeSingleFSM.topic, F.data == "admin:topic:new")
async def add_topic_new(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddQuestionTypeSingleFSM.new_topic)
    await callback.message.answer("Введите название новой темы:")
    await callback.answer()


@dp.message(AddQuestionTypeSingleFSM.new_topic)
async def add_topic_new_input(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название темы не может быть пустым")
        return
    created = db.client.table("topics").insert({"title": title, "is_active": True}).execute().data or []
    if not created:
        await message.answer("Не удалось создать тему")
        return
    await state.update_data(topic_id=int(created[0]["id"]), type="single")
    await state.set_state(AddQuestionTypeSingleFSM.prompt)
    await message.answer("Тема создана. Введите текст вопроса:")


@dp.message(AddQuestionTypeSingleFSM.prompt)
async def add_prompt(message: Message, state: FSMContext) -> None:
    prompt = (message.text or "").strip()
    if not prompt:
        await message.answer("Текст не должен быть пустым")
        return
    await state.update_data(prompt=prompt)
    await state.set_state(AddQuestionTypeSingleFSM.option_a)
    await message.answer("Введите вариант A:")


@dp.message(AddQuestionTypeSingleFSM.option_a)
async def add_a(message: Message, state: FSMContext) -> None:
    await state.update_data(option_a=(message.text or "").strip())
    await state.set_state(AddQuestionTypeSingleFSM.option_b)
    await message.answer("Введите вариант B:")


@dp.message(AddQuestionTypeSingleFSM.option_b)
async def add_b(message: Message, state: FSMContext) -> None:
    await state.update_data(option_b=(message.text or "").strip())
    await state.set_state(AddQuestionTypeSingleFSM.option_c)
    await message.answer("Введите вариант C:")


@dp.message(AddQuestionTypeSingleFSM.option_c)
async def add_c(message: Message, state: FSMContext) -> None:
    await state.update_data(option_c=(message.text or "").strip())
    await state.set_state(AddQuestionTypeSingleFSM.option_d)
    await message.answer("Введите вариант D:")


@dp.message(AddQuestionTypeSingleFSM.option_d)
async def add_d(message: Message, state: FSMContext) -> None:
    await state.update_data(option_d=(message.text or "").strip())
    await message.answer("Выберите правильный вариант:", reply_markup=admin_question_correct_kb())


@dp.callback_query(F.data.startswith("admin:correct:"))
async def add_correct(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AddQuestionTypeSingleFSM.option_d.state:
        await callback.answer("Не в сценарии добавления", show_alert=True)
        return
    letter = callback.data.split(":")[-1]
    idx = {"A": 1, "B": 2, "C": 3, "D": 4}[letter]
    data = await state.get_data()
    payload = {
        "topic_id": data["topic_id"],
        "type": "single",
        "prompt": data["prompt"],
        "options": [data["option_a"], data["option_b"], data["option_c"], data["option_d"]],
        "answer": {"correct": [idx]},
        "correct": idx,
    }
    await state.update_data(**payload)
    await callback.message.answer(_add_preview(payload), reply_markup=admin_question_preview_kb())
    await callback.answer()


@dp.callback_query(F.data == "admin:add:save")
async def add_save(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    if not data.get("prompt"):
        await callback.answer("Нет данных для сохранения", show_alert=True)
        return
    payload = {
        "topic_id": data["topic_id"],
        "type": "single",
        "prompt": data["prompt"],
        "options": data["options"],
        "answer": {"correct": [data["correct"]]},
        "is_active": True,
        "text": data["prompt"],
        "option1": data["options"][0],
        "option2": data["options"][1],
        "option3": data["options"][2],
        "option4": data["options"][3],
        "correct_option": data["correct"],
    }
    db.client.table("questions").insert(payload).execute()
    await state.clear()
    await callback.message.answer("Вопрос сохранён ✅")
    await callback.answer()


@dp.callback_query(F.data == "admin:add:edit")
async def add_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AddQuestionTypeSingleFSM.prompt)
    await callback.message.answer("Введите текст вопроса заново:")
    await callback.answer()


@dp.callback_query(F.data == "admin:add:cancel")
async def add_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.answer("Добавление отменено")
    await callback.answer()


async def _render_questions_page(message: Message | CallbackQuery, page: int, topic_id: int | None, active: str) -> None:
    query = db.client.table("questions").select("id,prompt,text,is_active,topic_id").order("id", desc=True)
    if topic_id:
        query = query.eq("topic_id", topic_id)
    if active == "active":
        query = query.eq("is_active", True)
    elif active == "inactive":
        query = query.eq("is_active", False)

    offset = (page - 1) * 10
    rows = query.range(offset, offset + 9).execute().data or []
    next_rows = query.range(offset + 10, offset + 10).execute().data or []
    has_next = bool(next_rows)

    if isinstance(message, CallbackQuery):
        send = message.message.answer
    else:
        send = message.answer

    if not rows:
        await send("Вопросы не найдены", reply_markup=admin_questions_nav_kb(page, False, topic_id, active))
        return

    for row in rows:
        text = _question_prompt(row).replace("\n", " ")
        short = f"{text[:80]}..." if len(text) > 80 else text
        status = "✅" if row.get("is_active") else "⛔"
        await send(f"#{row['id']} {status} {short}", reply_markup=admin_questions_item_kb(int(row["id"]), bool(row.get("is_active"))))
    await send(f"Страница {page}", reply_markup=admin_questions_nav_kb(page, has_next, topic_id, active))


@dp.callback_query(F.data == "admin:questions")
async def admin_questions(callback: CallbackQuery) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    await _render_questions_page(callback, page=1, topic_id=None, active="all")
    await callback.answer()


@dp.callback_query(F.data.startswith("admin:q_page:"))
async def admin_questions_page(callback: CallbackQuery) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, _, page_s, topic_s, active = callback.data.split(":")
    topic_id = int(topic_s)
    await _render_questions_page(callback, page=max(1, int(page_s)), topic_id=(topic_id or None), active=active)
    await callback.answer()


@dp.callback_query(F.data.startswith("admin:q_filter_topic:"))
async def admin_questions_filter_topic(callback: CallbackQuery) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, _, _, page_s, active = callback.data.split(":")
    topics = db.client.table("topics").select("id,title").order("title").limit(200).execute().data or []
    await callback.message.answer(
        "Фильтр по теме:",
        reply_markup=admin_question_topic_filter_kb(topics, page=int(page_s), active=active),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin:q_filter_active:"))
async def admin_questions_filter_active(callback: CallbackQuery) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    _, _, _, page_s, topic_s = callback.data.split(":")
    await callback.message.answer(
        "Фильтр по активности:",
        reply_markup=admin_question_active_filter_kb(page=int(page_s), topic_id=int(topic_s) or None),
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin:q_open:"))
async def admin_q_open(callback: CallbackQuery) -> None:
    qid = int(callback.data.split(":")[-1])
    row = db.client.table("questions").select("*").eq("id", qid).limit(1).execute().data or []
    if not row:
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    question = row[0]
    options = _question_options(question)
    correct = _question_correct_index(question)
    letters = ["A", "B", "C", "D"]
    await callback.message.answer(
        f"<b>#{qid}</b>\n"
        f"{_question_prompt(question)}\n\n"
        f"A) {options[0]}\nB) {options[1]}\nC) {options[2]}\nD) {options[3]}\n\n"
        f"Правильный: {letters[correct - 1]}"
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("admin:q_toggle:"))
async def admin_q_toggle(callback: CallbackQuery) -> None:
    qid = int(callback.data.split(":")[-1])
    rows = db.client.table("questions").select("is_active").eq("id", qid).limit(1).execute().data or []
    if not rows:
        await callback.answer("Вопрос не найден", show_alert=True)
        return
    active = bool(rows[0]["is_active"])
    db.client.table("questions").update({"is_active": not active}).eq("id", qid).execute()
    await callback.answer("Активность обновлена")


@dp.callback_query(F.data.startswith("admin:q_delete:"))
async def admin_q_delete(callback: CallbackQuery) -> None:
    qid = int(callback.data.split(":")[-1])
    db.client.table("questions").delete().eq("id", qid).execute()
    await callback.answer("Удалено")


@dp.callback_query(F.data == "admin:topics")
async def admin_topics(callback: CallbackQuery) -> None:
    if not admin_logic.has_admin_access(callback.from_user.id):
        await callback.answer("Недостаточно прав", show_alert=True)
        return
    rows = db.client.table("topics").select("id,title").order("id").limit(200).execute().data or []
    if not rows:
        await callback.message.answer("Тем пока нет", reply_markup=admin_topics_kb())
    else:
        for row in rows:
            await callback.message.answer(f"#{row['id']}: {row['title']}", reply_markup=admin_topics_manage_kb(int(row["id"])))
        await callback.message.answer("Управление темами:", reply_markup=admin_topics_kb())
    await callback.answer()


@dp.callback_query(F.data == "admin:topic:create")
async def admin_topic_create(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(TopicsFSM.create)
    await callback.message.answer("Введите название темы:")
    await callback.answer()


@dp.message(TopicsFSM.create)
async def admin_topic_create_input(message: Message, state: FSMContext) -> None:
    title = (message.text or "").strip()
    if not title:
        await message.answer("Название не может быть пустым")
        return
    db.client.table("topics").insert({"title": title, "is_active": True}).execute()
    await state.clear()
    await message.answer("Тема добавлена ✅")


@dp.callback_query(F.data.startswith("admin:topic_delete:"))
async def admin_topic_delete(callback: CallbackQuery) -> None:
    topic_id = int(callback.data.split(":")[-1])
    cnt = db.client.table("questions").select("id", count="exact").eq("topic_id", topic_id).limit(1).execute().count or 0
    if cnt > 0:
        await callback.answer("Нельзя удалить: есть связанные вопросы", show_alert=True)
        return
    db.client.table("topics").delete().eq("id", topic_id).execute()
    await callback.answer("Тема удалена")


@dp.message(Command("add_question"))
async def cmd_add_question(message: Message, state: FSMContext) -> None:
    if not admin_logic.has_admin_access(message.from_user.id):
        return
    await _start_add_single_question(message, state)




async def main() -> None:
    db.ensure_schema()
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
