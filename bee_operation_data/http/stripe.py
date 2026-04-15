import time
from urllib.parse import urlencode

import requests


def stripe_get(endpoint: str, api_key: str, params=None):
    url = f"https://api.stripe.com/v1/{endpoint}"
    if params:
        url = f"{url}?{urlencode(params)}"
    for attempt in range(4):
        try:
            resp = requests.get(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=(10, 30),
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException:
            if attempt >= 3:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("Stripe API sem resposta válida")


def stripe_all(endpoint: str, api_key: str, params=None) -> list[dict]:
    query = dict(params or {})
    query["limit"] = 100
    items: list[dict] = []
    while True:
        data = stripe_get(endpoint, api_key, query)
        items.extend(data.get("data", []))
        if not data.get("has_more"):
            break
        query["starting_after"] = items[-1]["id"]
    return items
