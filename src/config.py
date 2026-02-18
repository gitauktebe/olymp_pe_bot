from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str
    supabase_url: str
    supabase_anon_key: str
    supabase_service_role_key: str
    timezone: str = "Europe/Berlin"
    pack10_stars: int = 300
    unlimited30_stars: int = 1500
    test_mode: bool = False
    monetization_enabled: bool = False
    admin_tg_ids: tuple[int, ...] = ()



def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Environment variable {name} is required")
    return value


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_admin_tg_ids(value: str | None) -> tuple[int, ...]:
    if not value:
        return ()
    result: list[int] = []
    for item in value.split(","):
        token = item.strip()
        if not token:
            continue
        result.append(int(token))
    return tuple(result)


settings = Settings(
    telegram_bot_token=_required("TELEGRAM_BOT_TOKEN"),
    supabase_url=_required("SUPABASE_URL"),
    supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", "").strip(),
    supabase_service_role_key=_required("SUPABASE_SERVICE_ROLE_KEY"),
    timezone=os.getenv("TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin",
    pack10_stars=int(os.getenv("PACK10_STARS", "300")),
    unlimited30_stars=int(os.getenv("UNLIMITED30_STARS", "1500")),
    test_mode=_parse_bool(os.getenv("TEST_MODE")),
    monetization_enabled=_parse_bool(os.getenv("MONETIZATION_ENABLED")),
    admin_tg_ids=_parse_admin_tg_ids(os.getenv("ADMIN_TG_IDS")),
)
