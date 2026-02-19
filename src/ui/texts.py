import logging
from typing import Any, Mapping


logger = logging.getLogger(__name__)

WELCOME = "Привет! Это бот для подготовки к олимпиаде по PE. Нажми «Начать», чтобы получить вопрос."
BLOCKED = "На сегодня достаточно. Отдыхай до завтра 😴"
DAILY_DONE = "10/10 на сегодня выполнено. Возвращайся завтра ✅"
WRONG_STOP = "Есть ошибка — отдыхай до завтра 😴"
NO_QUESTIONS = "Пока нет подходящих вопросов."
QUESTION_FORMAT_ERROR = "Ошибка формата вопроса"


def normalize_question_fields(question: Mapping[str, Any]) -> dict[str, str] | None:
    text = question.get("q") or question.get("text")
    a1 = question.get("a1") or question.get("option1")
    a2 = question.get("a2") or question.get("option2")
    a3 = question.get("a3") or question.get("option3")
    a4 = question.get("a4") or question.get("option4")

    fields = {"text": text, "a1": a1, "a2": a2, "a3": a3, "a4": a4}
    missing = [name for name, value in fields.items() if value in (None, "")]
    if missing:
        logger.warning(
            "Invalid question payload: missing %s, available keys=%s",
            ", ".join(missing),
            sorted(question.keys()),
        )
        return None

    return {name: str(value) for name, value in fields.items()}


def question_text(question: dict) -> str:
    normalized = normalize_question_fields(question)
    if normalized is None:
        return QUESTION_FORMAT_ERROR

    return (
        f"<b>{normalized['text']}</b>\n\n"
        f"1) {normalized['a1']}\n"
        f"2) {normalized['a2']}\n"
        f"3) {normalized['a3']}\n"
        f"4) {normalized['a4']}"
    )
