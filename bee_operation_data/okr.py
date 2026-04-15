import os
from dataclasses import dataclass
from datetime import datetime, time as dtime

from bee_operation_data import config
from bee_operation_data.http.plane import plane_get_in_workspace
from bee_operation_data.http.stripe import stripe_all


@dataclass
class TrendStore:
    previous_okr: dict | None = None
    cache: dict | None = None

    def load_trend(self, key: str, value: float):
        if self.cache is None:
            okr = self.previous_okr or {}
            self.cache = {k: list(okr.get(k, [])) for k in ["kr1_trend", "kr2_trend", "kr3_trend"]}
        trend = list(self.cache.get(key, []))
        trend.append(round(value, 1))
        self.cache[key] = trend[-13:]
        return self.cache[key]


def _products(subscription: dict) -> list[str]:
    return [
        item["price"]["product"]
        for item in subscription.get("items", {}).get("data", [])
        if item.get("price")
    ]


def _item_mrr_monthly_reais(item):
    price = item.get("price")
    if isinstance(price, str):
        return None
    if not price:
        plan = item.get("plan")
        if isinstance(plan, dict):
            price = plan
        else:
            return 0.0
    amt = price.get("unit_amount")
    if amt is None:
        return 0.0
    qty = item.get("quantity", 1) or 1
    rec = price.get("recurring") or {}
    interval = rec.get("interval") or "month"
    icount = rec.get("interval_count") or 1
    if icount < 1:
        icount = 1
    total = (amt * qty) / 100.0
    if interval == "month":
        return total / icount
    if interval == "year":
        return total / (12 * icount)
    if interval == "week":
        return total * (52.0 / 12.0) / icount
    if interval == "day":
        return total * (365.0 / 12.0) / icount
    return total


def calcular_arpu(subs):
    mrr_por_cliente = {}
    itens_sem_preco = 0
    for sub in subs:
        cust = sub.get("customer")
        if not cust:
            continue
        mrr_por_cliente.setdefault(cust, 0.0)
        for item in (sub.get("items") or {}).get("data") or []:
            monthly = _item_mrr_monthly_reais(item)
            if monthly is None:
                itens_sem_preco += 1
                continue
            mrr_por_cliente[cust] += monthly
    n = len(mrr_por_cliente)
    if n == 0:
        return 0.0, 0, itens_sem_preco
    arpu = round(sum(mrr_por_cliente.values()) / n, 2)
    return arpu, n, itens_sem_preco


def calcular_kr2(subs, stripe_key: str, okr_cfg: config.OkrConfig):
    pro_hoje = {s["customer"] for s in subs if okr_cfg.produto_pro in _products(s)}
    inicio_ts = int(datetime.combine(okr_cfg.trimestre_inicio, dtime.min).timestamp())
    eventos = stripe_all(
        "events",
        stripe_key,
        {"type": "customer.subscription.updated", "created[gte]": inicio_ts},
    )
    convertidos = set()
    for ev in eventos:
        obj = ev.get("data", {}).get("object", {})
        prev = ev.get("data", {}).get("previous_attributes", {})
        if not (okr_cfg.produtos_enterprise & set(_products(obj))):
            continue
        for item in prev.get("items", {}).get("data", []):
            if item.get("price", {}).get("product") == okr_cfg.produto_pro:
                convertidos.add(obj.get("customer"))
                break
    base_pro = len(pro_hoje) + len(convertidos) or 1
    return len(pro_hoje), len(convertidos), base_pro


def _fetch_module_issues(okr_cfg: config.OkrConfig, max_pages: int = 50) -> list[dict]:
    endpoint = f"projects/{okr_cfg.plane_project_id}/modules/{okr_cfg.plane_module_id}/module-issues/"
    params = {"per_page": 100, "expand": "state_detail", "order_by": "-updated_at"}
    results = []
    seen_ids = set()
    for page in range(1, max_pages + 1):
        batch = plane_get_in_workspace(okr_cfg.plane_workspace, endpoint, {**params, "page": page})
        if not isinstance(batch, list):
            raise RuntimeError("Resposta inesperada do Plane para module-issues")
        if not batch:
            break
        fresh = 0
        for item in batch:
            issue_id = item.get("issue_id") or item.get("id")
            if issue_id and issue_id in seen_ids:
                continue
            if issue_id:
                seen_ids.add(issue_id)
            results.append(item)
            fresh += 1
        print(f"   Plane módulo OKR p{page}: {len(batch)} itens ({fresh} novos)", flush=True)
        if len(batch) < params["per_page"] or fresh == 0:
            break
    else:
        print(f"   Plane módulo OKR interrompido após {max_pages} páginas (safety)", flush=True)
    return results


def buscar_tarefas_plane(okr_cfg: config.OkrConfig):
    empty_stats = {"total": 0, "concluidas": 0, "em_andamento": 0, "nao_iniciadas": 0}
    if not config.plane_api_token():
        return [], empty_stats
    if not okr_cfg.plane_workspace or not okr_cfg.plane_project_id or not okr_cfg.plane_module_id:
        return [], empty_stats
    try:
        items = _fetch_module_issues(okr_cfg)
    except Exception as exc:
        print(f"  Plane módulo OKR indisponível: {exc}")
        return [], empty_stats
    tarefas = []
    stats = dict(empty_stats)
    for item in items:
        state = item.get("state_detail") or {}
        state_name = state.get("name", "").lower()
        state_grp = state.get("group", "").lower()
        stats["total"] += 1
        if state_grp in ("done", "completed", "cancelled"):
            stats["concluidas"] += 1
            status_color = "#3ecf8e"
        elif state_grp in ("started", "in_progress", "unstarted"):
            if "progress" in state_name or "andamento" in state_name:
                stats["em_andamento"] += 1
                status_color = "#f5a623"
            else:
                stats["nao_iniciadas"] += 1
                status_color = "#4a4f6a"
        else:
            stats["nao_iniciadas"] += 1
            status_color = "#4a4f6a"
        tarefas.append({"titulo": item.get("name", ""), "estado": state.get("name", "—"), "cor": status_color})
    return tarefas, stats


def _gerar_insight(kr1, kr2, kr3, r1, r2, r3, semana, plane_stats):
    if sum([kr1 >= 30, kr2 >= 24, kr3 >= 10]) == 3:
        return "Todos os KRs atingidos — trimestre concluído com sucesso."
    partes = []
    partes.append(f"KR1 em {kr1:.1f}% {'— no caminho.' if kr1 >= 15 else f'— ritmo necessário {r1:.1f}pp/semana.'}")
    partes.append(f"KR2 em {kr2:.1f}% {'— conversão acelerada.' if kr2 >= 12 else '— foco em abordagem ativa dos Pro.'}")
    partes.append(f"KR3: {kr3}/10.")
    if plane_stats["total"] > 0:
        pct_plane = round(plane_stats["concluidas"] / plane_stats["total"] * 100)
        partes.append(f"Plane: {plane_stats['concluidas']}/{plane_stats['total']} tarefas concluídas ({pct_plane}%).")
    partes.append(f"Semana {semana} do trimestre.")
    return " ".join(partes)


def build_okr_block(subs, previous_okr: dict | None = None):
    okr_cfg = config.load_okr_config()
    trend = TrendStore(previous_okr)
    stripe_key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not stripe_key:
        raise RuntimeError("STRIPE_SECRET_KEY não definida")
    restantes = max(round(max((okr_cfg.trimestre_fim - datetime.now().date()).days, 0) / 7, 1), 0.1)
    semana = max(1, (datetime.now().date() - okr_cfg.trimestre_inicio).days // 7 + 1)
    arpu, n_pagantes, _n_sem_preco = calcular_arpu(subs)
    kr1_pct = round((arpu - okr_cfg.ticket_base) / okr_cfg.ticket_base * 100, 1) if okr_cfg.ticket_base else 0.0
    kr1_ritmo = round((30 - kr1_pct) / restantes, 2)
    n_pro, n_convertidos, base_pro = calcular_kr2(subs, stripe_key, okr_cfg)
    kr2_pct = round(n_convertidos / base_pro * 100, 1)
    kr2_ritmo = round((24 - kr2_pct) / restantes, 2)
    tarefas_plane, plane_stats = buscar_tarefas_plane(okr_cfg)
    kr3_concluidas = plane_stats["concluidas"] if plane_stats["total"] else okr_cfg.kr3_concluidas_fallback
    kr3_em_andamento = (
        plane_stats["em_andamento"] if plane_stats["total"] else okr_cfg.kr3_em_andamento_fallback
    )
    kr3_ritmo = round((10 - kr3_concluidas) / restantes, 2)
    insight = _gerar_insight(kr1_pct, kr2_pct, kr3_concluidas, kr1_ritmo, kr2_ritmo, kr3_ritmo, semana, plane_stats)
    plane_rows = [
        {
            "label": t["titulo"][:45] + ("…" if len(t["titulo"]) > 45 else ""),
            "value": t["estado"],
            "color": t["cor"],
        }
        for t in tarefas_plane[:6]
    ]
    return {
        "insight": insight,
        "kr1_trend": trend.load_trend("kr1_trend", kr1_pct),
        "kr2_trend": trend.load_trend("kr2_trend", kr2_pct),
        "kr3_trend": trend.load_trend("kr3_trend", kr3_concluidas),
        "plane_stats": plane_stats,
        "key_results": [
            {
                "label": "KR1 · Aumentar faturamento da base +30%",
                "icon": "📈",
                "unit": "%",
                "current": kr1_pct,
                "target": 30,
                "delta": None,
                "details": [
                    {"label": "ARPU atual", "value": f"R${arpu}", "color": "#e8eaf0"},
                    {"label": "ARPU base (01/abr)", "value": f"R${okr_cfg.ticket_base}", "color": "#7b8099"},
                    {"label": "Clientes pagantes", "value": str(n_pagantes), "color": "#7b8099"},
                    {"label": "Crescimento acumulado", "value": f"{kr1_pct}%", "color": "#f5a623"},
                    {"label": "Meta trimestral", "value": "+30%", "color": "#7b8099"},
                    {"label": "Ritmo necessário", "value": f"~{kr1_ritmo}pp / semana", "color": "#7b8099"},
                ],
            },
            {
                "label": "KR2 · Converter 24% dos clientes Pro → Enterprise",
                "icon": "🚀",
                "unit": "%",
                "current": kr2_pct,
                "target": 24,
                "delta": None,
                "details": [
                    {"label": "Clientes Pro ativos", "value": str(n_pro), "color": "#e8eaf0"},
                    {"label": "Convertidos para Enterprise", "value": f"{kr2_pct}%", "color": "#f5a623"},
                    {"label": "Conversões no trimestre", "value": str(n_convertidos), "color": "#e8eaf0"},
                    {"label": "Base Pro (início trim.)", "value": str(base_pro), "color": "#7b8099"},
                    {"label": "Meta trimestral", "value": "24%", "color": "#7b8099"},
                    {"label": "Ritmo necessário", "value": f"~{kr2_ritmo}pp / semana", "color": "#7b8099"},
                ],
            },
            {
                "label": "KR3 · 10 implementações API realizadas",
                "icon": "⚙️",
                "unit": "",
                "current": kr3_concluidas,
                "target": 10,
                "delta": None,
                "details": [
                    {"label": "Implementações concluídas", "value": str(kr3_concluidas), "color": "#60a5fa"},
                    {"label": "Em andamento", "value": str(kr3_em_andamento), "color": "#f5a623"},
                    {"label": "Meta trimestral", "value": "10", "color": "#7b8099"},
                    {"label": "Ritmo necessário", "value": f"~{kr3_ritmo} / semana", "color": "#7b8099"},
                    *([{"label": "— Tarefas no Plane —", "value": "", "color": "#4a4f6a"}] if plane_rows else []),
                    *plane_rows,
                ],
            },
        ],
    }

