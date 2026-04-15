import argparse
import os
from datetime import timezone

from bee_operation_data import config, persistence
from bee_operation_data.github_progress import build_product_progress
from bee_operation_data.http.stripe import stripe_all
from bee_operation_data.insights import generate_insights
from bee_operation_data.okr import build_okr_block
from bee_operation_data.plane_ops import (
    apply_week_flow_metrics,
    classify,
    compute_metrics,
    fetch_all_issues,
    fetch_states,
)
from bee_operation_data.time_window import current_week_window


def _ensure_week_metadata(data: dict, timebox) -> dict:
    week_key = data.get("week_key") or persistence.week_key(timebox.week_start)
    week_label = data.get("week_label") or (
        f"Semana {timebox.week_start.isocalendar()[1]} · {timebox.week_start.strftime('%B %Y').capitalize()}"
    )
    week_range = data.get("week_range") or f"{timebox.week_start.strftime('%d/%m')} – {timebox.week_end.strftime('%d/%m/%Y')}"
    data["week_key"] = week_key
    data["week_label"] = week_label
    data["week_range"] = week_range
    return data


def run_full_pipeline():
    config.load_repo_dotenv()
    timebox = current_week_window()
    print(
        f"Buscando issues do Plane... (janela: {timebox.week_start.strftime('%d/%m')} – {timebox.week_end.strftime('%d/%m/%Y')})"
    )
    print(f"Projetos: {len(config.plane_project_ids())}", flush=True)
    issues = fetch_all_issues(timebox.prev_week_start)
    print("\nBuscando estados...", flush=True)
    states = fetch_states()
    print(f"{len(issues)} issues carregadas | {len(states)} estados", flush=True)

    print("\nClassificando e calculando métricas...", flush=True)
    this_week, prev_week_issues, all_issues = classify(issues, states, timebox)
    current = compute_metrics(this_week, all_issues, timebox.prev_week_start)
    previous = compute_metrics(prev_week_issues, all_issues, timebox.prev_week_start)
    apply_week_flow_metrics(current, this_week, all_issues, timebox)

    stripe_key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not stripe_key:
        raise SystemExit("STRIPE_SECRET_KEY é obrigatória para o OKR unificado")
    print("\nBuscando Stripe para OKR...", flush=True)
    subs = stripe_all(
        "subscriptions",
        stripe_key,
        {"status": "active", "expand[]": "data.items.data.price"},
    )
    latest_payload = persistence.load_latest() or {}
    okr_block = build_okr_block(subs, latest_payload.get("okr"))

    print("Gerando insights com Claude...")
    insights = generate_insights(current, previous)

    print("Buscando progresso do projeto no GitHub...")
    product_progress = build_product_progress(timebox)

    week_key = persistence.week_key(timebox.week_start)
    week_label = f"Semana {timebox.week_start.isocalendar()[1]} · {timebox.week_start.strftime('%B %Y').capitalize()}"
    week_range = f"{timebox.week_start.strftime('%d/%m')} – {timebox.week_end.strftime('%d/%m/%Y')}"
    snapshots = persistence.load_snapshots()
    prev_entry = snapshots[-1] if snapshots else None
    prev_week_out = persistence.load_week(prev_entry["key"]) if prev_entry and prev_entry.get("key") else None
    prev_current = (prev_week_out or {}).get("current", previous)

    output = {
        "generated_at": timebox.now.astimezone(timezone.utc).isoformat(),
        "week_key": week_key,
        "week_label": week_label,
        "week_range": week_range,
        "current": current,
        "previous": prev_current,
        "insights": insights,
        "okr": okr_block,
        "product_progress": product_progress,
        "trend": persistence.build_trend_weeks(snapshots, week_label),
    }
    persisted_key = persistence.save_week(output, week_key)
    persistence.save_latest(output)
    print(f"Semana {persisted_key} salva no Supabase")
    print(f"Painel atualizado — {week_label} ({week_range})")


def run_product_progress_only():
    config.load_repo_dotenv()
    timebox = current_week_window()
    data = _ensure_week_metadata(persistence.load_latest() or {}, timebox)
    data["product_progress"] = build_product_progress(timebox)
    data["generated_at"] = timebox.now.isoformat()
    persistence.save_week(data, data["week_key"])
    print(f"Supabase — product_progress atualizado em {data['week_key']}")


def run_okr_only():
    config.load_repo_dotenv()
    stripe_key = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    if not stripe_key:
        raise SystemExit("STRIPE_SECRET_KEY não definida.")
    subs = stripe_all(
        "subscriptions",
        stripe_key,
        {"status": "active", "expand[]": "data.items.data.price"},
    )
    timebox = current_week_window()
    data = _ensure_week_metadata(persistence.load_latest() or {}, timebox)
    okr = build_okr_block(subs, data.get("okr"))
    data["okr"] = okr
    data["okr_updated_at"] = timebox.now.isoformat()
    data["generated_at"] = timebox.now.isoformat()
    persistence.save_week(data, data["week_key"])
    print(f"Supabase — bloco OKR atualizado em {data['week_key']}.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--product-only", action="store_true")
    parser.add_argument("--okr-only", action="store_true")
    args = parser.parse_args()
    if args.product_only:
        run_product_progress_only()
        return
    if args.okr_only:
        run_okr_only()
        return
    run_full_pipeline()

