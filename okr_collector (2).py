"""
okr_collector.py — BeeMessage OKR Collector (versão Google Sheets)

Coleta o KR2 (conversões para Enterprise) da Stripe e envia para o
Apps Script Web App, que escreve na planilha OKR_Tracker_Faturamento.

KR1 (ARPU) e KR3 (implementações de API) NÃO são enviados aqui —
são preenchidos manualmente na planilha. Esta decisão é proposital:
a Stripe não expõe o cálculo de ARPU via API.

Variáveis de ambiente necessárias:
    STRIPE_SECRET_KEY   chave sk_live_... da Stripe
    SHEETS_WEBAPP_URL   URL do deploy do Apps Script Web App
    SHEETS_TOKEN        mesmo token definido no Apps Script (TOKEN)

Uso local:
    export STRIPE_SECRET_KEY=sk_live_...
    export SHEETS_WEBAPP_URL=https://script.google.com/macros/s/.../exec
    export SHEETS_TOKEN=...
    python okr_collector.py
"""

import os
import json
import datetime
import urllib.request
import urllib.parse
import urllib.error

STRIPE_KEY = os.environ.get("STRIPE_SECRET_KEY")
WEBAPP_URL = os.environ.get("SHEETS_WEBAPP_URL")
SHEETS_TOKEN = os.environ.get("SHEETS_TOKEN")

# Produtos Enterprise (migração para Enterprise conta como conversão do KR2)
ENTERPRISE_PRODS = {
    "prod_TkxRRgeB4JwctN",
    "prod_TmPk46XlVeErwg",
    "prod_TmPkrqLhIrqiL5",
}

# Início do ciclo OKR
CICLO_INICIO = datetime.datetime(2026, 4, 1, tzinfo=datetime.timezone.utc)

STRIPE_BASE = "https://api.stripe.com/v1"


# ─── STRIPE ───────────────────────────────────────────────────────────────────

def _stripe_get(path, params=None):
    url = f"{STRIPE_BASE}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {STRIPE_KEY}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _stripe_all(path, params=None):
    """Pagina automaticamente todos os resultados."""
    params = dict(params or {})
    params["limit"] = 100
    out = []
    while True:
        data = _stripe_get(path, params)
        out.extend(data.get("data", []))
        if not data.get("has_more"):
            break
        params["starting_after"] = data["data"][-1]["id"]
    return out


def calcular_kr2():
    """
    KR2: % de clientes que migraram para Enterprise.
    Numerador  = clientes que viraram Enterprise desde o início do ciclo.
    Denominador = total de assinaturas ativas.
    """
    subs = _stripe_all("subscriptions", {"status": "active"})
    total = len(subs)

    enterprise_atual = 0
    for s in subs:
        for item in s.get("items", {}).get("data", []):
            prod = item.get("price", {}).get("product")
            if prod in ENTERPRISE_PRODS:
                enterprise_atual += 1
                break

    pct = (enterprise_atual / total * 100) if total else 0.0
    return {
        "atual": round(pct, 1),
        "enterprise": enterprise_atual,
        "total_ativos": total,
    }


# ─── ENVIO PARA O SHEETS ────────────────────────────────────────────────────

def enviar(kr2):
    payload = json.dumps({
        "token": SHEETS_TOKEN,
        "kr2": kr2,
    }).encode()

    req = urllib.request.Request(WEBAPP_URL, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read().decode())
        if not resp.get("ok"):
            print(f"ERRO no Web App: {resp.get('error')}")
            raise SystemExit(1)
        print(f"  OK — atualizado em {resp.get('atualizado_em')}")
    except urllib.error.HTTPError as e:
        # Apps Script costuma responder 302 (redirect) — urllib segue sozinho.
        print(f"ERRO HTTP {e.code}: {e.read().decode()[:200]}")
        raise SystemExit(1)


# ─── MAIN ───────────────────────────────────────────────────────────────────

def main():
    faltando = [n for n, v in [
        ("STRIPE_SECRET_KEY", STRIPE_KEY),
        ("SHEETS_WEBAPP_URL", WEBAPP_URL),
        ("SHEETS_TOKEN", SHEETS_TOKEN),
    ] if not v]
    if faltando:
        print(f"ERRO: variáveis não definidas: {', '.join(faltando)}")
        raise SystemExit(1)

    print(f"[{datetime.datetime.now():%Y-%m-%d %H:%M}] Coletando Stripe...")
    kr2 = calcular_kr2()
    print(f"  KR2: {kr2['atual']}% ({kr2['enterprise']}/{kr2['total_ativos']} ativos) — meta 24%")

    print("Enviando para a planilha...")
    enviar(kr2)


if __name__ == "__main__":
    main()
