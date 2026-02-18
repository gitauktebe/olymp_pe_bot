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
            [InlineKeyboardButton(text="➕ Добавить вопрос", callback_data="admin:add_question")],
            [InlineKeyboardButton(text="📚 Вопросы", callback_data="admin:questions")],
            [InlineKeyboardButton(text="🧩 Темы", callback_data="admin:topics")],
        ]
    )


def admin_question_correct_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="A", callback_data="admin:correct:A"), InlineKeyboardButton(text="B", callback_data="admin:correct:B")],
            [InlineKeyboardButton(text="C", callback_data="admin:correct:C"), InlineKeyboardButton(text="D", callback_data="admin:correct:D")],
        ]
    )


def admin_question_preview_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Сохранить", callback_data="admin:add:save")],
            [InlineKeyboardButton(text="✏️ Изменить", callback_data="admin:add:edit")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin:add:cancel")],
        ]
    )


def admin_topics_choose_kb(topics: list[dict]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=row["title"], callback_data=f"admin:topic_pick:{row['id']}")]
        for row in topics
    ]
    rows.append([InlineKeyboardButton(text="➕ Новая тема", callback_data="admin:topic:new")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="admin:add:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_questions_item_kb(question_id: int, is_active: bool) -> InlineKeyboardMarkup:
    activity_text = "⛔ Активность" if is_active else "✅ Активность"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="👁 Открыть", callback_data=f"admin:q_open:{question_id}")],
            [InlineKeyboardButton(text=activity_text, callback_data=f"admin:q_toggle:{question_id}")],
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:q_delete:{question_id}")],
        ]
    )


def admin_questions_nav_kb(page: int, has_next: bool, topic_id: int | None, active: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    nav_row: list[InlineKeyboardButton] = []
    if page > 1:
        nav_row.append(
            InlineKeyboardButton(text="⬅️", callback_data=f"admin:q_page:{page - 1}:{topic_id or 0}:{active}")
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(text="➡️", callback_data=f"admin:q_page:{page + 1}:{topic_id or 0}:{active}")
        )
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text="Фильтр темы", callback_data=f"admin:q_filter_topic:{page}:{active}")])
    rows.append([InlineKeyboardButton(text="Фильтр активности", callback_data=f"admin:q_filter_active:{page}:{topic_id or 0}")])
    rows.append([InlineKeyboardButton(text="↩️ В админ-меню", callback_data="admin:menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_question_topic_filter_kb(topics: list[dict], page: int, active: str) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="Все темы", callback_data=f"admin:q_page:{page}:0:{active}")]]
    rows.extend(
        [[InlineKeyboardButton(text=t["title"], callback_data=f"admin:q_page:{page}:{t['id']}:{active}")]] for t in topics
    )
    rows.append([InlineKeyboardButton(text="↩️ Назад", callback_data=f"admin:q_page:{page}:0:{active}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_question_active_filter_kb(page: int, topic_id: int | None) -> InlineKeyboardMarkup:
    tid = topic_id or 0
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Все", callback_data=f"admin:q_page:{page}:{tid}:all")],
            [InlineKeyboardButton(text="Только активные", callback_data=f"admin:q_page:{page}:{tid}:active")],
            [InlineKeyboardButton(text="Только неактивные", callback_data=f"admin:q_page:{page}:{tid}:inactive")],
        ]
    )


def admin_topics_manage_kb(topic_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🗑 Удалить", callback_data=f"admin:topic_delete:{topic_id}")]
        ]
    )


def admin_topics_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить тему", callback_data="admin:topic:create")],
            [InlineKeyboardButton(text="↩️ В админ-меню", callback_data="admin:menu")],
        ]
    )
