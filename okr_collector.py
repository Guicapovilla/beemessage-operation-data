"""
okr_collector.py — BeeMessage OKR Collector
Injeta o bloco "okr" no data/latest.json no formato que renderOKRs() espera.

Variáveis de ambiente necessárias:
    STRIPE_SECRET_KEY   — chave secreta da Stripe (sk_live_...)
    PLANE_API_KEY       — chave da API do Plane (plane_api_...)

Opcional:
    LATEST_JSON_PATH    — caminho do latest.json (padrão: data/latest.json)
"""

import os, json, datetime, urllib.request, urllib.parse, urllib.error

# ─── CONFIG ──────────────────────────────────────────────────────────────────

STRIPE_KEY   = os.environ.get("STRIPE_SECRET_KEY", "")
PLANE_KEY    = os.environ.get("PLANE_API_KEY", "")
LATEST_JSON  = os.environ.get("LATEST_JSON_PATH", "data/latest.json")

TRIMESTRE_INICIO = datetime.date(2026, 4, 1)
TRIMESTRE_FIM    = datetime.date(2026, 6, 30)
TICKET_BASE      = 212.53   # ARPU real em 01/04 conforme dashboard Stripe

PRODUTO_PRO         = "prod_TcDLqaVOaBQyhF"
PRODUTOS_ENTERPRISE = {"prod_TkxRRgeB4JwctN", "prod_TmPk46XlVeErwg", "prod_TmPkrqLhIrqiL5"}

PLANE_WORKSPACE  = "bros-mkt"
PLANE_PROJECT    = "8dad6009-bf11-4298-ba54-66ad464a4472"
PLANE_MODULE     = "234d8424-63c4-4ea7-94c7-e1742707df64"

KR3_CONCLUIDAS   = 6
KR3_EM_ANDAMENTO = 2

# ─── HTTP HELPERS ─────────────────────────────────────────────────────────────

def _get(url, headers):
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

def _stripe(endpoint, params=None):
    url = "https://api.stripe.com/v1/" + endpoint
    if params:
        url += "?" + urllib.parse.urlencode(params)
    return _get(url, {"Authorization": "Bearer " + STRIPE_KEY})

def _stripe_all(endpoint, params=None):
    params = dict(params or {})
    params["limit"] = 100
    items = []
    while True:
        data = _stripe(endpoint, params)
        items.extend(data.get("data", []))
        if not data.get("has_more"):
            break
        params["starting_after"] = items[-1]["id"]
    return items

def _plane(endpoint):
    url = "https://api.plane.so/api/v1/" + endpoint
    return _get(url, {"X-API-Key": PLANE_KEY, "Content-Type": "application/json"})

def _plane_all(endpoint):
    """Pagina automaticamente o Plane e retorna todos os resultados."""
    results = []
    cursor = "100:0:0"
    while True:
        sep = "&" if "?" in endpoint else "?"
        data = _plane(f"{endpoint}{sep}cursor={cursor}&per_page=100")
        results.extend(data.get("results", []))
        next_cursor = data.get("next_cursor")
        if not data.get("next_page_results") or not next_cursor:
            break
        cursor = next_cursor
    return results

def _products(sub):
    return [i["price"]["product"] for i in sub.get("items", {}).get("data", []) if i.get("price")]

# ─── SEMANAS ─────────────────────────────────────────────────────────────────

def semanas_restantes():
    dias = (TRIMESTRE_FIM - datetime.date.today()).days
    return max(round(max(dias, 0) / 7, 1), 0.1)

def semana_num():
    return max(1, (datetime.date.today() - TRIMESTRE_INICIO).days // 7 + 1)

# ─── TENDÊNCIA ───────────────────────────────────────────────────────────────

_trend_cache = {}

def _load_trend(key, novo_valor):
    global _trend_cache
    if not _trend_cache:
        try:
            with open(LATEST_JSON, encoding="utf-8") as f:
                okr = json.load(f).get("okr", {})
            _trend_cache = {k: list(okr.get(k, [])) for k in ["kr1_trend","kr2_trend","kr3_trend"]}
        except Exception:
            _trend_cache = {"kr1_trend": [], "kr2_trend": [], "kr3_trend": []}
    trend = list(_trend_cache.get(key, []))
    trend.append(round(novo_valor, 1))
    _trend_cache[key] = trend[-13:]
    return _trend_cache[key]

# ─── KR1 — ARPU via faturas pagas ─────────────────────────────────────────────
# Usa faturas reais pagas no período (não preços cadastrados)
# → Descontos, clientes free e isenções são excluídos automaticamente

def calcular_arpu():
    """
    Soma o valor líquido de todas as faturas de assinatura pagas
    desde o início do trimestre, dividindo por clientes únicos pagantes.
    Isso replica exatamente o que a Stripe exibe como ARPU.
    """
    inicio_ts = int(datetime.datetime.combine(
        TRIMESTRE_INICIO, datetime.time.min
    ).timestamp())

    # Busca faturas pagas desde 01/04
    invoices = _stripe_all("invoices", {
        "status": "paid",
        "created[gte]": inicio_ts,
        "expand[]": "data.subscription",
    })

    # Agrupa por cliente: soma o que pagou de fato (amount_paid - desconto já aplicado)
    receita_por_cliente = {}
    meses_por_cliente   = {}

    for inv in invoices:
        customer = inv.get("customer")
        if not customer:
            continue
        # amount_paid já é o valor líquido após descontos — em centavos
        pago = inv.get("amount_paid", 0)
        if pago <= 0:
            continue  # ignora faturas gratuitas (clientes free)

        # Normaliza para mensal: se fatura cobre vários meses divide pelo período
        periodo_inicio = inv.get("period_start", 0)
        periodo_fim    = inv.get("period_end", 0)
        dias_periodo   = max((periodo_fim - periodo_inicio) / 86400, 1)
        meses_periodo  = dias_periodo / 30.44  # média de dias/mês

        receita_mensal = (pago / 100) / meses_periodo

        receita_por_cliente[customer] = receita_por_cliente.get(customer, 0) + receita_mensal
        meses_por_cliente[customer]   = meses_por_cliente.get(customer, 0) + 1

    n = len(receita_por_cliente) or 1
    # ARPU = média de receita mensal por cliente pagante
    arpu = round(sum(receita_por_cliente.values()) / n, 2)
    return arpu, n, len(invoices)

# ─── KR2 — Conversão Pro → Enterprise ────────────────────────────────────────

def calcular_kr2(subs):
    pro_hoje  = {s["customer"] for s in subs if PRODUTO_PRO in _products(s)}
    inicio_ts = int(datetime.datetime.combine(TRIMESTRE_INICIO, datetime.time.min).timestamp())

    eventos = _stripe_all("events", {
        "type": "customer.subscription.updated",
        "created[gte]": inicio_ts,
    })

    convertidos = set()
    for ev in eventos:
        obj  = ev.get("data", {}).get("object", {})
        prev = ev.get("data", {}).get("previous_attributes", {})
        if not (PRODUTOS_ENTERPRISE & set(_products(obj))):
            continue
        for item in prev.get("items", {}).get("data", []):
            if item.get("price", {}).get("product") == PRODUTO_PRO:
                convertidos.add(obj.get("customer"))
                break

    base_pro = len(pro_hoje) + len(convertidos) or 1
    return len(pro_hoje), len(convertidos), base_pro

# ─── PLANE — tarefas do módulo OKR ───────────────────────────────────────────

def buscar_tarefas_plane():
    if not PLANE_KEY:
        return [], {"total": 0, "concluidas": 0, "em_andamento": 0, "nao_iniciadas": 0}

    try:
        endpoint = (f"workspaces/{PLANE_WORKSPACE}/projects/{PLANE_PROJECT}"
                    f"/modules/{PLANE_MODULE}/work-items/")
        items = _plane_all(endpoint)
    except Exception as e:
        print(f"  ⚠ Plane: {e}")
        return [], {"total": 0, "concluidas": 0, "em_andamento": 0, "nao_iniciadas": 0}

    tarefas = []
    stats   = {"total": 0, "concluidas": 0, "em_andamento": 0, "nao_iniciadas": 0}

    for item in items:
        state      = (item.get("state_detail") or {})
        state_name = state.get("name", "").lower()
        state_grp  = state.get("group", "").lower()

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

        tarefas.append({
            "titulo": item.get("name", ""),
            "estado": state.get("name", "—"),
            "cor":    status_color,
            "priority": item.get("priority", "none"),
            "due_date": item.get("due_date") or "—",
        })

    return tarefas, stats

# ─── INSIGHT ─────────────────────────────────────────────────────────────────

def _gerar_insight(kr1, kr2, kr3, r1, r2, r3, semana, plane_stats):
    if sum([kr1 >= 30, kr2 >= 24, kr3 >= 10]) == 3:
        return "Todos os KRs atingidos — trimestre concluído com sucesso."
    partes = []
    partes.append(f"KR1 em {kr1}% {'— no caminho.' if kr1 >= 15 else f'— ritmo necessário {r1}pp/semana.'}") 
    partes.append(f"KR2 em {kr2}% {'— conversão acelerada.' if kr2 >= 12 else f'— foco em abordagem ativa dos Pro.'}")
    partes.append(f"KR3: {kr3}/10.")
    if plane_stats["total"] > 0:
        pct_plane = round(plane_stats["concluidas"] / plane_stats["total"] * 100)
        partes.append(f"Plane: {plane_stats['concluidas']}/{plane_stats['total']} tarefas concluídas ({pct_plane}%).")
    partes.append(f"Semana {semana} do trimestre.")
    return " ".join(partes)

# ─── CALCULAR TUDO ────────────────────────────────────────────────────────────

def calcular(subs):
    restantes = semanas_restantes()
    semana    = semana_num()

    # KR1
    print("  → Calculando ARPU via faturas pagas...")
    arpu, n_pagantes, n_faturas = calcular_arpu()
    kr1_pct   = round((arpu - TICKET_BASE) / TICKET_BASE * 100, 1)
    kr1_ritmo = round((30 - kr1_pct) / restantes, 2)
    print(f"     ARPU: R${arpu} | {n_pagantes} clientes pagantes | {n_faturas} faturas")

    # KR2
    print("  → Calculando conversão Pro→Enterprise...")
    n_pro, n_convertidos, base_pro = calcular_kr2(subs)
    kr2_pct   = round(n_convertidos / base_pro * 100, 1)
    kr2_ritmo = round((24 - kr2_pct) / restantes, 2)
    print(f"     {n_convertidos} convertidos de {base_pro} clientes Pro base")

    # KR3
    kr3_pct   = round(KR3_CONCLUIDAS / 10 * 100, 1)
    kr3_ritmo = round((10 - KR3_CONCLUIDAS) / restantes, 2)

    # Plane
    print("  → Buscando tarefas do módulo OKR no Plane...")
    tarefas_plane, plane_stats = buscar_tarefas_plane()
    print(f"     {plane_stats['total']} tarefas | {plane_stats['concluidas']} concluídas")

    insight = _gerar_insight(kr1_pct, kr2_pct, KR3_CONCLUIDAS,
                             kr1_ritmo, kr2_ritmo, kr3_ritmo, semana, plane_stats)

    # Monta linhas do card Plane para exibir no detail do KR (máx 6 tarefas)
    plane_rows = []
    for t in tarefas_plane[:6]:
        plane_rows.append({
            "label": t["titulo"][:45] + ("…" if len(t["titulo"]) > 45 else ""),
            "value": t["estado"],
            "color": t["cor"],
        })

    return {
        "insight":   insight,
        "kr1_trend": _load_trend("kr1_trend", kr1_pct),
        "kr2_trend": _load_trend("kr2_trend", kr2_pct),
        "kr3_trend": _load_trend("kr3_trend", KR3_CONCLUIDAS),
        "plane_stats": plane_stats,
        "key_results": [
            {
                "label": "KR1 · Aumentar faturamento da base +30%",
                "icon": "📈", "unit": "%",
                "current": kr1_pct, "target": 30, "delta": None,
                "details": [
                    {"label": "ARPU atual",            "value": f"R${arpu}",               "color": "#e8eaf0"},
                    {"label": "ARPU base (01/abr)",    "value": f"R${TICKET_BASE}",         "color": "#7b8099"},
                    {"label": "Clientes pagantes",     "value": str(n_pagantes),            "color": "#7b8099"},
                    {"label": "Crescimento acumulado", "value": f"{kr1_pct}%",              "color": "#f5a623"},
                    {"label": "Meta trimestral",       "value": "+30%",                     "color": "#7b8099"},
                    {"label": "Ritmo necessário",      "value": f"~{kr1_ritmo}pp / semana", "color": "#7b8099"},
                ],
            },
            {
                "label": "KR2 · Converter 24% dos clientes Pro → Enterprise",
                "icon": "🚀", "unit": "%",
                "current": kr2_pct, "target": 24, "delta": None,
                "details": [
                    {"label": "Clientes Pro ativos",         "value": str(n_pro),           "color": "#e8eaf0"},
                    {"label": "Convertidos para Enterprise", "value": f"{kr2_pct}%",        "color": "#f5a623"},
                    {"label": "Conversões no trimestre",     "value": str(n_convertidos),   "color": "#e8eaf0"},
                    {"label": "Base Pro (início trim.)",     "value": str(base_pro),        "color": "#7b8099"},
                    {"label": "Meta trimestral",             "value": "24%",                "color": "#7b8099"},
                    {"label": "Ritmo necessário",            "value": f"~{kr2_ritmo}pp / semana", "color": "#7b8099"},
                ],
            },
            {
                "label": "KR3 · 10 implementações API realizadas",
                "icon": "⚙️", "unit": "",
                "current": KR3_CONCLUIDAS, "target": 10, "delta": None,
                "details": [
                    {"label": "Implementações concluídas", "value": str(KR3_CONCLUIDAS),   "color": "#60a5fa"},
                    {"label": "Em andamento",              "value": str(KR3_EM_ANDAMENTO), "color": "#f5a623"},
                    {"label": "Meta trimestral",           "value": "10",                  "color": "#7b8099"},
                    {"label": "Ritmo necessário",          "value": f"~{kr3_ritmo} / semana", "color": "#7b8099"},
                    *([{"label": "— Tarefas no Plane —",  "value": "", "color": "#4a4f6a"}] if plane_rows else []),
                    *plane_rows,
                ],
            },
        ],
    }

# ─── MERGE NO latest.json ─────────────────────────────────────────────────────

def merge_latest(okr_block):
    try:
        with open(LATEST_JSON, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data["okr"] = okr_block
    data["okr_updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    pasta = os.path.dirname(LATEST_JSON)
    if pasta:
        os.makedirs(pasta, exist_ok=True)
    with open(LATEST_JSON, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"✓ {LATEST_JSON} atualizado.")

# ─── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    if not STRIPE_KEY:
        print("ERRO: STRIPE_SECRET_KEY não definida.")
        raise SystemExit(1)
    if not PLANE_KEY:
        print("⚠ PLANE_API_KEY não definida — tarefas do Plane serão omitidas.")

    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] Iniciando coleta OKR...")
    subs = _stripe_all("subscriptions", {"status": "active"})
    print(f"  {len(subs)} assinaturas ativas.")

    okr = calcular(subs)
    kr  = okr["key_results"]
    print(f"\n  KR1: {kr[0]['current']}% de +30%")
    print(f"  KR2: {kr[1]['current']}% de 24%")
    print(f"  KR3: {kr[2]['current']}/10")

    merge_latest(okr)

if __name__ == "__main__":
    main()
