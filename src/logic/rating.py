from __future__ import annotations

from src.db import db
from src.logic.quiz import ensure_day_row, get_unlimited_until


def user_stats(tg_id: int) -> dict:
    user = db.client.table("users").select("total_answers,total_correct,total_wrong,best_streak").eq("tg_id", tg_id).single().execute().data
    day = ensure_day_row(tg_id)
    return {
        "total_answers": int(user.get("total_answers", 0)),
        "total_correct": int(user.get("total_correct", 0)),
        "total_wrong": int(user.get("total_wrong", 0)),
        "best_streak": int(user.get("best_streak", 0)),
        "streak_today": int(day.get("streak_today", 0)),
        "correct_today": int(day.get("correct_count", 0)),
        "unlimited_until": get_unlimited_until(tg_id),
    }


def top10(metric: str) -> list[dict]:
    if metric not in {"total_correct", "best_streak"}:
        raise ValueError("Unsupported leaderboard metric")

    return (
        db.client.table("users")
        .select("tg_id,first_name,username,total_correct,best_streak")
        .order(metric, desc=True)
        .order("tg_id")
        .limit(10)
        .execute()
        .data
    ) or []


def user_rank(tg_id: int, metric: str) -> int:
    if metric not in {"total_correct", "best_streak"}:
        raise ValueError("Unsupported leaderboard metric")

    user = (
        db.client.table("users")
        .select(f"tg_id,{metric}")
        .eq("tg_id", tg_id)
        .single()
        .execute()
        .data
    )
    user_value = int((user or {}).get(metric, 0))

    better_count = (
        db.client.table("users")
        .select("tg_id", count="exact")
        .gt(metric, user_value)
        .execute()
        .count
        or 0
    )

    same_with_lower_id = (
        db.client.table("users")
        .select("tg_id", count="exact")
        .eq(metric, user_value)
        .lt("tg_id", tg_id)
        .execute()
        .count
        or 0
    )

    return int(better_count + same_with_lower_id + 1)
