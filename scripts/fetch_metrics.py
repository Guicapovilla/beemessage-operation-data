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
import requests
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ── Configuração ──────────────────────────────────────────────────────────────
PLANE_BASE     = "https://api.plane.so/api/v1"
PLANE_TOKEN    = os.environ["PLANE_API_TOKEN"]
PLANE_SLUG     = os.environ["PLANE_WORKSPACE_SLUG"]   # ex: "beemessage"
PROJECT_IDS    = os.environ["PLANE_PROJECT_IDS"].split(",")  # ex: "proj-uuid1,proj-uuid2"
ANTHROPIC_KEY  = os.environ["ANTHROPIC_API_KEY"]

HEADERS_PLANE  = {"X-API-Key": PLANE_TOKEN, "Content-Type": "application/json"}
HEADERS_AI     = {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}

NOW            = datetime.now(timezone.utc)
WEEK_START     = (NOW - timedelta(days=NOW.weekday() + 1)).replace(hour=0, minute=0, second=0)  # segunda passada
PREV_WEEK_START= WEEK_START - timedelta(days=7)


# ── Helpers ───────────────────────────────────────────────────────────────────
def plane_get(path, params=None):
    url = f"{PLANE_BASE}/workspaces/{PLANE_SLUG}/{path}"
    for attempt in range(5):
        r = requests.get(url, headers=HEADERS_PLANE, params=params or {})
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


# ── Fetch all issues ──────────────────────────────────────────────────────────
def fetch_all_issues():
    issues = []
    for pid in PROJECT_IDS:
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
    for pid in PROJECT_IDS:
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

    # Per-area metrics
    areas = defaultdict(lambda: {"total":0,"done":0,"overdue":[],"cycle_times":[],"backlog":0})
    for i in issues:
        a = i["_area"]
        areas[a]["total"] += 1
        if i["_is_done"]:
            areas[a]["done"] += 1
            if i["_cycle_time"]:
                areas[a]["cycle_times"].append(i["_cycle_time"])
        if i["_overdue"]:
            areas[a]["overdue"].append(i)
        if not i["_is_done"]:
            areas[a]["backlog"] += 1

    areas_list = []
    for name, d in sorted(areas.items()):
        t = d["total"]
        dn = d["done"]
        r = round(dn / t * 100) if t else 0
        ct = safe_round(sum(d["cycle_times"]) / len(d["cycle_times"])) if d["cycle_times"] else None
        n_od = len(d["overdue"])
        # Score: A=no overdue + rate>=90; B=0-1 overdue + rate>=75; C=1-2 overdue or rate>=60; D=else
        if n_od == 0 and r >= 90:   score = "A"
        elif n_od <= 1 and r >= 75: score = "B"
        elif n_od <= 2 and r >= 60: score = "C"
        else:                        score = "D"
        areas_list.append({
            "name": name, "score": score, "total": t, "done": dn,
            "rate": r, "overdue": n_od, "cycle_time": ct, "backlog": d["backlog"]
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

    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers=HEADERS_AI,
        json={"model": "claude-sonnet-4-20250514", "max_tokens": 1000,
              "messages": [{"role": "user", "content": prompt}]}
    )
    resp.raise_for_status()
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


def trend_row_from_snapshot(snap):
    cur = (snap or {}).get("current") or {}
    if not isinstance(cur, dict):
        cur = {}
    total = int(cur.get("total") or 0)
    done = int(cur.get("done") or 0)
    row = {
        "week": (snap or {}).get("week_label", "") or "",
        "rate": cur.get("rate", 0) or 0,
        "overdue": cur.get("overdue", 0) or 0,
        "cycle_time": cur.get("avg_cycle_time") or 0,
        "total": total,
        "done": done,
        "remaining": max(0, total - done),
    }
    if "created_week" in cur:
        row["created_week"] = cur.get("created_week")
    if "completed_week" in cur:
        row["completed_week"] = cur.get("completed_week")
    if "balance_week" in cur:
        row["balance_week"] = cur.get("balance_week")
    return row


def trend_row_current(week_label, current):
    total = int(current.get("total") or 0)
    done = int(current.get("done") or 0)
    row = {
        "week": week_label,
        "rate": current.get("rate", 0),
        "overdue": current.get("overdue", 0),
        "cycle_time": current.get("avg_cycle_time") or 0,
        "total": total,
        "done": done,
        "remaining": max(0, total - done),
    }
    if "created_week" in current:
        row["created_week"] = current.get("created_week")
    if "completed_week" in current:
        row["completed_week"] = current.get("completed_week")
    if "balance_week" in current:
        row["balance_week"] = current.get("balance_week")
    return row


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

    snapshots = load_snapshots()
    # Find previous snapshot for trend chart
    prev_snapshot = snapshots[-1] if snapshots else previous

    week_label = f"Semana {NOW.isocalendar()[1]} · {NOW.strftime('%B %Y').capitalize()}"

    output = {
        "generated_at": NOW.isoformat(),
        "week_label": week_label,
        "current": current,
        "previous": prev_snapshot.get("current", previous) if isinstance(prev_snapshot, dict) else previous,
        "insights": insights,
        "trend": [trend_row_from_snapshot(s) for s in snapshots[-5:]]
        + [trend_row_current(week_label, current)],
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
        for pid in PROJECT_IDS:
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
        "trend": [trend_row_from_snapshot(s) for s in snapshots[-5:]]
        + [trend_row_current(week_label, current)],
    }

    snapshots.append({"week_label": week_label, "current": current})
    save_snapshots(snapshots)

    os.makedirs("data", exist_ok=True)
    with open("data/latest.json", "w") as f:
        json.dump(output, f, indent=2, default=str, ensure_ascii=False)

    print(f"✅ data/latest.json salvo — semana {week_label}")
    print(f"   Taxa: {current['rate']}% | C.Time: {current['avg_cycle_time']}d | Atrasos: {current['overdue']}")
    print(f"   Fluxo semana: {current.get('created_week', '?')} criadas, {current.get('completed_week', '?')} concluídas, saldo {current.get('balance_week', '?')}")


if __name__ == "__main__":
    main()
