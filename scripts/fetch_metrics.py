#!/usr/bin/env python3
"""
fetch_metrics.py
Puxa dados do Plane API, calcula métricas operacionais e gera insights via Claude API.
Salva o resultado em data/latest.json para o dashboard consumir.
"""

import os
import json
import math
import time
import unicodedata
import requests
from datetime import date, datetime, timedelta, timezone
from collections import defaultdict

try:
    from dotenv import load_dotenv
    _repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    load_dotenv(os.path.join(_repo_root, ".env"))
except ImportError:
    pass

# ── Configuração ──────────────────────────────────────────────────────────────
PLANE_BASE = "https://api.plane.so/api/v1"


def _headers_plane():
    return {"X-API-Key": os.environ["PLANE_API_TOKEN"], "Content-Type": "application/json", "Connection": "close"}


def _plane_slug():
    return os.environ["PLANE_WORKSPACE_SLUG"]


def _project_ids():
    return os.environ["PLANE_PROJECT_IDS"].split(",")


def _anthropic_headers():
    return {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def _anthropic_model():
    return (os.environ.get("ANTHROPIC_MODEL") or "").strip() or "claude-haiku-4-5"


def _anthropic_max_tokens():
    raw = (os.environ.get("ANTHROPIC_MAX_TOKENS") or "").strip()
    try:
        return max(256, min(4096, int(raw))) if raw else 768
    except ValueError:
        return 768

NOW            = datetime.now(timezone.utc)
WEEK_START     = (NOW - timedelta(days=NOW.weekday() + 1)).replace(hour=0, minute=0, second=0)  # segunda passada
PREV_WEEK_START= WEEK_START - timedelta(days=7)


# ── Helpers ───────────────────────────────────────────────────────────────────
def plane_get(path, params=None):
    url = f"{PLANE_BASE}/workspaces/{_plane_slug()}/{path}"
    for attempt in range(5):
        try:
            r = requests.get(url, headers=_headers_plane(), params=params or {}, timeout=(10, 30))
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            if attempt < 4:
                wait = min(10 * (2 ** attempt), 120)
                print(f"   ⏱️  Plane timeout/conexão (tentativa {attempt+1}/5) — aguardando {wait}s...")
                time.sleep(wait)
                continue
            else:
                raise TimeoutError(f"Plane API falhou após {attempt+1} tentativas: {e}")
        if r.status_code == 429 and attempt < 4:
            raw = r.headers.get("Retry-After", "45")
            try:
                wait = int(raw)
            except ValueError:
                wait = 45
            time.sleep(min(max(wait, 5), 120))
            continue
        r.raise_for_status()
        break
    data = r.json()
    if isinstance(data, dict) and "results" in data:
        return data["results"]
    return data

def parse_dt(s):
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None

def days_between(a, b):
    if not a or not b:
        return None
    return max(0, (b - a).total_seconds() / 86400)

def safe_round(v, d=1):
    if v is None:
        return None
    return round(v, d)


PRIORITY_MAP = {
    "urgent": "urgente", "high": "alta", "medium": "media",
    "low": "baixa", "none": "sem", None: "sem", "": "sem",
}
PRIORITY_KEYS = ["urgente", "alta", "media", "baixa", "sem"]


# ── Fetch all issues ──────────────────────────────────────────────────────────
def fetch_all_issues():
    issues = []
    for pid in _project_ids():
        page = 1
        while True:
            batch = plane_get(f"projects/{pid}/issues/", {"per_page": 100, "page": page, "expand": "state,assignees,labels"})
            if not batch:
                break
            issues.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return issues

def fetch_states():
    states = {}
    for pid in _project_ids():
        for s in plane_get(f"projects/{pid}/states/"):
            states[s["id"]] = s
    return states


# ── Classify issues ───────────────────────────────────────────────────────────
def classify(issues, states):
    done_states  = {sid for sid, s in states.items() if s.get("group") in ("done", "completed")}
    start_states = {sid for sid, s in states.items() if s.get("group") in ("started", "in_progress")}

    this_week, prev_week, backlog = [], [], []

    for iss in issues:
        created = parse_dt(iss.get("created_at"))
        completed = parse_dt(iss.get("completed_at"))
        due = parse_dt(iss.get("due_date"))
        state_id = iss.get("state")
        state = states.get(state_id, {})
        group = state.get("group", "")

        enriched = {
            **iss,
            "_created": created,
            "_completed": completed,
            "_due": due,
            "_state_group": group,
            "_is_done": state_id in done_states,
            "_is_active": state_id in start_states,
            "_cycle_time": days_between(created, completed) if completed else None,
            "_overdue": (
                due and not (state_id in done_states) and due < NOW
            ),
            "_overdue_days": (
                int((NOW - due).total_seconds() / 86400)
                if due and not (state_id in done_states) and due < NOW else 0
            ),
            "_assignee_names": [
                a.get("display_name", a.get("email", "?"))
                for a in (iss.get("assignees") or [])
            ],
            "_area": (iss.get("label_details") or [{}])[0].get("name", "Sem área")
                     if iss.get("label_details") else "Sem área",
        }

        if created and created >= WEEK_START:
            this_week.append(enriched)
        elif created and created >= PREV_WEEK_START:
            prev_week.append(enriched)
        backlog.append(enriched)

    return this_week, prev_week, backlog


# ── Compute metrics per week set ──────────────────────────────────────────────
def compute_metrics(issues, all_issues=None):
    done      = [i for i in issues if i["_is_done"]]
    overdue   = [i for i in issues if i["_overdue"]]
    active    = [i for i in issues if i["_is_active"]]

    total     = len(issues)
    n_done    = len(done)
    rate      = round(n_done / total * 100) if total else 0

    cycle_times = [i["_cycle_time"] for i in done if i["_cycle_time"] is not None]
    avg_ct    = safe_round(sum(cycle_times) / len(cycle_times)) if cycle_times else None

    # Chronic rollover: issues still open that were created before prev_week_start
    chronic   = [i for i in (all_issues or issues)
                 if not i["_is_done"] and i["_created"] and i["_created"] < PREV_WEEK_START]
    n_chronic = len(chronic)

    chronic_by_area = defaultdict(int)
    for i in (all_issues or issues):
        if not i["_is_done"] and i["_created"] and i["_created"] < PREV_WEEK_START:
            chronic_by_area[i["_area"]] += 1

    # Per-area metrics (including priority breakdown)
    def _area_default():
        return {
            "total": 0, "done": 0, "overdue": [], "cycle_times": [], "backlog": 0,
            "pri_done": {k: {"cts": [], "tasks": []} for k in PRIORITY_KEYS},
        }
    areas = defaultdict(_area_default)
    for i in issues:
        a = i["_area"]
        areas[a]["total"] += 1
        if i["_is_done"]:
            areas[a]["done"] += 1
            if i["_cycle_time"]:
                areas[a]["cycle_times"].append(i["_cycle_time"])
            pri = PRIORITY_MAP.get(i.get("priority") or "none", "sem")
            areas[a]["pri_done"][pri]["tasks"].append(i.get("name", "?")[:60])
            if i["_cycle_time"] is not None:
                areas[a]["pri_done"][pri]["cts"].append(i["_cycle_time"])
        if i["_overdue"]:
            areas[a]["overdue"].append(i)
        if not i["_is_done"]:
            areas[a]["backlog"] += 1

    areas_list = []
    for name, d in sorted(areas.items()):
        t = d["total"]
        dn = d["done"]
        r = round(dn / t * 100) if t else 0
        n_od = len(d["overdue"])
        n_roll = chronic_by_area.get(name, 0)
        # Score: A=no overdue + rate>=90; B=0-1 overdue + rate>=75; C=1-2 overdue or rate>=60; D=else
        if n_od == 0 and r >= 90:   score = "A"
        elif n_od <= 1 and r >= 75: score = "B"
        elif n_od <= 2 and r >= 60: score = "C"
        else:                        score = "D"
        pri_breakdown = {}
        for pk in PRIORITY_KEYS:
            pd = d["pri_done"][pk]
            cts = pd["cts"]
            pri_breakdown[pk] = {
                "count": len(pd["tasks"]),
                "avg_days": safe_round(sum(cts) / len(cts)) if cts else None,
                "min_days": safe_round(min(cts)) if cts else None,
                "max_days": safe_round(max(cts)) if cts else None,
                "tasks": pd["tasks"][-3:][::-1],
            }
        areas_list.append({
            "name": name, "score": score, "total": t, "done": dn,
            "rate": r, "overdue": n_od, "rollover": n_roll, "backlog": d["backlog"],
            "priority_breakdown": pri_breakdown,
        })

    # Per-assignee metrics
    collab = defaultdict(lambda: {"tasks":0,"done":0,"overdue":[],"areas":set()})
    for i in issues:
        for name in (i["_assignee_names"] or ["Sem dono"]):
            collab[name]["tasks"] += 1
            collab[name]["areas"].add(i["_area"])
            if i["_is_done"]:
                collab[name]["done"] += 1
            if i["_overdue"]:
                collab[name]["overdue"].append({
                    "title": i.get("name","?")[:60],
                    "days": i["_overdue_days"],
                    "area": i["_area"]
                })

    # Overload: flag anyone with > median+1.5*IQR tasks (simple: flag top 20% if >8 tasks)
    task_counts = sorted([v["tasks"] for v in collab.values()])
    median = task_counts[len(task_counts)//2] if task_counts else 0
    overload_threshold = max(8, median * 1.8)

    collabs_list = []
    for name, d in sorted(collab.items(), key=lambda x: -len(x[1]["overdue"])):
        n_od = len(d["overdue"])
        chronic_flag = any(
            od["days"] >= 14 for od in d["overdue"]
        )
        overloaded = d["tasks"] >= overload_threshold
        if n_od == 0 and not overloaded: severity = "ok"
        elif chronic_flag:               severity = "critical"
        elif n_od > 0 or overloaded:     severity = "warn"
        else:                             severity = "ok"

        collabs_list.append({
            "name": name,
            "tasks": d["tasks"],
            "done": d["done"],
            "rate": round(d["done"] / d["tasks"] * 100) if d["tasks"] else 0,
            "overdue": d["overdue"],
            "areas": list(d["areas"]),
            "overloaded": overloaded,
            "chronic": chronic_flag,
            "severity": severity,
        })

    overdue_list = sorted(
        [{"title": i.get("name","?")[:60], "area": i["_area"],
          "days": i["_overdue_days"], "assignees": i["_assignee_names"],
          "due": i["_due"].strftime("%d/%m") if i["_due"] else "—"}
         for i in overdue],
        key=lambda x: -x["days"]
    )

    return {
        "total": total, "done": n_done, "rate": rate,
        "avg_cycle_time": avg_ct, "overdue": len(overdue),
        "chronic_rollover": n_chronic, "areas": areas_list,
        "collaborators": collabs_list, "overdue_tasks": overdue_list,
    }


# ── Generate AI insights ──────────────────────────────────────────────────────
def generate_insights(current, previous):
    delta_rate = current["rate"] - previous["rate"]
    delta_ct   = (current["avg_cycle_time"] or 0) - (previous["avg_cycle_time"] or 0)
    delta_od   = current["overdue"] - previous["overdue"]
    delta_ch   = current["chronic_rollover"] - previous["chronic_rollover"]

    critical_collabs = [c for c in current["collaborators"] if c["severity"] == "critical"]
    overloaded       = [c for c in current["collaborators"] if c["overloaded"]]
    worst_areas      = [a for a in current["areas"] if a["score"] in ("C","D")]

    prompt = f"""Você é um assistente de operações que analisa métricas semanais de uma equipe.
Gere insights executivos em português para uma reunião semanal de sexta-feira.

DADOS DA SEMANA ATUAL:
- Taxa de conclusão: {current['rate']}% (semana anterior: {previous['rate']}%, delta: {delta_rate:+}pp)
- Cycle time médio: {current['avg_cycle_time']}d (anterior: {previous['avg_cycle_time']}d, delta: {delta_ct:+.1f}d)
- Tasks em atraso: {current['overdue']} (anterior: {previous['overdue']}, delta: {delta_od:+})
- Rollover crônico: {current['chronic_rollover']} (anterior: {previous['chronic_rollover']}, delta: {delta_ch:+})

ÁREAS COM SCORE C ou D: {[a['name'] + ' (score ' + a['score'] + ', taxa ' + str(a['rate']) + '%, ' + str(a['overdue']) + ' atrasos)' for a in worst_areas]}

COLABORADORES CRÍTICOS (rollover crônico): {[c['name'] + ' - ' + str(len(c['overdue'])) + ' atrasadas, maior aging: ' + str(max((o['days'] for o in c['overdue']), default=0)) + 'd' for c in critical_collabs]}

SOBRECARREGADOS (acima do limiar): {[c['name'] + ' - ' + str(c['tasks']) + ' tasks abertas' for c in overloaded]}

Gere um JSON com esta estrutura exata (sem markdown, só JSON puro):
{{
  "headline": "frase curta de 1 linha resumindo a semana (máx 15 palavras)",
  "status": "positivo" | "neutro" | "negativo",
  "summary": "parágrafo de 2-3 frases sobre o estado geral da semana",
  "highlights": ["insight positivo 1", "insight positivo 2"],
  "alerts": ["alerta 1 com causa provável e ação sugerida", "alerta 2"],
  "overload_analysis": "análise sobre distribuição de carga: se há pessoas sobrecarregadas, se a demanda é coerente com a capacidade do time, e recomendação",
  "projections": ["projeção ou risco para próxima semana baseada na tendência atual"],
  "action_items": ["ação concreta prioritária 1 para o COO", "ação 2"]
}}"""

    _m, _mt = _anthropic_model(), _anthropic_max_tokens()
    print(f"   → API Anthropic: modelo {_m}, max_tokens={_mt}")
    max_retries = 3
    for attempt in range(max_retries):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=_anthropic_headers(),
                json={"model": _m, "max_tokens": _mt,
                      "messages": [{"role": "user", "content": prompt}]},
                timeout=(10, 60)
            )
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            if attempt < max_retries - 1:
                wait = 10 * (2 ** attempt)
                print(f"   ⏱️  Claude API timeout (tentativa {attempt+1}/{max_retries}) — aguardando {wait}s...")
                time.sleep(wait)
            else:
                raise TimeoutError("Claude API não respondeu após 3 tentativas (60s cada)")
        except requests.exceptions.RequestException as e:
            if attempt < max_retries - 1:
                wait = 10 * (2 ** attempt)
                print(f"   ⚠️  Claude API erro (tentativa {attempt+1}/{max_retries}): {e} — aguardando {wait}s...")
                time.sleep(wait)
            else:
                raise
    
    text = resp.json()["content"][0]["text"].strip()
    # Strip markdown fences if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ── Load / save snapshot ──────────────────────────────────────────────────────
SNAPSHOT_PATH = "data/snapshots.json"

def load_snapshots():
    try:
        with open(SNAPSHOT_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return []

def save_snapshots(snaps):
    os.makedirs("data", exist_ok=True)
    with open(SNAPSHOT_PATH, "w") as f:
        json.dump(snaps[-12:], f, indent=2, default=str)  # keep last 12 weeks


def apply_week_flow_metrics(current, this_week, all_issues):
    """Criadas na semana (coorte) vs concluídas com completed_at na semana (qualquer idade)."""
    created_week = len(this_week)
    completed_week = sum(
        1
        for i in all_issues
        if i.get("_completed") and WEEK_START <= i["_completed"] <= NOW
    )
    current["created_week"] = created_week
    current["completed_week"] = completed_week
    current["balance_week"] = created_week - completed_week


# ── GitHub product progress ───────────────────────────────────────────────────

# Heurística padrão para mapear nomes de label para chaves de prioridade.
# Pode ser sobrescrita via PRIORITY_<CHAVE>_GITHUB_PRODUCT=csv de nomes exatos.
_GH_PRI_PATTERNS: dict[str, list[str]] = {
    "urgente": ["urgent", "urgente", "crítico", "critico", "critical", "p0"],
    "alta":    ["high", "alta", "p1"],
    "media":   ["medium", "média", "media", "normal", "p2"],
    "baixa":   ["low", "baixa", "minor", "p3"],
}


def github_priority_from_labels(label_names: list[str], product_label: str = "") -> str:
    """Retorna a chave de PRIORITY_KEYS mais adequada a partir dos nomes de labels da issue."""
    # Lê overrides opcionais via env: PRIORITY_URGENTE_GITHUB_PRODUCT, ...ALTA, etc.
    overrides: dict[str, set[str]] = {}
    for pk in PRIORITY_KEYS[:-1]:   # sem "sem"
        env_val = os.environ.get(f"PRIORITY_{pk.upper()}_GITHUB_PRODUCT", "")
        if env_val:
            overrides[pk] = {v.strip().lower() for v in env_val.split(",") if v.strip()}

    lowers = {n.lower() for n in label_names if n.lower() != product_label.lower()}

    for pk in ["urgente", "alta", "media", "baixa"]:
        candidates = overrides.get(pk) or {p for p in _GH_PRI_PATTERNS[pk]}
        if lowers & candidates:
            return pk
    return "sem"


def _empty_pp_extras() -> dict:
    """Campos de rollover/cycle/prioridade com valores nulos para retornos de erro."""
    empty_bd = {k: {"count": 0, "avg_days": None, "min_days": None, "max_days": None, "tasks": []}
                for k in PRIORITY_KEYS}
    return {
        "rollover": 0,
        "avg_cycle_time_sprint": None,
        "priority_breakdown": empty_bd,
        "sprint": {"current": None, "previous": None},
        "previous_sprint": None,
    }


def _gql_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _new_pp_bucket() -> dict:
    return {
        "planned": [],
        "in_progress": [],
        "completed": [],
        "rollover": 0,
        "pri_done": {k: {"cts": [], "tasks": []} for k in PRIORITY_KEYS},
    }


def _normalize_sprint_title(s: str) -> str:
    """Normaliza título de sprint para comparação: remove acentos compostos, espaços extras e padroniza caixa."""
    s = unicodedata.normalize("NFKC", s)
    return " ".join(s.split()).lower()


def _sprint_title_from_fv(sprint_fv: dict | None) -> str | None:
    """Extrai título da iteração ou nome do single select do campo Sprint no Project v2."""
    if not sprint_fv or not isinstance(sprint_fv, dict):
        return None
    t = (sprint_fv.get("title") or sprint_fv.get("name") or "").strip()
    return t or None


def _finalize_pp_slice(
    planned_items: list,
    in_progress_items: list,
    completed_items: list,
    rollover_count: int,
    pri_done: dict,
) -> dict:
    planned_count = len(planned_items)
    completed_count = len(completed_items)
    rate = round(completed_count / planned_count * 100) if planned_count else None
    all_cts = [i["cycle_days"] for i in completed_items if i.get("cycle_days") is not None]
    avg_ct = safe_round(sum(all_cts) / len(all_cts)) if all_cts else None
    priority_breakdown: dict[str, dict] = {}
    for pk in PRIORITY_KEYS:
        pd = pri_done[pk]
        cts = pd["cts"]
        priority_breakdown[pk] = {
            "count": len(pd["tasks"]),
            "avg_days": safe_round(sum(cts) / len(cts)) if cts else None,
            "min_days": safe_round(min(cts)) if cts else None,
            "max_days": safe_round(max(cts)) if cts else None,
            "tasks": pd["tasks"][:3],
        }
    return {
        "planned": {"count": planned_count, "items": planned_items},
        "in_progress": {"count": len(in_progress_items), "items": in_progress_items},
        "completed": {"count": completed_count, "items": completed_items},
        "completion_rate_pct": rate,
        "rollover": rollover_count,
        "avg_cycle_time_sprint": avg_ct,
        "priority_breakdown": priority_breakdown,
    }


def _route_item_to_bucket(
    bucket: dict,
    status_lower: str,
    item: dict,
    pri_key: str,
    created_dt,
    closed_dt,
    sprint_mode: bool,
    rollover_cutoff,
    status_planned: set,
    status_in_progress: set,
    status_done: set,
) -> bool:
    """Acrescenta item ao bucket conforme coluna Status (e regra de concluídas calendário vs sprint). Retorna se classificou."""
    pri_done = bucket["pri_done"]
    if status_lower in status_done:
        if sprint_mode:
            cd = safe_round(days_between(created_dt, closed_dt)) if created_dt and closed_dt else None
            item = {**item, "cycle_days": cd}
            bucket["completed"].append(item)
            pri_done[pri_key]["tasks"].append(item["title"])
            if cd is not None:
                pri_done[pri_key]["cts"].append(cd)
            return True
        if closed_dt and WEEK_START <= closed_dt <= NOW:
            cd = safe_round(days_between(created_dt, closed_dt)) if created_dt else None
            item = {**item, "cycle_days": cd}
            bucket["completed"].append(item)
            pri_done[pri_key]["tasks"].append(item["title"])
            if cd is not None:
                pri_done[pri_key]["cts"].append(cd)
            return True
        return False
    if status_lower in status_in_progress:
        bucket["in_progress"].append(item)
        if created_dt and rollover_cutoff and created_dt < rollover_cutoff:
            bucket["rollover"] += 1
        return True
    if status_lower in status_planned:
        bucket["planned"].append(item)
        if created_dt and rollover_cutoff and created_dt < rollover_cutoff:
            bucket["rollover"] += 1
        return True
    return False


def _github_resolve_sprint_titles(
    gh_headers: dict,
    owner_field: str,
    owner: str,
    project_number: str,
    sprint_field_name: str,
    env_current: str,
    env_previous: str,
    sprint_auto: bool,
) -> tuple[str, str]:
    """
    Preenche títulos de sprint (campo de iteração Project v2) quando env está incompleto e sprint_auto está ativo.
    Usa startDate + duration (UTC); sprint atual = iteração que contém hoje; anterior = maior término antes do início do atual.
    """
    ec = env_current.strip()
    ep = env_previous.strip()
    if not sprint_auto:
        return ec, ep
    if ec and ep:
        return ec, ep

    owner_g = _gql_escape(owner)
    try:
        pn = int(str(project_number).strip())
    except ValueError:
        return ec, ep

    query = f"""
    query {{
      {owner_field}(login: "{owner_g}") {{
        projectV2(number: {pn}) {{
          fields(first: 50) {{
            nodes {{
              __typename
              ... on ProjectV2IterationField {{
                name
                configuration {{
                  iterations {{ id title startDate duration }}
                  completedIterations {{ id title startDate duration }}
                }}
              }}
            }}
          }}
        }}
      }}
    }}"""
    try:
        resp = requests.post(
            "https://api.github.com/graphql",
            headers=gh_headers,
            json={"query": query},
            timeout=(10, 30),
        )
        resp.raise_for_status()
        gql = resp.json()
        if gql.get("errors"):
            raise RuntimeError(gql["errors"][0].get("message", str(gql["errors"])))
    except Exception as ex:
        print(f"   ⚠️  Auto-sprint (GraphQL): {ex}")
        return ec, ep

    nodes = (
        (((gql.get("data") or {}).get(owner_field) or {}).get("projectV2") or {}).get("fields") or {}
    ).get("nodes") or []

    iter_field = None
    for node in nodes:
        if node.get("__typename") != "ProjectV2IterationField":
            continue
        if node.get("name") == sprint_field_name:
            iter_field = node
            break
    if not iter_field:
        for node in nodes:
            if node.get("__typename") == "ProjectV2IterationField":
                if (node.get("name") or "").strip().lower() == sprint_field_name.lower():
                    iter_field = node
                    break

    if not iter_field:
        return ec, ep

    cfg = iter_field.get("configuration") or {}
    raw = list(cfg.get("iterations") or []) + list(cfg.get("completedIterations") or [])
    seen: set[str] = set()
    rows: list[tuple[date, date, str]] = []
    for it in raw:
        iid = str(it.get("id") or "")
        if iid and iid in seen:
            continue
        if iid:
            seen.add(iid)
        sd_s = ((it.get("startDate") or "")[:10]).strip()
        if not sd_s:
            continue
        try:
            sd = date.fromisoformat(sd_s)
        except ValueError:
            continue
        dur = int(it.get("duration") or 0)
        if dur < 1:
            dur = 1
        end = sd + timedelta(days=dur - 1)
        title = (it.get("title") or "").strip()
        if title:
            rows.append((sd, end, title))

    rows.sort(key=lambda r: r[0])
    if not rows:
        return ec, ep

    today = NOW.date()
    if not ec:
        for sd, end, title in rows:
            if sd <= today <= end:
                ec = title
                break

    if not ep and ec:
        cur_start: date | None = None
        for sd, _end, title in rows:
            if title == ec:
                cur_start = sd
                break
        if cur_start is not None:
            best_end: date | None = None
            best_title = ""
            for sd, end, title in rows:
                if end < cur_start and (best_end is None or end > best_end):
                    best_end = end
                    best_title = title
            ep = best_title

    return ec, ep


def build_product_progress():
    """
    Busca issues no GitHub e agrupa por status no board (Project v2 ou repo).

    Suporta dois modos configuráveis via env:
      Modo A (Project v2, recomendado): define PROJECT_NUMBER_GITHUB_PRODUCT — todas as issues do board
      Modo B (issues no repo + label):   define OWNER_GITHUB_PRODUCT + REPO_GITHUB_PRODUCT

    Variáveis de ambiente:
      TOKEN_GITHUB_PRODUCT         – PAT com acesso a issues (e projects se modo A)
      OWNER_GITHUB_PRODUCT         – login de usuário ou organização
      REPO_GITHUB_PRODUCT          – nome do repositório (modo B)
      PROJECT_NUMBER_GITHUB_PRODUCT– número do project v2 (modo A)
      LABEL_GITHUB_PRODUCT         – só Modo B: filtra issues na API REST (default Produto se ausente). Ignorado no Modo A.
      STATUS_PLANNED_GITHUB_PRODUCT    – nomes de status (separados por vírgula, case-insensitive)
                                        que representam "planejadas" (default: Backlog,Todo,Planejado,Planejada)
      STATUS_IN_PROGRESS_GITHUB_PRODUCT– default: In progress,Em progresso,In Progress
      STATUS_DONE_GITHUB_PRODUCT       – default: Done,Concluído,Concluída,Done,Feito
      PRIORITY_<CHAVE>_GITHUB_PRODUCT  – lista CSV de nomes de label que mapeiam para a prioridade
                                        (opcional; usa heurística embutida se ausente)
      SPRINT_FIELD_NAME_GITHUB_PRODUCT – nome do campo de iteração no Project (default: Sprint)
      SPRINT_CURRENT_GITHUB_PRODUCT    – título do sprint atual (ex.: S2 Abril); ativa modo sprint
      SPRINT_PREVIOUS_GITHUB_PRODUCT   – sprint anterior para comparativo (opcional)
      SPRINT_AUTO_GITHUB_PRODUCT       – 1 (default): preenche CURRENT/PREVIOUS vazios via campo Iteration no Project
    """
    _base_error = {"source": "github", "planned": {"count": 0, "items": []},
                   "in_progress": {"count": 0, "items": []},
                   "completed": {"count": 0, "items": []}, "completion_rate_pct": None}

    token = (os.environ.get("TOKEN_GITHUB_PRODUCT") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        print("   ⚠️  TOKEN_GITHUB_PRODUCT não configurado — seção Progresso Sprint desativada")
        return {
            **_base_error,
            "error": (
                "Sem token GitHub: no .env local use TOKEN_GITHUB_PRODUCT=… (ou GITHUB_TOKEN=…) na raiz do repo; "
                "no Actions crie o secret TOKEN_GITHUB_PRODUCT e rode o workflow."
            ),
            **_empty_pp_extras(),
        }

    owner = (os.environ.get("OWNER_GITHUB_PRODUCT") or "").strip()
    repo = (os.environ.get("REPO_GITHUB_PRODUCT") or "").strip()
    project_number = (os.environ.get("PROJECT_NUMBER_GITHUB_PRODUCT") or "").strip()
    mode_a = bool(project_number and owner)

    if mode_a:
        label = ""
    elif "LABEL_GITHUB_PRODUCT" in os.environ:
        label = os.environ.get("LABEL_GITHUB_PRODUCT", "").strip()
    else:
        label = "Produto"

    sprint_field = (os.environ.get("SPRINT_FIELD_NAME_GITHUB_PRODUCT") or "Sprint").strip() or "Sprint"
    sprint_current = (os.environ.get("SPRINT_CURRENT_GITHUB_PRODUCT") or "").strip()
    sprint_previous = (os.environ.get("SPRINT_PREVIOUS_GITHUB_PRODUCT") or "").strip()

    raw_auto = (os.environ.get("SPRINT_AUTO_GITHUB_PRODUCT") or "1").strip().lower()
    sprint_auto = raw_auto not in ("0", "false", "no", "off")

    gh_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    owner_field = ""
    if mode_a:
        owner_type = os.environ.get("OWNER_TYPE_GITHUB_PRODUCT", "").lower()
        if not owner_type:
            test = requests.get(f"https://api.github.com/orgs/{owner}", headers=gh_headers, timeout=10)
            owner_type = "organization" if test.status_code == 200 else "user"
        owner_field = "organization" if owner_type == "organization" else "user"
        sc0, sp0 = sprint_current, sprint_previous
        sprint_current, sprint_previous = _github_resolve_sprint_titles(
            gh_headers,
            owner_field,
            owner,
            project_number,
            sprint_field,
            sprint_current,
            sprint_previous,
            sprint_auto,
        )
        if sprint_auto and (sc0, sp0) != (sprint_current, sprint_previous):
            print(
                f"   GitHub sprint (auto): atual={sprint_current or '—'} · anterior={sprint_previous or '—'}"
            )

    sprint_field_q = _gql_escape(sprint_field)
    use_sprint_mode = bool(sprint_current)

    if use_sprint_mode and not mode_a:
        print("   ⚠️  SPRINT_CURRENT_GITHUB_PRODUCT exige Project v2 (OWNER_GITHUB_PRODUCT + PROJECT_NUMBER_GITHUB_PRODUCT)")
        return {
            **_base_error,
            "error": "SPRINT_CURRENT_GITHUB_PRODUCT requer Modo A: OWNER_GITHUB_PRODUCT e PROJECT_NUMBER_GITHUB_PRODUCT.",
            **_empty_pp_extras(),
        }

    # Defaults alinhados a boards PT-BR (BeeMessage); ainda compatíveis com nomes em inglês.
    def _status_csv(key: str, default: str) -> set:
        raw = os.environ.get(key)
        if raw is None or not str(raw).strip():
            raw = default
        return {s.strip().lower() for s in raw.split(",") if s.strip()}

    _def_planned = "backlog,a fazer,todo,planejado,planejada"
    _def_prog = "em andamento,aguardando aprovação,in progress,em progresso,in_progress"
    _def_done = "concluído,concluída,done,feito"
    status_planned     = _status_csv("STATUS_PLANNED_GITHUB_PRODUCT",     _def_planned)
    status_in_progress = _status_csv("STATUS_IN_PROGRESS_GITHUB_PRODUCT", _def_prog)
    status_done        = _status_csv("STATUS_DONE_GITHUB_PRODUCT",        _def_done)

    def _normalize_item(issue_or_node):
        """Normaliza para o formato base { title, assignees, cycle_days, due, priority }."""
        title     = (issue_or_node.get("title") or "?")[:100]
        assignees = issue_or_node.get("assignees") or []
        if isinstance(assignees, dict):
            assignees = [a.get("login", "") for a in assignees.get("nodes", [])]
        elif isinstance(assignees, list):
            assignees = [a.get("login", "") for a in assignees if isinstance(a, dict)]
        return {"title": title, "assignees": [a for a in assignees if a], "cycle_days": None, "due": None, "priority": "sem"}

    def _label_names_gql(content: dict) -> list[str]:
        return [l.get("name", "") for l in (content.get("labels") or {}).get("nodes", [])]

    def _label_names_rest(iss: dict) -> list[str]:
        return [l.get("name", "") for l in (iss.get("labels") or [])]

    bucket_cur = _new_pp_bucket()
    bucket_prev = _new_pp_bucket() if sprint_previous else None
    bucket_legacy = _new_pp_bucket()  # Modo A sem sprint ou Modo B

    rollover_cutoff = PREV_WEEK_START

    # Pré-normaliza os títulos de sprint para comparação robusta
    sprint_current_norm = _normalize_sprint_title(sprint_current) if sprint_current else ""
    sprint_previous_norm = _normalize_sprint_title(sprint_previous) if sprint_previous else ""

    try:
        # ── Modo A: GitHub Project v2 ────────────────────────────────────────
        if project_number and owner:
            mode_msg = f"sprint '{sprint_current}'" if use_sprint_mode else "semana calendário"
            print(f"   GitHub Project #{project_number} ({owner}) — {mode_msg}…")
            owner_q = _gql_escape(owner)
            pp_stats = {"skipped_sprint": 0, "unknown_status": 0, "empty_status": 0}
            cursor = None
            while True:
                after_arg = f', after: "{cursor}"' if cursor else ""
                query = f"""
                query {{
                  {owner_field}(login: "{owner_q}") {{
                    projectV2(number: {project_number}) {{
                      title
                      items(first: 100{after_arg}) {{
                        pageInfo {{ hasNextPage endCursor }}
                        nodes {{
                          type
                          statusVal: fieldValueByName(name: "Status") {{
                            ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
                          }}
                          sprintVal: fieldValueByName(name: "{sprint_field_q}") {{
                            ... on ProjectV2ItemFieldIterationValue {{ title }}
                            ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
                          }}
                          content {{
                            ... on Issue {{
                              title
                              state
                              createdAt
                              closedAt
                              dueOn: milestone {{ dueOn }}
                              assignees(first: 5) {{ nodes {{ login }} }}
                              labels(first: 10) {{ nodes {{ name }} }}
                            }}
                          }}
                        }}
                      }}
                    }}
                  }}
                }}"""
                resp = requests.post("https://api.github.com/graphql",
                                     headers=gh_headers, json={"query": query}, timeout=(10, 30))
                resp.raise_for_status()
                gql = resp.json()
                if gql.get("errors"):
                    raise RuntimeError(gql["errors"][0].get("message", str(gql["errors"])))
                project_data = (gql.get("data") or {}).get(owner_field, {}).get("projectV2") or {}
                items_page   = project_data.get("items") or {}
                nodes        = items_page.get("nodes") or []

                for node in nodes:
                    if node.get("type") != "ISSUE":
                        continue
                    content = node.get("content") or {}
                    if not content:
                        continue

                    if label:
                        issue_label_names = _label_names_gql(content)
                        if label.lower() not in [n.lower() for n in issue_label_names]:
                            continue
                    else:
                        issue_label_names = _label_names_gql(content)

                    status_raw   = ((node.get("statusVal") or {}).get("name") or "").strip()
                    status_lower = status_raw.lower()
                    created_dt   = parse_dt(content.get("createdAt"))
                    closed_dt    = parse_dt(content.get("closedAt"))
                    due_str      = (content.get("dueOn") or {}).get("dueOn") if content.get("dueOn") else None
                    pri_key      = github_priority_from_labels(issue_label_names, label)
                    item         = _normalize_item(content)
                    item["due"]  = due_str
                    item["priority"] = pri_key

                    sprint_title_raw = _sprint_title_from_fv(node.get("sprintVal"))
                    sprint_title_norm = _normalize_sprint_title(sprint_title_raw) if sprint_title_raw else ""

                    if use_sprint_mode:
                        if sprint_title_norm == sprint_current_norm:
                            placed = _route_item_to_bucket(
                                bucket_cur, status_lower, item, pri_key, created_dt, closed_dt,
                                True, rollover_cutoff, status_planned, status_in_progress, status_done,
                            )
                            if not placed:
                                if status_lower:
                                    pp_stats["unknown_status"] += 1
                                else:
                                    pp_stats["empty_status"] += 1
                        elif bucket_prev and sprint_title_norm == sprint_previous_norm:
                            placed = _route_item_to_bucket(
                                bucket_prev, status_lower, item, pri_key, created_dt, closed_dt,
                                True, rollover_cutoff, status_planned, status_in_progress, status_done,
                            )
                            if not placed:
                                if status_lower:
                                    pp_stats["unknown_status"] += 1
                                else:
                                    pp_stats["empty_status"] += 1
                        else:
                            pp_stats["skipped_sprint"] += 1
                    else:
                        placed = _route_item_to_bucket(
                            bucket_legacy, status_lower, item, pri_key, created_dt, closed_dt,
                            False, rollover_cutoff, status_planned, status_in_progress, status_done,
                        )
                        if not placed:
                            if status_lower:
                                pp_stats["unknown_status"] += 1
                            else:
                                pp_stats["empty_status"] += 1

                page_info = items_page.get("pageInfo") or {}
                if page_info.get("hasNextPage"):
                    cursor = page_info.get("endCursor")
                else:
                    break

            tot_diag = sum(pp_stats.values())
            if tot_diag:
                print(
                    f"   GitHub board (diagn.): fora_do_sprint={pp_stats['skipped_sprint']} | "
                    f"status_vazio={pp_stats['empty_status']} | "
                    f"status_desconhecido={pp_stats['unknown_status']}"
                )

        # ── Modo B: Issues REST (sem campo Sprint no project) ──────────────
        elif owner and repo:
            if use_sprint_mode:
                return {
                    **_base_error,
                    "error": "Filtro por sprint só funciona com GitHub Project v2 (PROJECT_NUMBER_GITHUB_PRODUCT).",
                    **_empty_pp_extras(),
                }
            lbl_txt = f"label={label}" if label else "todas as issues"
            print(f"   GitHub repo {owner}/{repo} ({lbl_txt})…")
            week_str = WEEK_START.strftime("%Y-%m-%dT%H:%M:%SZ")

            page = 1
            while True:
                params: dict = {"state": "open", "per_page": 100, "page": page}
                if label:
                    params["labels"] = label
                r = requests.get(
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    headers=gh_headers, timeout=(10, 30),
                    params=params,
                )
                r.raise_for_status()
                batch = r.json()
                if not batch:
                    break
                for iss in batch:
                    if iss.get("pull_request"):
                        continue
                    issue_label_names = _label_names_rest(iss)
                    pri_key = github_priority_from_labels(issue_label_names, label)
                    item = _normalize_item(iss)
                    item["due"] = (iss.get("milestone") or {}).get("due_on")
                    item["priority"] = pri_key
                    created_dt = parse_dt(iss.get("created_at"))
                    status_l = [n.lower() for n in issue_label_names]
                    if any(s in status_l for s in status_in_progress):
                        bucket_legacy["in_progress"].append(item)
                        if created_dt and created_dt < rollover_cutoff:
                            bucket_legacy["rollover"] += 1
                    else:
                        bucket_legacy["planned"].append(item)
                        if created_dt and created_dt < rollover_cutoff:
                            bucket_legacy["rollover"] += 1
                if len(batch) < 100:
                    break
                page += 1

            page = 1
            while True:
                params = {"state": "closed", "since": week_str, "per_page": 100, "page": page}
                if label:
                    params["labels"] = label
                r = requests.get(
                    f"https://api.github.com/repos/{owner}/{repo}/issues",
                    headers=gh_headers, timeout=(10, 30),
                    params=params,
                )
                r.raise_for_status()
                batch = r.json()
                if not batch:
                    break
                for iss in batch:
                    if iss.get("pull_request"):
                        continue
                    closed_dt  = parse_dt(iss.get("closed_at"))
                    created_dt = parse_dt(iss.get("created_at"))
                    if not (closed_dt and WEEK_START <= closed_dt <= NOW):
                        continue
                    issue_label_names = _label_names_rest(iss)
                    pri_key = github_priority_from_labels(issue_label_names, label)
                    item = _normalize_item(iss)
                    cd = safe_round(days_between(created_dt, closed_dt)) if created_dt else None
                    item["cycle_days"] = cd
                    item["priority"] = pri_key
                    bucket_legacy["completed"].append(item)
                    pd = bucket_legacy["pri_done"][pri_key]
                    pd["tasks"].append(item["title"])
                    if cd is not None:
                        pd["cts"].append(cd)
                if len(batch) < 100:
                    break
                page += 1
        else:
            print("   ⚠️  Configuração GitHub (progresso sprint) incompleta — defina OWNER + REPO ou PROJECT_NUMBER")
            return {
                **_base_error,
                "error": (
                    "Token OK, mas falta escopo do Project: defina OWNER_GITHUB_PRODUCT (org ou user) e "
                    "PROJECT_NUMBER_GITHUB_PRODUCT (Modo A, Project v2) — ou OWNER_GITHUB_PRODUCT + REPO_GITHUB_PRODUCT (Modo B). "
                    "Os secrets do GitHub Actions precisam ter os mesmos nomes e o workflow precisa rodar de novo."
                ),
                **_empty_pp_extras(),
            }

    except Exception as exc:
        msg = str(exc)
        print(f"   ⚠️  Erro ao buscar dados do GitHub (board/sprint): {msg}")
        if "Resource not accessible by personal access token" in msg:
            msg += (
                " — Conceda ao PAT leitura de Projects da organização (fine-grained: Organization → Projects → Read "
                "e autorize na org; SSO se exigido). Classic: escopo read:project."
            )
        return {**_base_error, "error": msg, **_empty_pp_extras()}

    if use_sprint_mode:
        cur_slice = _finalize_pp_slice(
            bucket_cur["planned"], bucket_cur["in_progress"], bucket_cur["completed"],
            bucket_cur["rollover"], bucket_cur["pri_done"],
        )
        prev_block = None
        if bucket_prev:
            prev_block = _finalize_pp_slice(
                bucket_prev["planned"], bucket_prev["in_progress"], bucket_prev["completed"],
                bucket_prev["rollover"], bucket_prev["pri_done"],
            )
        pc, pic, cc = cur_slice["planned"]["count"], cur_slice["in_progress"]["count"], cur_slice["completed"]["count"]
        print(f"   GitHub sprint atual: {pc} planejadas | {pic} em execução | {cc} concluídas | rollover {cur_slice['rollover']}")
        if prev_block:
            print(f"   GitHub sprint anterior: {prev_block['planned']['count']} p | {prev_block['in_progress']['count']} ex | {prev_block['completed']['count']} ok")
        out = {
            "source": "github",
            "sprint": {"current": sprint_current, "previous": sprint_previous or None},
            **cur_slice,
            "previous_sprint": prev_block,
        }
        return out

    leg = _finalize_pp_slice(
        bucket_legacy["planned"], bucket_legacy["in_progress"], bucket_legacy["completed"],
        bucket_legacy["rollover"], bucket_legacy["pri_done"],
    )
    print(
        f"   GitHub board: {leg['planned']['count']} planejadas | {leg['in_progress']['count']} em execução | "
        f"{leg['completed']['count']} concluídas | rollover {leg['rollover']}"
    )
    return {
        "source": "github",
        "sprint": {"current": None, "previous": None},
        "previous_sprint": None,
        **leg,
    }


def build_trend_weeks(snapshots, week_label):
    """Eixo temporal (rótulos de semana) para os mini-gráficos de OKR no dashboard."""
    rows = []
    for s in snapshots[-5:]:
        wl = (s or {}).get("week_label", "") or ""
        rows.append({"week": wl})
    rows.append({"week": week_label})
    return rows


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("🔄 Buscando issues do Plane...")
    issues = fetch_all_issues()
    states = fetch_states()
    print(f"   {len(issues)} issues carregadas")

    this_week, prev_week_issues, all_issues = classify(issues, states)

    print("📊 Calculando métricas...")
    current  = compute_metrics(this_week, all_issues)
    previous = compute_metrics(prev_week_issues, all_issues)
    apply_week_flow_metrics(current, this_week, all_issues)

    print("🤖 Gerando insights com Claude...")
    insights = generate_insights(current, previous)

    print("🐙 Buscando progresso do projeto no GitHub…")
    product_progress = build_product_progress()

    snapshots = load_snapshots()
    prev_snapshot = snapshots[-1] if snapshots else previous

    week_label = f"Semana {NOW.isocalendar()[1]} · {NOW.strftime('%B %Y').capitalize()}"

    output = {
        "generated_at": NOW.isoformat(),
        "week_label": week_label,
        "current": current,
        "previous": prev_snapshot.get("current", previous) if isinstance(prev_snapshot, dict) else previous,
        "insights": insights,
        "product_progress": product_progress,
        "trend": build_trend_weeks(snapshots, week_label),
    }

    # Save snapshot
    snapshots.append({"week_label": week_label, "current": current})
    save_snapshots(snapshots)

    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w") as f:
        json.dump(output, f, indent=2, default=str, ensure_ascii=False)

    print(f"✅ data/latest.json salvo — semana {week_label}")
    print(f"   Taxa: {current['rate']}% | C.Time: {current['avg_cycle_time']}d | Atrasos: {current['overdue']}")
    print(f"   Fluxo semana: {current.get('created_week', '?')} criadas, {current.get('completed_week', '?')} concluídas, saldo {current.get('balance_week', '?')}")


# ── Stripe integration ────────────────────────────────────────────────────────
def fetch_stripe_okr():
    """Fetch OKR data from Stripe API."""
    stripe_key = os.environ.get("STRIPE_SECRET_KEY")
    if not stripe_key:
        print("   ⚠️  STRIPE_SECRET_KEY não configurado — OKRs usando dados de exemplo")
        return None

    headers = {"Authorization": f"Bearer {stripe_key}"}

    try:
        # Fetch subscriptions to calculate MRR and plan distribution
        subs_resp = requests.get(
            "https://api.stripe.com/v1/subscriptions",
            headers=headers,
            params={"status": "active", "limit": 100, "expand[]": "data.plan.product"}
        )
        subs_resp.raise_for_status()
        subs = subs_resp.json().get("data", [])

        mrr_total = 0
        pro_count = 0
        enterprise_count = 0
        baseline_mrr = float(os.environ.get("BASELINE_MRR", "0"))  # Set this to your MRR at start of OKR period

        for sub in subs:
            amount = sub.get("plan", {}).get("amount", 0) / 100  # cents to BRL
            interval = sub.get("plan", {}).get("interval", "month")
            monthly = amount if interval == "month" else amount / 12
            mrr_total += monthly

            product_name = sub.get("plan", {}).get("product", {}).get("name", "").lower()
            if "enterprise" in product_name:
                enterprise_count += 1
            elif "pro" in product_name:
                pro_count += 1

        total_paid = pro_count + enterprise_count
        conversion_pct = round((enterprise_count / total_paid * 100) if total_paid > 0 else 0, 1)
        mrr_growth_pct = round(((mrr_total - baseline_mrr) / baseline_mrr * 100) if baseline_mrr > 0 else 0, 1)

        # Count API implementations from metadata/events (customize per your setup)
        api_count = int(os.environ.get("API_IMPLEMENTATIONS_COUNT", "6"))  # Manual fallback

        print(f"   Stripe: MRR R${mrr_total:.0f}, Pro→Ent conversão {conversion_pct}%, crescimento {mrr_growth_pct}%")

        return {
            "mrr_total": round(mrr_total),
            "mrr_growth_pct": mrr_growth_pct,
            "pro_count": pro_count,
            "enterprise_count": enterprise_count,
            "conversion_pct": conversion_pct,
            "api_implementations": api_count,
        }

    except Exception as e:
        print(f"   ⚠️  Erro ao buscar dados do Stripe: {e}")
        return None


def fetch_plane_okr():
    """Fetch OKR cycle progress from Plane."""
    cycle_id = os.environ.get("PLANE_OKR_CYCLE_ID")
    if not cycle_id:
        return None

    try:
        for pid in _project_ids():
            try:
                cycle = plane_get(f"projects/{pid}/cycles/{cycle_id}/")
                issues = plane_get(f"projects/{pid}/cycles/{cycle_id}/cycle-issues/")
                total = len(issues)
                done  = sum(1 for i in issues if i.get("sub_issues_count", 0) == 0)  # adjust per your setup
                print(f"   Plane OKR: ciclo encontrado, {done}/{total} issues concluídas")
                return {"total_issues": total, "done_issues": done, "pct": round(done/total*100) if total else 0}
            except Exception:
                continue
    except Exception as e:
        print(f"   ⚠️  Erro ao buscar OKR do Plane: {e}")
    return None


def build_okr_block(stripe_data, plane_okr):
    """Build OKR block for latest.json."""
    s = stripe_data or {}
    growth = s.get("mrr_growth_pct", 14)
    conv   = s.get("conversion_pct", 9)
    api    = s.get("api_implementations", 6)

    insight_parts = []
    if stripe_data:
        insight_parts.append(
            f"Faturamento da base cresceu {growth}% (meta: 30%). "
            f"Conversão Pro→Enterprise em {conv}% (meta: 24%). "
            f"Implementações API: {api}/10."
        )
        if growth >= 20: insight_parts.append("KR1 em ritmo positivo — acima de 66% da meta.")
        elif growth < 10: insight_parts.append("KR1 abaixo do ritmo esperado — investigar churn ou falta de upsell.")
        if conv >= 16: insight_parts.append("KR2 avançando bem. Priorizar clientes Pro com alto uso.")
        if api >= 7: insight_parts.append("KR3 praticamente atingido — formalizar documentação das implementações.")
    else:
        insight_parts.append("Conecte o Stripe (STRIPE_SECRET_KEY) e o Plane OKR (PLANE_OKR_CYCLE_ID) para habilitar análise automática de progresso e projeção de atingimento de metas com dados reais.")

    return {
        "insight": " ".join(insight_parts),
        "key_results": [
            {
                "label": "KR1 · Aumentar faturamento da base +30%",
                "icon": "📈", "unit": "%",
                "current": growth, "target": 30,
                "delta": None,
                "details": [
                    {"label": "MRR atual",           "value": f"R${s.get('mrr_total','—')}" if stripe_data else "— (aguardando Stripe)", "color": "#4a4f6a" if not stripe_data else "#60a5fa"},
                    {"label": "Crescimento acumulado","value": f"{growth}%",                  "color": "#f5a623" if growth < 20 else "#3ecf8e"},
                    {"label": "Meta trimestral",      "value": "+30%",                          "color": "#7b8099"},
                    {"label": "Ritmo necessário",     "value": "~5.3pp / semana",               "color": "#7b8099"},
                ]
            },
            {
                "label": "KR2 · Converter 24% dos clientes Pro → Enterprise",
                "icon": "🚀", "unit": "%",
                "current": conv, "target": 24,
                "delta": None,
                "details": [
                    {"label": "Clientes Pro ativos",          "value": str(s.get("pro_count", "—")) if stripe_data else "— (aguardando Stripe)", "color": "#4a4f6a" if not stripe_data else "#60a5fa"},
                    {"label": "Convertidos para Enterprise",  "value": f"{conv}%",               "color": "#f5a623" if conv < 16 else "#3ecf8e"},
                    {"label": "Meta trimestral",              "value": "24%",                    "color": "#7b8099"},
                    {"label": "Ritmo necessário",             "value": "~3.75pp / semana",       "color": "#7b8099"},
                ]
            },
            {
                "label": "KR3 · 10 implementações API realizadas",
                "icon": "⚙️", "unit": "",
                "current": api, "target": 10,
                "delta": None,
                "details": [
                    {"label": "Implementações concluídas", "value": str(api),        "color": "#60a5fa"},
                    {"label": "Em andamento",              "value": str(plane_okr.get("done_issues", "—") if plane_okr else "—"), "color": "#f5a623"},
                    {"label": "Meta trimestral",           "value": "10",             "color": "#7b8099"},
                    {"label": "Ritmo necessário",          "value": "~0.6 / semana", "color": "#7b8099"},
                ]
            },
        ]
    }


# Patch main() to include OKR data
_original_main = main

def main():
    print("🔄 Buscando issues do Plane...")
    issues = fetch_all_issues()
    states = fetch_states()
    print(f"   {len(issues)} issues carregadas")

    this_week, prev_week_issues, all_issues = classify(issues, states)

    print("📊 Calculando métricas...")
    current  = compute_metrics(this_week, all_issues)
    previous = compute_metrics(prev_week_issues, all_issues)
    apply_week_flow_metrics(current, this_week, all_issues)

    print("📦 Buscando dados do Stripe e OKR do Plane...")
    stripe_data = fetch_stripe_okr()
    plane_okr   = fetch_plane_okr()
    okr_block   = build_okr_block(stripe_data, plane_okr)

    print("🤖 Gerando insights com Claude...")
    insights = generate_insights(current, previous)

    print("🐙 Buscando progresso do projeto no GitHub…")
    product_progress = build_product_progress()

    snapshots = load_snapshots()
    prev_snapshot = snapshots[-1] if snapshots else previous
    week_label = f"Semana {NOW.isocalendar()[1]} · {NOW.strftime('%B %Y').capitalize()}"

    output = {
        "generated_at": NOW.isoformat(),
        "week_label": week_label,
        "current": current,
        "previous": prev_snapshot.get("current", previous) if isinstance(prev_snapshot, dict) else previous,
        "insights": insights,
        "okr": okr_block,
        "product_progress": product_progress,
        "trend": build_trend_weeks(snapshots, week_label),
    }

    snapshots.append({"week_label": week_label, "current": current})
    save_snapshots(snapshots)

    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w") as f:
        json.dump(output, f, indent=2, default=str, ensure_ascii=False)

    print(f"✅ data/latest.json salvo — semana {week_label}")
    print(f"   Taxa: {current['rate']}% | C.Time: {current['avg_cycle_time']}d | Atrasos: {current['overdue']}")
    print(f"   Fluxo semana: {current.get('created_week', '?')} criadas, {current.get('completed_week', '?')} concluídas, saldo {current.get('balance_week', '?')}")


def main_product_progress_only():
    """Atualiza apenas product_progress em data/latest.json (só precisa de *_GITHUB_PRODUCT no .env)."""
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    os.chdir(repo_root)
    path = os.path.join(repo_root, "data", "latest.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}
    print("🐙 Atualizando apenas product_progress (GitHub)…")
    data["product_progress"] = build_product_progress()
    data["generated_at"] = NOW.isoformat()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str, ensure_ascii=False)
    print(f"✅ {path} — campo product_progress atualizado")


if __name__ == "__main__":
    import sys
    if "--product-only" in sys.argv:
        main_product_progress_only()
    else:
        main()
