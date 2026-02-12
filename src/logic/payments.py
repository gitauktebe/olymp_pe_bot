from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.db import db


PACK10 = "pack10"
UNLIMITED30 = "unlimited30"


def register_purchase(tg_id: int, kind: str, telegram_payment_charge_id: str, provider_payment_charge_id: str, amount: int) -> None:
    db.client.table("purchases").insert(
        {
            "tg_id": tg_id,
            "kind": kind,
            "telegram_payment_charge_id": telegram_payment_charge_id,
            "provider_payment_charge_id": provider_payment_charge_id,
            "amount": amount,
        }
    ).execute()


def grant_pack10(tg_id: int) -> None:
    settings_row = db.ensure_user_settings(tg_id)
    available = int(settings_row.get("paid_packs_available", 0))
    db.client.table("user_settings").update(
        {"paid_packs_available": available + 1, "updated_at": datetime.utcnow().isoformat()}
    ).eq("tg_id", tg_id).execute()


def grant_unlimited_30(tg_id: int) -> datetime:
    current = (
        db.client.table("subscriptions")
        .select("id,unlimited_until")
        .eq("tg_id", tg_id)
        .order("unlimited_until", desc=True)
        .limit(1)
        .execute()
        .data
    )

    now = datetime.now(timezone.utc)
    if current and current[0].get("unlimited_until"):
        existing = datetime.fromisoformat(current[0]["unlimited_until"].replace("Z", "+00:00"))
        start = existing if existing > now else now
    else:
        start = now

    new_until = start + timedelta(days=30)
    db.client.table("subscriptions").upsert(
        {
            "tg_id": tg_id,
            "unlimited_until": new_until.isoformat(),
            "updated_at": now.isoformat(),
        },
        on_conflict="tg_id",
    ).execute()
    return new_until
