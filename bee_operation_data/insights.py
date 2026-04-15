import json
import os
import time

import requests

from bee_operation_data import config


def _anthropic_headers():
    return {
        "x-api-key": os.environ["ANTHROPIC_API_KEY"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }


def generate_insights(current, previous):
    delta_rate = current["rate"] - previous["rate"]
    delta_ct = (current["avg_cycle_time"] or 0) - (previous["avg_cycle_time"] or 0)
    delta_od = current["overdue"] - previous["overdue"]
    delta_ch = current["chronic_rollover"] - previous["chronic_rollover"]
    critical_collabs = [c for c in current["collaborators"] if c["severity"] == "critical"]
    overloaded = [c for c in current["collaborators"] if c["overloaded"]]
    worst_areas = [a for a in current["areas"] if a["score"] in ("C", "D")]
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
    model_name, max_tokens = config.anthropic_model(), config.anthropic_max_tokens()
    print(f"   → API Anthropic: modelo {model_name}, max_tokens={max_tokens}")
    for attempt in range(3):
        try:
            resp = requests.post(
                "https://api.anthropic.com/v1/messages",
                headers=_anthropic_headers(),
                json={"model": model_name, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]},
                timeout=(10, 60),
            )
            resp.raise_for_status()
            break
        except requests.exceptions.Timeout:
            if attempt < 2:
                wait = 10 * (2**attempt)
                print(f"   Claude timeout (tentativa {attempt+1}/3) — aguardando {wait}s...")
                time.sleep(wait)
            else:
                raise TimeoutError("Claude API não respondeu após 3 tentativas")
        except requests.exceptions.RequestException:
            if attempt < 2:
                time.sleep(10 * (2**attempt))
            else:
                raise
    data = resp.json()
    if data.get("stop_reason") == "max_tokens":
        return {
            "headline": "Análise indisponível — resposta truncada",
            "status": "neutro",
            "summary": "O modelo atingiu o limite de tokens antes de completar o JSON.",
            "highlights": [],
            "alerts": [],
            "overload_analysis": "—",
            "projections": [],
            "action_items": [],
        }
    text = data["content"][0]["text"].strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        return {
            "headline": "Análise indisponível — JSON inválido",
            "status": "neutro",
            "summary": "Não foi possível parsear a resposta do modelo.",
            "highlights": [],
            "alerts": [],
            "overload_analysis": "—",
            "projections": [],
            "action_items": [],
        }

