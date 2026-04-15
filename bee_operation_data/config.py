import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlparse, urlunparse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def load_repo_dotenv() -> None:
    if load_dotenv is None:
        return
    load_dotenv(repo_root() / ".env")


def plane_api_token() -> str:
    return (os.environ.get("PLANE_API_TOKEN") or os.environ.get("PLANE_API_KEY") or "").strip()


def plane_workspace_slug() -> str:
    return os.environ["PLANE_WORKSPACE_SLUG"]


def plane_project_ids() -> list[str]:
    return [v.strip() for v in os.environ["PLANE_PROJECT_IDS"].split(",") if v.strip()]


def anthropic_model() -> str:
    return (os.environ.get("ANTHROPIC_MODEL") or "").strip() or "claude-haiku-4-5"


def anthropic_max_tokens() -> int:
    raw = (os.environ.get("ANTHROPIC_MAX_TOKENS") or "").strip()
    try:
        return max(256, min(4096, int(raw))) if raw else 2048
    except ValueError:
        return 2048


def latest_json_path() -> Path:
    return repo_root() / (os.environ.get("LATEST_JSON_PATH") or "data/latest.json")


def snapshots_path() -> Path:
    return repo_root() / "data/snapshots.json"


def weeks_dir() -> Path:
    return repo_root() / "data/weeks"


def supabase_url() -> str:
    return (os.environ.get("SUPABASE_URL") or "").strip()


def supabase_service_role_key() -> str:
    return (os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "").strip()


def normalize_supabase_api_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return raw

    lower = raw.lower()
    if lower.startswith(("postgres://", "postgresql://")):
        raise RuntimeError(
            "SUPABASE_URL deve usar a Project URL HTTPS (https://<project-ref>.supabase.co), "
            "não a connection string do Postgres."
        )

    if "://" not in raw:
        raw = f"https://{raw.lstrip('/')}"

    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host.startswith("db.") and host.endswith(".supabase.co"):
        suffix = host[len("db.") :]
        port = f":{parsed.port}" if parsed.port else ""
        netloc = f"{suffix}{port}"
        raw = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))

    return raw.rstrip("/")


def require_supabase_credentials() -> tuple[str, str]:
    url = normalize_supabase_api_url(supabase_url())
    key = supabase_service_role_key()
    if not url or not key:
        raise RuntimeError("SUPABASE_URL e SUPABASE_SERVICE_ROLE_KEY são obrigatórias.")
    return url, key


@dataclass(frozen=True)
class OkrConfig:
    trimestre_inicio: date
    trimestre_fim: date
    ticket_base: float
    produto_pro: str
    produtos_enterprise: set[str]
    plane_workspace: str
    plane_project_id: str
    plane_module_id: str
    kr3_concluidas_fallback: int
    kr3_em_andamento_fallback: int


def load_okr_config() -> OkrConfig:
    prod_enterprise = {
        x.strip()
        for x in (
            os.environ.get("STRIPE_ENTERPRISE_PRODUCTS")
            or "prod_TkxRRgeB4JwctN,prod_TmPk46XlVeErwg,prod_TmPkrqLhIrqiL5"
        ).split(",")
        if x.strip()
    }
    return OkrConfig(
        trimestre_inicio=date.fromisoformat(os.environ.get("OKR_TRIMESTRE_INICIO", "2026-04-01")),
        trimestre_fim=date.fromisoformat(os.environ.get("OKR_TRIMESTRE_FIM", "2026-06-30")),
        ticket_base=float(os.environ.get("OKR_TICKET_BASE", "212.53")),
        produto_pro=os.environ.get("STRIPE_PRO_PRODUCT", "prod_TcDLqaVOaBQyhF"),
        produtos_enterprise=prod_enterprise,
        plane_workspace=(os.environ.get("PLANE_OKR_WORKSPACE") or os.environ.get("PLANE_WORKSPACE_SLUG") or "").strip(),
        plane_project_id=(os.environ.get("PLANE_OKR_PROJECT_ID") or "").strip(),
        plane_module_id=(os.environ.get("PLANE_OKR_MODULE_ID") or "").strip(),
        kr3_concluidas_fallback=int(os.environ.get("KR3_CONCLUIDAS_FALLBACK", "6")),
        kr3_em_andamento_fallback=int(os.environ.get("KR3_EM_ANDAMENTO_FALLBACK", "2")),
    )
