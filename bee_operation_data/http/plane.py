import time

import requests
from requests.adapters import HTTPAdapter

from bee_operation_data import config

PLANE_BASE = "https://api.plane.so/api/v1"
_plane_session_obj = None


class ForceHTTP1Adapter(HTTPAdapter):
    def send(self, request, **kwargs):
        kwargs.setdefault("timeout", (10, 30))
        return super().send(request, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["socket_options"] = []
        super().init_poolmanager(*args, **kwargs)


def _plane_session():
    global _plane_session_obj
    if _plane_session_obj is None:
        session = requests.Session()
        adapter = ForceHTTP1Adapter()
        session.mount("https://api.plane.so", adapter)
        _plane_session_obj = session
    return _plane_session_obj


def headers_plane() -> dict[str, str]:
    token = config.plane_api_token()
    if not token:
        raise KeyError("PLANE_API_TOKEN/PLANE_API_KEY não configurado")
    return {
        "X-API-Key": token,
        "Content-Type": "application/json",
        "Connection": "close",
    }


def plane_get(path: str, params=None):
    return plane_get_in_workspace(config.plane_workspace_slug(), path, params)


def plane_get_in_workspace(workspace_slug: str, path: str, params=None):
    url = f"{PLANE_BASE}/workspaces/{workspace_slug}/{path}"
    for attempt in range(5):
        try:
            resp = _plane_session().get(
                url, headers=headers_plane(), params=params or {}, timeout=(10, 30)
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if attempt < 4:
                wait = min(10 * (2**attempt), 120)
                print(
                    f"   Plane timeout/conexão (tentativa {attempt+1}/5) — aguardando {wait}s..."
                )
                time.sleep(wait)
                continue
            raise TimeoutError(
                f"Plane API falhou após {attempt+1} tentativas: {exc}"
            ) from exc
        if resp.status_code == 429 and attempt < 4:
            raw = resp.headers.get("Retry-After", "45")
            try:
                wait = int(raw)
            except ValueError:
                wait = 45
            time.sleep(min(max(wait, 5), 120))
            continue
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return data
    raise RuntimeError("Plane API sem resposta válida")

