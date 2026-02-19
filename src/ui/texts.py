import logging

from src.logic.question_schema import normalize_question


logger = logging.getLogger(__name__)

WELCOME = "Привет! Это бот для подготовки к олимпиаде по PE. Нажми «Начать», чтобы получить вопрос."
BLOCKED = "На сегодня достаточно. Отдыхай до завтра 😴"
DAILY_DONE = "10/10 на сегодня выполнено. Возвращайся завтра ✅"
WRONG_STOP = "Есть ошибка — отдыхай до завтра 😴"
NO_QUESTIONS = "Пока нет подходящих вопросов."
QUESTION_FORMAT_ERROR = "Ошибка формата вопроса"


def question_text(question: dict) -> str:
    normalized = normalize_question(question)
    if normalized is None:
        logger.warning("Invalid question payload in question_text: keys=%s", sorted(question.keys()))
        return QUESTION_FORMAT_ERROR

    return (
        f"<b>{normalized['text']}</b>\n\n"
        f"1) {normalized['a1']}\n"
        f"2) {normalized['a2']}\n"
        f"3) {normalized['a3']}\n"
        f"4) {normalized['a4']}"
    )
