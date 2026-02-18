from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from src.db import db
from src.logic import payments


_VALID_PAYLOADS = {payments.PACK10_PAYLOAD, payments.UNLIMITED30_PAYLOAD}


def _current_unlimited_until(tg_id: int) -> datetime | None:
    row = (
        db.client.table("subscriptions")
        .select("unlimited_until")
        .eq("tg_id", tg_id)
        .limit(1)
        .execute()
        .data
    )
    if not row or not row[0].get("unlimited_until"):
        return None
    return datetime.fromisoformat(row[0]["unlimited_until"].replace("Z", "+00:00"))


def grant_purchase(
    tg_id: int,
    payload: str,
    amount: int,
    currency: str,
    charge_id: str,
    is_test: bool,
) -> dict[str, Any]:
    if payload not in _VALID_PAYLOADS:
        raise ValueError(f"Unsupported payload: {payload}")

    is_new = payments.insert_payment_if_new(
        tg_id=tg_id,
        currency=currency,
        total_amount=amount,
        invoice_payload=payload,
        telegram_payment_charge_id=charge_id,
    )
    if not is_new:
        return {"ok": True, "duplicate": True}

    if payload == payments.PACK10_PAYLOAD:
        settings_row = db.ensure_user_settings(tg_id)
        available = int(settings_row.get("paid_packs_available", 0)) + 1
        db.client.table("user_settings").update(
            {"paid_packs_available": available, "updated_at": datetime.utcnow().isoformat()}
        ).eq("tg_id", tg_id).execute()
        return {
            "ok": True,
            "type": payments.PACK10,
            "packs": available,
            "duplicate": False,
            "is_test": is_test,
        }

    now = datetime.now(timezone.utc)
    current_until = _current_unlimited_until(tg_id)
    start = current_until if current_until and current_until > now else now
    new_until = start + timedelta(days=30)

    db.client.table("subscriptions").upsert(
        {
            "tg_id": tg_id,
            "unlimited_until": new_until.isoformat(),
        },
        on_conflict="tg_id",
    ).execute()

    return {
        "ok": True,
        "type": payments.UNLIMITED30,
        "new_until": new_until.isoformat(),
        "duplicate": False,
        "is_test": is_test,
    }
