WELCOME = "Привет! Это бот для подготовки к олимпиаде по PE. Нажми «Начать», чтобы получить вопрос."
BLOCKED = "На сегодня достаточно. Отдыхай до завтра 😴"
DAILY_DONE = "10/10 на сегодня выполнено. Возвращайся завтра ✅"
WRONG_STOP = "Есть ошибка — отдыхай до завтра 😴"
NO_QUESTIONS = "Пока нет подходящих вопросов."


def question_text(question: dict) -> str:
    prompt = question.get("prompt") or question.get("text") or ""
    options = question.get("options")
    if not isinstance(options, list) or len(options) != 4:
        options = [question.get("option1"), question.get("option2"), question.get("option3"), question.get("option4")]
    return (
        f"<b>{prompt}</b>\n\n"
        f"1) {options[0]}\n"
        f"2) {options[1]}\n"
        f"3) {options[2]}\n"
        f"4) {options[3]}"
    )
