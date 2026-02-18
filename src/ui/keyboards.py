from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup


def start_kb(has_unlimited: bool = False) -> ReplyKeyboardMarkup:
    keyboard = [[KeyboardButton(text="Начать")], [KeyboardButton(text="Мои покупки")]]
    if has_unlimited:
        keyboard.append([KeyboardButton(text="Настройки безлимита")])
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True)


def answers_kb(question_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="A1", callback_data=f"ans:{question_id}:1")],
            [InlineKeyboardButton(text="A2", callback_data=f"ans:{question_id}:2")],
            [InlineKeyboardButton(text="A3", callback_data=f"ans:{question_id}:3")],
            [InlineKeyboardButton(text="A4", callback_data=f"ans:{question_id}:4")],
        ]
    )


def buy_kb() -> InlineKeyboardMarkup:
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


def next_pack_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Следующие 10", callback_data="next10")]])
