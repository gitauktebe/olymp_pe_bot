from __future__ import annotations

from src.db import db
from src.logic.quiz import get_unlimited_until


def user_stats(tg_id: int) -> dict:
    user = db.client.table("users").select("total_answers,total_correct,best_streak,current_streak").eq("tg_id", tg_id).single().execute().data
    return {
        "total_answers": int(user.get("total_answers", 0)),
        "total_correct": int(user.get("total_correct", 0)),
        "best_streak": int(user.get("best_streak", 0)),
        "streak_today": int(user.get("current_streak", 0)),
        "unlimited_until": get_unlimited_until(tg_id),
    }


def top50() -> list[dict]:
    return (
        db.client.table("users")
        .select("tg_id,first_name,username,total_correct,best_streak")
        .order("total_correct", desc=True)
        .order("best_streak", desc=True)
        .limit(50)
        .execute()
        .data
    ) or []
