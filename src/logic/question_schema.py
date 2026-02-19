from __future__ import annotations

import logging
from typing import Any, Mapping


logger = logging.getLogger(__name__)


def _first_non_empty(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_question(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    text = _first_non_empty(payload, "text", "q")
    a1 = _first_non_empty(payload, "option1", "a1")
    a2 = _first_non_empty(payload, "option2", "a2")
    a3 = _first_non_empty(payload, "option3", "a3")
    a4 = _first_non_empty(payload, "option4", "a4")
    correct_raw = _first_non_empty(payload, "correct_option", "correct")

    missing = [
        name
        for name, value in (("text", text), ("a1", a1), ("a2", a2), ("a3", a3), ("a4", a4), ("correct", correct_raw))
        if value in (None, "")
    ]
    if missing:
        logger.warning("Question payload missing fields=%s keys=%s", ",".join(missing), sorted(payload.keys()))
        return None

    try:
        correct = int(correct_raw)
    except (TypeError, ValueError):
        logger.warning("Question payload has invalid correct option=%r keys=%s", correct_raw, sorted(payload.keys()))
        return None

    if correct < 1 or correct > 4:
        logger.warning("Question payload has out-of-range correct option=%s keys=%s", correct, sorted(payload.keys()))
        return None

    normalized: dict[str, Any] = dict(payload)
    normalized.update(
        {
            "text": str(text),
            "q": str(text),
            "option1": str(a1),
            "option2": str(a2),
            "option3": str(a3),
            "option4": str(a4),
            "a1": str(a1),
            "a2": str(a2),
            "a3": str(a3),
            "a4": str(a4),
            "correct_option": correct,
            "correct": correct,
        }
    )
    return normalized

