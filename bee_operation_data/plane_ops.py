import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

from bee_operation_data import config
from bee_operation_data.common import PRIORITY_KEYS, PRIORITY_MAP, days_between, parse_dt, safe_round
from bee_operation_data.http.plane import plane_get


def _fetch_issues_paged(pid, extra_params, early_exit_cutoff=None, max_pages: int = 200):
    issues = []
    seen_ids = set()
    page = 1
    base_params = {
        "per_page": 100,
        "expand": "state,assignees,labels",
        "order_by": "-updated_at",
    }
    base_params.update(extra_params)
    filter_desc = ", ".join(f"{k}={v}" for k, v in extra_params.items()) or "sem filtro"
    t0 = time.time()
    while page <= max_pages:
        t_page = time.time()
        params = {**base_params, "page": page}
        batch = plane_get(f"projects/{pid}/issues/", params)
        elapsed_page = time.time() - t_page
        if not batch:
            break
        fresh_batch = []
        for iss in batch:
            issue_id = iss.get("id")
            if issue_id and issue_id in seen_ids:
                continue
            if issue_id:
                seen_ids.add(issue_id)
            fresh_batch.append(iss)
        issues.extend(fresh_batch)
        if early_exit_cutoff:
            page_has_recent = False
            for iss in fresh_batch:
                updated = iss.get("updated_at") or iss.get("created_at")
                if updated:
                    try:
                        ut = parse_dt(updated.replace("+00:00", "Z"))
                        if ut and ut >= early_exit_cutoff:
                            page_has_recent = True
                            break
                    except (ValueError, TypeError):
                        page_has_recent = True
                        break
            if not page_has_recent:
                print(
                    f"      p{page}: {len(batch)} issues/{len(fresh_batch)} novas ({elapsed_page:.1f}s) — nenhuma recente, parando early exit ✓",
                    flush=True,
                )
                break
        print(
            f"      p{page}: {len(batch)} issues/{len(fresh_batch)} novas ({elapsed_page:.1f}s) — total {len(issues)}",
            flush=True,
        )
        # Protege contra loop quando a API repete páginas iguais.
        if len(batch) < 100 or not fresh_batch:
            break
        page += 1
    if page > max_pages:
        print(f"      safety stop: limite de {max_pages} páginas atingido", flush=True)
    print(f"      ✓ {len(issues)} issues [{filter_desc}] em {time.time()-t0:.1f}s", flush=True)
    return issues


def _fetch_project_issues(pid, idx, total, prev_week_start):
    proj_name = pid[:8]
    t_proj = time.time()
    print(f"   [{idx+1}/{total}] Projeto {proj_name} — iniciando...", flush=True)
    issues = _fetch_issues_paged(pid, {}, early_exit_cutoff=prev_week_start)
    relevant = []
    skipped = 0
    for iss in issues:
        raw_state = iss.get("state")
        group = raw_state.get("group", "") if isinstance(raw_state, dict) else ""
        if group in ("completed", "cancelled"):
            completed_at = iss.get("completed_at")
            if not completed_at:
                skipped += 1
                continue
            ct = parse_dt(completed_at)
            if not ct or ct < prev_week_start:
                skipped += 1
                continue
        relevant.append(iss)
    elapsed = time.time() - t_proj
    print(
        f"   ✓ [{idx+1}/{total}] Projeto {proj_name}: {len(relevant)} relevantes ({skipped} antigas descartadas) em {elapsed:.1f}s",
        flush=True,
    )
    return relevant


def fetch_all_issues(prev_week_start):
    project_ids = config.plane_project_ids()
    total = len(project_ids)
    t_total = time.time()
    print(f"   Iniciando fetch paralelo de {total} projetos...", flush=True)
    results = {}
    with ThreadPoolExecutor(max_workers=min(total, 7)) as executor:
        futures = {
            executor.submit(_fetch_project_issues, pid, idx, total, prev_week_start): pid
            for idx, pid in enumerate(project_ids)
        }
        for future in as_completed(futures):
            pid = futures[future]
            try:
                results[pid] = future.result()
            except Exception as exc:
                print(f"   Erro no projeto {pid[:8]}: {exc}", flush=True)
                results[pid] = []
    seen = set()
    issues = []
    for pid in project_ids:
        for iss in results.get(pid, []):
            uid = iss.get("id")
            if uid and uid not in seen:
                seen.add(uid)
                issues.append(iss)
    print(
        f"\n   Total: {len(issues)} issues relevantes em {time.time()-t_total:.1f}s (paralelo, {total} projetos)",
        flush=True,
    )
    return issues


def fetch_states():
    states = {}
    for pid in config.plane_project_ids():
        for state in plane_get(f"projects/{pid}/states/"):
            states[state["id"]] = state
    return states


def classify(issues, states, timebox):
    done_states = {sid for sid, state in states.items() if state.get("group") in ("done", "completed")}
    start_states = {
        sid
        for sid, state in states.items()
        if state.get("group") in ("started", "in_progress")
    }
    this_week, prev_week, backlog = [], [], []
    for iss in issues:
        created = parse_dt(iss.get("created_at"))
        completed = parse_dt(iss.get("completed_at"))
        due = parse_dt(iss.get("due_date"))
        raw_state = iss.get("state")
        if isinstance(raw_state, dict):
            state_id = raw_state.get("id")
            state = raw_state
        else:
            state_id = raw_state
            state = states.get(state_id, {})
        enriched = {
            **iss,
            "_created": created,
            "_completed": completed,
            "_due": due,
            "_state_group": state.get("group", ""),
            "_is_done": state_id in done_states,
            "_is_active": state_id in start_states,
            "_cycle_time": days_between(created, completed) if completed else None,
            "_overdue": due and not (state_id in done_states) and due < timebox.now,
            "_overdue_days": (
                int((timebox.now - due).total_seconds() / 86400)
                if due and not (state_id in done_states) and due < timebox.now
                else 0
            ),
            "_assignee_names": [
                a.get("display_name", a.get("email", "?")) for a in (iss.get("assignees") or [])
            ],
            "_area": (iss.get("label_details") or [{}])[0].get("name", "Sem área")
            if iss.get("label_details")
            else "Sem área",
        }
        if created and timebox.week_start <= created <= timebox.week_end:
            this_week.append(enriched)
        elif created and created >= timebox.prev_week_start:
            prev_week.append(enriched)
        backlog.append(enriched)
    return this_week, prev_week, backlog


def compute_metrics(issues, all_issues, prev_week_start):
    done = [i for i in issues if i["_is_done"]]
    overdue = [i for i in issues if i["_overdue"]]
    total = len(issues)
    n_done = len(done)
    rate = round(n_done / total * 100) if total else 0
    cycle_times = [i["_cycle_time"] for i in done if i["_cycle_time"] is not None]
    avg_ct = safe_round(sum(cycle_times) / len(cycle_times)) if cycle_times else None
    chronic = [
        i for i in all_issues if not i["_is_done"] and i["_created"] and i["_created"] < prev_week_start
    ]
    chronic_by_area = defaultdict(int)
    for item in all_issues:
        if not item["_is_done"] and item["_created"] and item["_created"] < prev_week_start:
            chronic_by_area[item["_area"]] += 1

    def _area_default():
        return {
            "total": 0,
            "done": 0,
            "overdue": [],
            "cycle_times": [],
            "backlog": 0,
            "pri_done": {k: {"cts": [], "tasks": []} for k in PRIORITY_KEYS},
        }

    areas = defaultdict(_area_default)
    for item in issues:
        area = item["_area"]
        areas[area]["total"] += 1
        if item["_is_done"]:
            areas[area]["done"] += 1
            if item["_cycle_time"]:
                areas[area]["cycle_times"].append(item["_cycle_time"])
            pri = PRIORITY_MAP.get(item.get("priority") or "none", "sem")
            areas[area]["pri_done"][pri]["tasks"].append(item.get("name", "?")[:60])
            if item["_cycle_time"] is not None:
                areas[area]["pri_done"][pri]["cts"].append(item["_cycle_time"])
        if item["_overdue"]:
            areas[area]["overdue"].append(item)
        if not item["_is_done"]:
            areas[area]["backlog"] += 1

    areas_list = []
    for name, data in sorted(areas.items()):
        t = data["total"]
        dn = data["done"]
        r = round(dn / t * 100) if t else 0
        n_od = len(data["overdue"])
        n_roll = chronic_by_area.get(name, 0)
        if n_od == 0 and r >= 90:
            score = "A"
        elif n_od <= 1 and r >= 75:
            score = "B"
        elif n_od <= 2 and r >= 60:
            score = "C"
        else:
            score = "D"
        pri_breakdown = {}
        for pk in PRIORITY_KEYS:
            pd = data["pri_done"][pk]
            cts = pd["cts"]
            pri_breakdown[pk] = {
                "count": len(pd["tasks"]),
                "avg_days": safe_round(sum(cts) / len(cts)) if cts else None,
                "min_days": safe_round(min(cts)) if cts else None,
                "max_days": safe_round(max(cts)) if cts else None,
                "tasks": pd["tasks"][-3:][::-1],
            }
        areas_list.append(
            {
                "name": name,
                "score": score,
                "total": t,
                "done": dn,
                "rate": r,
                "overdue": n_od,
                "rollover": n_roll,
                "backlog": data["backlog"],
                "priority_breakdown": pri_breakdown,
            }
        )

    collab = defaultdict(lambda: {"tasks": 0, "done": 0, "overdue": [], "areas": set()})
    for item in issues:
        for name in (item["_assignee_names"] or ["Sem dono"]):
            collab[name]["tasks"] += 1
            collab[name]["areas"].add(item["_area"])
            if item["_is_done"]:
                collab[name]["done"] += 1
            if item["_overdue"]:
                collab[name]["overdue"].append(
                    {"title": item.get("name", "?")[:60], "days": item["_overdue_days"], "area": item["_area"]}
                )
    task_counts = sorted([v["tasks"] for v in collab.values()])
    median = task_counts[len(task_counts) // 2] if task_counts else 0
    overload_threshold = max(8, median * 1.8)
    collabs_list = []
    for name, data in sorted(collab.items(), key=lambda x: -len(x[1]["overdue"])):
        n_od = len(data["overdue"])
        chronic_flag = any(od["days"] >= 14 for od in data["overdue"])
        overloaded = data["tasks"] >= overload_threshold
        if n_od == 0 and not overloaded:
            severity = "ok"
        elif chronic_flag:
            severity = "critical"
        elif n_od > 0 or overloaded:
            severity = "warn"
        else:
            severity = "ok"
        collabs_list.append(
            {
                "name": name,
                "tasks": data["tasks"],
                "done": data["done"],
                "rate": round(data["done"] / data["tasks"] * 100) if data["tasks"] else 0,
                "overdue": data["overdue"],
                "areas": list(data["areas"]),
                "overloaded": overloaded,
                "chronic": chronic_flag,
                "severity": severity,
            }
        )
    overdue_list = sorted(
        [
            {
                "title": i.get("name", "?")[:60],
                "area": i["_area"],
                "days": i["_overdue_days"],
                "assignees": i["_assignee_names"],
                "due": i["_due"].strftime("%d/%m") if i["_due"] else "—",
            }
            for i in overdue
        ],
        key=lambda x: -x["days"],
    )
    return {
        "total": total,
        "done": n_done,
        "rate": rate,
        "avg_cycle_time": avg_ct,
        "overdue": len(overdue),
        "chronic_rollover": len(chronic),
        "areas": areas_list,
        "collaborators": collabs_list,
        "overdue_tasks": overdue_list,
    }


def apply_week_flow_metrics(current, this_week, all_issues, timebox):
    created_week = len(this_week)
    completed_week = sum(
        1
        for issue in all_issues
        if issue.get("_completed") and timebox.week_start <= issue["_completed"] <= timebox.week_end
    )
    current["created_week"] = created_week
    current["completed_week"] = completed_week
    current["balance_week"] = created_week - completed_week

