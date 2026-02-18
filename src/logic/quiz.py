from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import pytz

from src.config import settings
from src.db import db

DAILY_LIMIT = 10


@dataclass
class RuntimeSession:
    asked_ids: set[int] = field(default_factory=set)
    active_question_id: int | None = None
    answered_active: bool = False


runtime_sessions: dict[int, RuntimeSession] = {}


def _today_str() -> str:
    tz = pytz.timezone(settings.timezone)
    return datetime.now(tz).date().isoformat()


def next_midnight_iso() -> str:
    tz = pytz.timezone(settings.timezone)
    now = datetime.now(tz)
    tomorrow = now.date() + timedelta(days=1)
    midnight = tz.localize(datetime.combine(tomorrow, time.min))
    return midnight.isoformat()


def get_or_create_session(tg_id: int) -> RuntimeSession:
    if tg_id not in runtime_sessions:
        runtime_sessions[tg_id] = RuntimeSession()
    return runtime_sessions[tg_id]


def reset_session(tg_id: int) -> None:
    runtime_sessions[tg_id] = RuntimeSession()


def ensure_day_row(tg_id: int, day: str | None = None) -> dict[str, Any]:
    day = day or _today_str()
    db.client.table("user_day").upsert(
        {
            "tg_id": tg_id,
            "day": day,
            "correct_count": 0,
            "wrong_count": 0,
            "streak_today": 0,
            "is_blocked": False,
        },
        on_conflict="tg_id,day",
    ).execute()
    return db.client.table("user_day").select("*").eq("tg_id", tg_id).eq("day", day).single().execute().data


def get_unlimited_until(tg_id: int) -> datetime | None:
    row = (
        db.client.table("subscriptions")
        .select("unlimited_until")
        .eq("tg_id", tg_id)
        .limit(1)
        .execute()
        .data
    )
    if not row:
        return None
    value = row[0].get("unlimited_until")
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def has_unlimited_now(tg_id: int) -> bool:
    until = get_unlimited_until(tg_id)
    return bool(until and until > datetime.utcnow().astimezone(until.tzinfo))


def get_settings(tg_id: int) -> dict[str, Any]:
    return db.ensure_user_settings(tg_id)


def _query_questions(settings_row: dict[str, Any]) -> list[dict[str, Any]]:
    query = db.client.table("questions").select("*").eq("is_active", True)
    mode = settings_row.get("mode", "random")
    if mode == "topic" and settings_row.get("topic_id"):
        query = query.eq("topic_id", settings_row["topic_id"])
    elif mode == "difficulty" and settings_row.get("difficulty"):
        query = query.eq("difficulty", settings_row["difficulty"])
    return query.limit(2000).execute().data or []


def pick_question(tg_id: int) -> dict[str, Any] | None:
    session = get_or_create_session(tg_id)
    settings_row = get_settings(tg_id)
    questions = _query_questions(settings_row)
    if not questions:
        return None
    not_used = [q for q in questions if q["id"] not in session.asked_ids]
    pool = not_used if not_used else questions
    question = random.choice(pool)
    session.asked_ids.add(question["id"])
    session.active_question_id = question["id"]
    session.answered_active = False
    return question


def can_start_quiz_now(tg_id: int) -> tuple[bool, str | None]:
    if has_unlimited_now(tg_id):
        return True, None

    day_row = ensure_day_row(tg_id)
    if day_row.get("is_blocked"):
        return False, "На сегодня достаточно. Отдыхай до завтра 😴"
    if int(day_row.get("correct_count", 0)) >= DAILY_LIMIT:
        return False, "10/10 на сегодня выполнено. Возвращайся завтра ✅"
    return True, None


def save_answer(tg_id: int, question: dict[str, Any], answer_index: int) -> tuple[bool, str]:
    session = get_or_create_session(tg_id)
    if session.answered_active:
        return False, "already_answered"
    if session.active_question_id != question["id"]:
        return False, "stale_question"

    correct_option = int(question["correct_option"])
    is_correct = answer_index == correct_option

    db.client.table("answers").insert(
        {
            "tg_id": tg_id,
            "question_id": question["id"],
            "selected_option": answer_index,
            "is_correct": is_correct,
            "mode": get_settings(tg_id).get("mode", "random"),
        }
    ).execute()

    day_row = ensure_day_row(tg_id)
    unlimited = has_unlimited_now(tg_id)
    updates = {
        "correct_count": int(day_row.get("correct_count", 0)) + (1 if is_correct else 0),
        "wrong_count": int(day_row.get("wrong_count", 0)) + (0 if is_correct else 1),
    }
    if is_correct:
        updates["streak_today"] = int(day_row.get("streak_today", 0)) + 1
    elif not unlimited:
        updates["is_blocked"] = True
        updates["streak_today"] = 0
    db.client.table("user_day").update(updates).eq("tg_id", tg_id).eq("day", _today_str()).execute()

    stats = db.client.table("users").select("total_answers,total_correct,total_wrong,best_streak").eq("tg_id", tg_id).single().execute().data
    best_streak = int(stats.get("best_streak", 0))
    today_streak = int(updates.get("streak_today", day_row.get("streak_today", 0)))
    if today_streak > best_streak:
        best_streak = today_streak

    db.client.table("users").update(
        {
            "total_answers": int(stats.get("total_answers", 0)) + 1,
            "total_correct": int(stats.get("total_correct", 0)) + (1 if is_correct else 0),
            "total_wrong": int(stats.get("total_wrong", 0)) + (0 if is_correct else 1),
            "best_streak": best_streak,
        }
    ).eq("tg_id", tg_id).execute()

    session.answered_active = True
    if is_correct and (not unlimited) and updates["correct_count"] >= DAILY_LIMIT:
        return True, "daily_done"
    if (not is_correct) and (not unlimited):
        return True, "blocked"
    return True, "correct" if is_correct else "wrong"


def get_question_by_id(question_id: int) -> dict[str, Any] | None:
    rows = db.client.table("questions").select("*").eq("id", question_id).limit(1).execute().data
    return rows[0] if rows else None
