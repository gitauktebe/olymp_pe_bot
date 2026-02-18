from __future__ import annotations

import asyncio
import logging
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
from src.ui.keyboards import answers_kb, buy_kb, next_question_kb, start_kb, unlimited_settings_kb
from src.ui.texts import BLOCKED, DAILY_DONE, NO_QUESTIONS, WELCOME, WRONG_STOP, question_text

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(
    token=settings.telegram_bot_token,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)
dp = Dispatcher()


class AddQuestionFSM(StatesGroup):
    topic_id = State()
    difficulty = State()
    text = State()
    option1 = State()
    option2 = State()
    option3 = State()
    option4 = State()
    correct_option = State()


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


@dp.message(Command("rating"))
async def cmd_rating(message: Message) -> None:
    rows = rating.top50()
    if not rows:
        await message.answer("Рейтинг пока пуст")
        return
    lines = ["<b>TOP-50</b>"]
    for i, row in enumerate(rows, start=1):
        name = row.get("username") or row.get("first_name") or str(row["tg_id"])
        lines.append(f"{i}. {name}: ✅ {int(row.get('total_correct', 0))} | 🔥 {int(row.get('best_streak', 0))}")
    await message.answer("\n".join(lines))


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
    await state.set_state(AddQuestionFSM.topic_id)
    await message.answer("ID темы?")


@dp.message(AddQuestionFSM.topic_id)
async def aq_topic(message: Message, state: FSMContext) -> None:
    await state.update_data(topic_id=int(message.text))
    await state.set_state(AddQuestionFSM.difficulty)
    await message.answer("Сложность 1..5?")


@dp.message(AddQuestionFSM.difficulty)
async def aq_diff(message: Message, state: FSMContext) -> None:
    await state.update_data(difficulty=int(message.text))
    await state.set_state(AddQuestionFSM.text)
    await message.answer("Текст вопроса?")


@dp.message(AddQuestionFSM.text)
async def aq_text(message: Message, state: FSMContext) -> None:
    await state.update_data(text=message.text)
    await state.set_state(AddQuestionFSM.option1)
    await message.answer("Вариант 1?")


@dp.message(AddQuestionFSM.option1)
async def aq_o1(message: Message, state: FSMContext) -> None:
    await state.update_data(option1=message.text)
    await state.set_state(AddQuestionFSM.option2)
    await message.answer("Вариант 2?")


@dp.message(AddQuestionFSM.option2)
async def aq_o2(message: Message, state: FSMContext) -> None:
    await state.update_data(option2=message.text)
    await state.set_state(AddQuestionFSM.option3)
    await message.answer("Вариант 3?")


@dp.message(AddQuestionFSM.option3)
async def aq_o3(message: Message, state: FSMContext) -> None:
    await state.update_data(option3=message.text)
    await state.set_state(AddQuestionFSM.option4)
    await message.answer("Вариант 4?")


@dp.message(AddQuestionFSM.option4)
async def aq_o4(message: Message, state: FSMContext) -> None:
    await state.update_data(option4=message.text)
    await state.set_state(AddQuestionFSM.correct_option)
    await message.answer("Правильный вариант (1..4)?")


@dp.message(AddQuestionFSM.correct_option)
async def aq_done(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    correct_option = int(message.text)
    db.client.table("questions").insert(
        {
            "topic_id": data["topic_id"],
            "difficulty": data["difficulty"],
            "text": data["text"],
            "option1": data["option1"],
            "option2": data["option2"],
            "option3": data["option3"],
            "option4": data["option4"],
            "correct_option": correct_option,
            "is_active": True,
        }
    ).execute()
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
