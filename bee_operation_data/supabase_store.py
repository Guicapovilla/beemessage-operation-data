from __future__ import annotations

from typing import Any

from supabase import create_client

from bee_operation_data import config

_CLIENT = None


def _client():
    global _CLIENT
    if _CLIENT is None:
        url, key = config.require_supabase_credentials()
        _CLIENT = create_client(url, key)
    return _CLIENT


def _response_data(response):
    if response is None:
        return None
    return getattr(response, "data", None)


def fetch_week_payload(week_key: str) -> dict[str, Any] | None:
    response = _client().table("operation_weeks").select("payload").eq("week_key", week_key).limit(1).execute()
    rows = _response_data(response) or []
    row = rows[0] if rows else None
    if not row:
        return None
    return row.get("payload")


def fetch_latest_payload() -> dict[str, Any] | None:
    response = (
        _client()
        .table("operation_weeks")
        .select("payload")
        .order("week_key", desc=True)
        .limit(1)
        .execute()
    )
    rows = _response_data(response) or []
    row = rows[0] if rows else None
    if not row:
        return None
    return row.get("payload")


def upsert_week_payload(week_key: str, payload: dict[str, Any]) -> str:
    (
        _client()
        .table("operation_weeks")
        .upsert({"week_key": week_key, "payload": payload}, on_conflict="week_key")
        .execute()
    )
    return week_key


def fetch_week_index() -> list[dict[str, Any]]:
    response = _client().table("v_week_index").select("key,label,range,rate,generated_at").order("key").execute()
    return list(_response_data(response) or [])


def fetch_improvements_payload() -> dict[str, Any]:
    response = _client().table("dashboard_improvements").select("payload").eq("id", 1).limit(1).maybe_single().execute()
    row = _response_data(response) or {}
    payload = row.get("payload") if isinstance(row, dict) else None
    return payload if isinstance(payload, dict) else {"improvements": []}


def upsert_improvements_payload(payload: dict[str, Any]) -> None:
    _client().table("dashboard_improvements").upsert({"id": 1, "payload": payload}, on_conflict="id").execute()
