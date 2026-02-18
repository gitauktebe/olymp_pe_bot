from __future__ import annotations

from datetime import datetime
from typing import Any

from postgrest.exceptions import APIError

from src.db import db


PACK10 = "pack10"
UNLIMITED30 = "unlimited30"
PACK10_PAYLOAD = "PACK10"
UNLIMITED30_PAYLOAD = "UNLIMITED30"
PAYLOAD_TO_KIND = {
    PACK10_PAYLOAD: PACK10,
    UNLIMITED30_PAYLOAD: UNLIMITED30,
}


def payload_for_kind(kind: str) -> str:
    if kind == PACK10:
        return PACK10_PAYLOAD
    if kind == UNLIMITED30:
        return UNLIMITED30_PAYLOAD
    raise ValueError(f"Unknown payment kind: {kind}")


def kind_from_payload(payload: str) -> str | None:
    return PAYLOAD_TO_KIND.get(payload)


def insert_payment_if_new(
    *,
    tg_id: int,
    currency: str,
    total_amount: int,
    invoice_payload: str,
    telegram_payment_charge_id: str,
) -> bool:
    try:
        db.client.table("payments").insert(
            {
                "tg_id": tg_id,
                "provider": "telegram_stars",
                "currency": currency,
                "total_amount": total_amount,
                "invoice_payload": invoice_payload,
                "telegram_payment_charge_id": telegram_payment_charge_id,
            }
        ).execute()
        return True
    except APIError as exc:
        details = f"{exc.message} {exc.details}"
        if "payments_telegram_payment_charge_id_key" in details or "duplicate key" in details:
            return False
        raise


def get_user_purchases_summary(tg_id: int) -> dict[str, Any]:
    settings_row = db.ensure_user_settings(tg_id)
    packs_available = int(settings_row.get("paid_packs_available", 0))

    sub_row = (
        db.client.table("subscriptions")
        .select("unlimited_until")
        .eq("tg_id", tg_id)
        .limit(1)
        .execute()
        .data
    )
    unlimited_until = None
    if sub_row and sub_row[0].get("unlimited_until"):
        unlimited_until = datetime.fromisoformat(sub_row[0]["unlimited_until"].replace("Z", "+00:00"))

    recent = (
        db.client.table("payments")
        .select("created_at,invoice_payload,total_amount,currency")
        .eq("tg_id", tg_id)
        .order("created_at", desc=True)
        .limit(3)
        .execute()
        .data
        or []
    )

    return {
        "packs_available": packs_available,
        "unlimited_until": unlimited_until,
        "recent_payments": recent,
    }
