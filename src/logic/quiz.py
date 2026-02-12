from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any

import pytz

from src.config import settings
from src.db import db

PACKAGE_SIZE = 10


@dataclass
class RuntimeSession:
    asked_ids: set[int] = field(default_factory=set)
    package_progress: int = 0
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


def ensure_day_row(tg_id: int) -> dict[str, Any]:
    day = _today_str()
    db.client.table("user_day").upsert(
        {
            "tg_id": tg_id,
            "day": day,
            "correct_count": 0,
            "wrong_count": 0,
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
        .order("unlimited_until", desc=True)
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

    settings_row = get_settings(tg_id)
    if int(settings_row.get("paid_packs_available", 0)) > 0:
        return True, None

    day_row = ensure_day_row(tg_id)
    if day_row.get("is_blocked"):
        return False, "Отдохни, продолжим завтра"
    return True, None


def consume_pack_if_needed(tg_id: int) -> bool:
    if has_unlimited_now(tg_id):
        return True

    day_row = ensure_day_row(tg_id)
    if day_row.get("is_blocked"):
        settings_row = get_settings(tg_id)
        available = int(settings_row.get("paid_packs_available", 0))
        if available <= 0:
            return False
        db.client.table("user_settings").update(
            {"paid_packs_available": available - 1, "updated_at": datetime.utcnow().isoformat()}
        ).eq("tg_id", tg_id).execute()
        db.client.table("user_day").update({"is_blocked": False}).eq("tg_id", tg_id).eq("day", _today_str()).execute()
    return True


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
    updates = {
        "correct_count": int(day_row.get("correct_count", 0)) + (1 if is_correct else 0),
        "wrong_count": int(day_row.get("wrong_count", 0)) + (0 if is_correct else 1),
    }
    if not is_correct and not has_unlimited_now(tg_id):
        updates["is_blocked"] = True
    db.client.table("user_day").update(updates).eq("tg_id", tg_id).eq("day", _today_str()).execute()

    stats = db.client.table("users").select("total_answers,total_correct,best_streak,current_streak").eq("tg_id", tg_id).single().execute().data
    current_streak = int(stats.get("current_streak", 0))
    best_streak = int(stats.get("best_streak", 0))
    if is_correct:
        current_streak += 1
        best_streak = max(best_streak, current_streak)
    else:
        current_streak = 0

    db.client.table("users").update(
        {
            "total_answers": int(stats.get("total_answers", 0)) + 1,
            "total_correct": int(stats.get("total_correct", 0)) + (1 if is_correct else 0),
            "current_streak": current_streak,
            "best_streak": best_streak,
        }
    ).eq("tg_id", tg_id).execute()

    session.answered_active = True
    session.package_progress += 1
    return True, "correct" if is_correct else "wrong"


def package_completed(tg_id: int) -> bool:
    return get_or_create_session(tg_id).package_progress >= PACKAGE_SIZE


def reset_package_progress(tg_id: int) -> None:
    get_or_create_session(tg_id).package_progress = 0


def get_question_by_id(question_id: int) -> dict[str, Any] | None:
    rows = db.client.table("questions").select("*").eq("id", question_id).limit(1).execute().data
    return rows[0] if rows else None
