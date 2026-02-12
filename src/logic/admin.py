from __future__ import annotations

from src.db import db

ROLE_ORDER = {"editor": 1, "admin": 2, "owner": 3}


def get_admin_role(tg_id: int) -> str | None:
    rows = db.client.table("admins").select("role").eq("tg_id", tg_id).limit(1).execute().data
    return rows[0]["role"] if rows else None


def has_admin_access(tg_id: int) -> bool:
    return get_admin_role(tg_id) is not None


def can_grant(granter_id: int, role: str) -> bool:
    granter_role = get_admin_role(granter_id)
    if not granter_role:
        return False
    if granter_role == "owner":
        return role in {"admin", "editor"}
    if granter_role == "admin":
        return role == "editor"
    return False


def grant_admin(granter_id: int, target_tg_id: int, role: str) -> bool:
    if role not in ROLE_ORDER:
        return False
    if not can_grant(granter_id, role):
        return False
    db.client.table("admins").upsert({"tg_id": target_tg_id, "role": role}, on_conflict="tg_id").execute()
    return True


def revoke_admin(granter_id: int, target_tg_id: int) -> bool:
    granter_role = get_admin_role(granter_id)
    target_role = get_admin_role(target_tg_id)
    if not granter_role or not target_role:
        return False
    if ROLE_ORDER[granter_role] <= ROLE_ORDER[target_role]:
        return False
    db.client.table("admins").delete().eq("tg_id", target_tg_id).execute()
    return True


def admin_stats() -> dict:
    total_users = db.client.table("users").select("tg_id", count="exact").limit(1).execute().count or 0
    total_answers = db.client.table("answers").select("id", count="exact").limit(1).execute().count or 0
    active_subs = (
        db.client.table("subscriptions")
        .select("tg_id", count="exact")
        .gt("unlimited_until", "now()")
        .limit(1)
        .execute()
        .count
        or 0
    )
    return {
        "total_users": total_users,
        "total_answers": total_answers,
        "active_unlimited": active_subs,
    }
