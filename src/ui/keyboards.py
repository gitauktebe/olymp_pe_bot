from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def start_kb(has_unlimited: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton(text="Начать")],
        [KeyboardButton(text="Меню")],
        [KeyboardButton(text="Моя статистика")],
        [KeyboardButton(text="Рейтинг")],
        [KeyboardButton(text="Мои покупки")],
    ]
    if has_unlimited:
        keyboard.append([KeyboardButton(text="Настройки безлимита")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def answers_kb(question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Ответить: 1", callback_data=f"ans:{question_id}:1")],
            [InlineKeyboardButton(text="Ответить: 2", callback_data=f"ans:{question_id}:2")],
            [InlineKeyboardButton(text="Ответить: 3", callback_data=f"ans:{question_id}:3")],
            [InlineKeyboardButton(text="Ответить: 4", callback_data=f"ans:{question_id}:4")],
            [InlineKeyboardButton(text="Меню", callback_data="menu")],
        ]
    )


def next_question_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Следующий", callback_data="next")],
            [InlineKeyboardButton(text="Меню", callback_data="menu")],
        ]
    )


def buy_kb(monetization_enabled: bool = True) -> InlineKeyboardMarkup | None:
    if not monetization_enabled:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Купить +10", callback_data="buy:pack10")],
            [InlineKeyboardButton(text="Купить безлимит 30 дней", callback_data="buy:unlimited30")],
        ]
    )


def unlimited_settings_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="random", callback_data="setmode:random")],
            [InlineKeyboardButton(text="topic", callback_data="setmode:topic")],
            [InlineKeyboardButton(text="difficulty", callback_data="setmode:difficulty")],
        ]
    )


def rating_type_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Всего верных", callback_data="rating:total_correct")],
            [InlineKeyboardButton(text="Лучшая серия", callback_data="rating:best_streak")],
        ]
    )


def admin_menu_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Добавить вопрос", callback_data="admin:add_question")],
            [InlineKeyboardButton(text="Импорт вопросов (пачкой)", callback_data="admin:bulk_import")],
            [InlineKeyboardButton(text="Импорт вопросов (файлом)", callback_data="admin:file_import")],
            [InlineKeyboardButton(text="Список последних 10 вопросов", callback_data="admin:list_questions")],
            [InlineKeyboardButton(text="Включить/выключить вопрос", callback_data="admin:toggle_question")],
            [InlineKeyboardButton(text="Выдать админку", callback_data="admin:grant_admin")],
        ]
    )
