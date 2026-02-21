from __future__ import annotations

import re

QUESTION_START_RE = re.compile(r"^\s*(?:Q|В)\s*:\s*", flags=re.IGNORECASE)
OPTION_RE = re.compile(r"^([ABCD])\s*[\):]\s*(.+)$", flags=re.IGNORECASE)
FIELD_RE = re.compile(r"^([A-ZА-Я_]+)\s*:\s*(.*)$", flags=re.IGNORECASE)
SEPARATOR_RE = re.compile(r"^\s*---\s*$")


def split_bulk_blocks(raw_text: str) -> list[str]:
    lines = raw_text.splitlines()

    # Primary format: explicit separator line with ---
    if any(SEPARATOR_RE.match(line) for line in lines):
        blocks: list[str] = []
        current: list[str] = []
        for line in lines:
            if SEPARATOR_RE.match(line):
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

    # Fallback format: no separators, each new question begins with Q:/В:
    blocks = []
    current = []
    for line in lines:
        if QUESTION_START_RE.match(line) and current:
            blocks.append("\n".join(current).strip())
            current = [line]
            continue
        current.append(line)

    tail = "\n".join(current).strip()
    if tail:
        blocks.append(tail)

    return [block for block in blocks if block]


def parse_bool(value: str) -> bool | None:
    normalized = value.strip().lower()
    if normalized in {"true", "1", "yes", "y", "да"}:
        return True
    if normalized in {"false", "0", "no", "n", "нет"}:
        return False
    return None


def resolve_topic_id(topic_raw: str) -> int:
    topic_value = topic_raw.strip()
    if not topic_value:
        raise ValueError("TOPIC_ID: пустое значение")
    if not topic_value.isdigit():
        raise ValueError("TOPIC_ID должен быть числом")
    return int(topic_value)


def parse_bulk_block(block: str) -> dict:
    payload: dict = {"is_active": True}
    options: dict[str, str] = {}

    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        option_match = OPTION_RE.match(line)
        if option_match:
            letter = option_match.group(1).upper()
            options[letter] = option_match.group(2).strip()
            continue

        q_match = QUESTION_START_RE.match(line)
        if q_match:
            payload["q"] = re.sub(QUESTION_START_RE, "", line, count=1).strip()
            continue

        field_match = FIELD_RE.match(line)
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
                payload["topic_id"] = resolve_topic_id(value)
        elif key == "DIFF":
            if not value:
                continue
            if not value.isdigit() or not (1 <= int(value) <= 5):
                raise ValueError("DIFF должен быть числом 1..5")
            payload["difficulty"] = int(value)
        elif key == "ACTIVE":
            if not value:
                continue
            bool_value = parse_bool(value)
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
