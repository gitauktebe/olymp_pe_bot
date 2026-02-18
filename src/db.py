from __future__ import annotations

import logging
from typing import Any

import requests
from supabase import Client, create_client

from src.config import settings

logger = logging.getLogger(__name__)


class Database:
    def __init__(self) -> None:
        self.client: Client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    def _run_sql_via_pg_endpoint(self, sql: str) -> bool:
        """
        Best-effort migration execution using Supabase SQL endpoint.
        Endpoint availability depends on project settings/service role capabilities.
        """
        url = f"{settings.supabase_url}/pg/v1/query"
        headers = {
            "apikey": settings.supabase_service_role_key,
            "Authorization": f"Bearer {settings.supabase_service_role_key}",
            "Content-Type": "application/json",
        }
        response = requests.post(url, json={"query": sql}, headers=headers, timeout=20)
        if response.status_code >= 400:
            logger.warning("SQL migration endpoint not available (%s): %s", response.status_code, response.text)
            return False
        return True

    def ensure_schema(self) -> None:
        migration_sql = """
        create table if not exists public.user_settings (
          tg_id bigint primary key references public.users(tg_id) on delete cascade,
          mode text not null default 'random' check (mode in ('random','topic','difficulty')),
          topic_id bigint null references public.topics(id),
          difficulty smallint null check (difficulty between 1 and 5),
          paid_packs_available integer not null default 0,
          updated_at timestamptz not null default now()
        );
        """.strip()

        if self._run_sql_via_pg_endpoint(migration_sql):
            logger.info("Schema ensured via /pg/v1/query")
            return

        try:
            self.client.table("user_settings").select("tg_id").limit(1).execute()
            logger.info("user_settings already exists")
        except Exception:
            logger.error("Unable to auto-create user_settings. Run scripts manually with SQL:\n%s", migration_sql)

    def upsert_user(self, tg_id: int, first_name: str | None, username: str | None) -> None:
        payload = {
            "tg_id": tg_id,
            "first_name": first_name,
            "username": username,
        }
        self.client.table("users").upsert(payload, on_conflict="tg_id").execute()

    def ensure_user_settings(self, tg_id: int) -> dict[str, Any]:
        self.client.table("user_settings").upsert({"tg_id": tg_id}, on_conflict="tg_id").execute()
        data = self.client.table("user_settings").select("*").eq("tg_id", tg_id).single().execute().data
        return data


db = Database()
