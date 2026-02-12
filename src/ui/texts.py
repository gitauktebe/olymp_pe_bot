WELCOME = "Привет! Это бот для подготовки к олимпиаде по PE."
BLOCKED = "Отдохни, продолжим завтра"
NO_QUESTIONS = "Пока нет подходящих вопросов."


def question_text(question: dict) -> str:
    return (
        f"<b>{question['text']}</b>\n\n"
        f"1) {question['option1']}\n"
        f"2) {question['option2']}\n"
        f"3) {question['option3']}\n"
        f"4) {question['option4']}"
    )
