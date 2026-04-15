from bee_operation_data import supabase_store


def week_key(week_start) -> str:
    iso = week_start.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def load_snapshots() -> list[dict]:
    return supabase_store.fetch_week_index()


def save_week(output: dict, key: str) -> str:
    return supabase_store.upsert_week_payload(key, output)


def save_snapshots(snaps: list[dict]) -> None:
    # O índice agora vem da view `v_week_index`; mantemos a função por compatibilidade.
    _ = snaps


def load_week(key: str) -> dict | None:
    return supabase_store.fetch_week_payload(key)


def save_latest(output: dict) -> None:
    key = (output or {}).get("week_key")
    if not key:
        raise ValueError("payload sem week_key")
    supabase_store.upsert_week_payload(key, output)


def load_latest() -> dict | None:
    return supabase_store.fetch_latest_payload()


def build_trend_weeks(snapshots: list[dict], week_label: str) -> list[dict]:
    rows = []
    for snap in snapshots[-5:]:
        label = (snap or {}).get("label") or (snap or {}).get("week_label") or ""
        rows.append({"week": label})
    rows.append({"week": week_label})
    return rows

