import os
import unicodedata
from datetime import date, timedelta

import requests

from bee_operation_data.common import PRIORITY_KEYS, days_between, parse_dt, safe_round

_GH_PRI_PATTERNS = {
    "urgente": ["urgent", "urgente", "crítico", "critico", "critical", "p0"],
    "alta": ["high", "alta", "p1"],
    "media": ["medium", "média", "media", "normal", "p2"],
    "baixa": ["low", "baixa", "minor", "p3"],
}


def github_priority_from_labels(label_names: list[str], product_label: str = "") -> str:
    overrides = {}
    for pk in PRIORITY_KEYS[:-1]:
        env_val = os.environ.get(f"PRIORITY_{pk.upper()}_GITHUB_PRODUCT", "")
        if env_val:
            overrides[pk] = {v.strip().lower() for v in env_val.split(",") if v.strip()}
    lowers = {n.lower() for n in label_names if n.lower() != product_label.lower()}
    for pk in ["urgente", "alta", "media", "baixa"]:
        candidates = overrides.get(pk) or {p for p in _GH_PRI_PATTERNS[pk]}
        if lowers & candidates:
            return pk
    return "sem"


def _empty_pp_extras():
    empty_bd = {
        k: {"count": 0, "avg_days": None, "min_days": None, "max_days": None, "tasks": []}
        for k in PRIORITY_KEYS
    }
    return {
        "rollover": 0,
        "avg_cycle_time_sprint": None,
        "priority_breakdown": empty_bd,
        "sprint": {"current": None, "previous": None},
        "previous_sprint": None,
    }


def _gql_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _new_bucket():
    return {
        "planned": [],
        "in_progress": [],
        "completed": [],
        "rollover": 0,
        "pri_done": {k: {"cts": [], "tasks": []} for k in PRIORITY_KEYS},
    }


def _normalize_sprint_title(s: str) -> str:
    s = unicodedata.normalize("NFKC", s)
    return " ".join(s.split()).lower()


def _sprint_title_from_fv(sprint_fv):
    if not sprint_fv or not isinstance(sprint_fv, dict):
        return None
    t = (sprint_fv.get("title") or sprint_fv.get("name") or "").strip()
    return t or None


def _finalize_slice(bucket):
    planned_items = bucket["planned"]
    in_progress_items = bucket["in_progress"]
    completed_items = bucket["completed"]
    planned_count = len(planned_items)
    completed_count = len(completed_items)
    rate = round(completed_count / planned_count * 100) if planned_count else None
    all_cts = [i["cycle_days"] for i in completed_items if i.get("cycle_days") is not None]
    avg_ct = safe_round(sum(all_cts) / len(all_cts)) if all_cts else None
    priority_breakdown = {}
    for pk in PRIORITY_KEYS:
        pd = bucket["pri_done"][pk]
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
        "rollover": bucket["rollover"],
        "avg_cycle_time_sprint": avg_ct,
        "priority_breakdown": priority_breakdown,
    }


def _github_resolve_sprint_titles(gh_headers, owner_field, owner, project_number, sprint_field_name, current, previous, sprint_auto, now_date):
    ec = current.strip()
    ep = previous.strip()
    if not sprint_auto or (ec and ep):
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
        resp = requests.post("https://api.github.com/graphql", headers=gh_headers, json={"query": query}, timeout=(10, 30))
        resp.raise_for_status()
        gql = resp.json()
    except Exception:
        return ec, ep
    nodes = ((((gql.get("data") or {}).get(owner_field) or {}).get("projectV2") or {}).get("fields") or {}).get("nodes") or []
    iter_field = None
    for node in nodes:
        if node.get("__typename") == "ProjectV2IterationField" and (node.get("name") or "").strip().lower() == sprint_field_name.lower():
            iter_field = node
            break
    if not iter_field:
        return ec, ep
    cfg = iter_field.get("configuration") or {}
    raw = list(cfg.get("iterations") or []) + list(cfg.get("completedIterations") or [])
    rows = []
    seen = set()
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
        dur = int(it.get("duration") or 1)
        end = sd + timedelta(days=max(dur, 1) - 1)
        title = (it.get("title") or "").strip()
        if title:
            rows.append((sd, end, title))
    rows.sort(key=lambda r: r[0])
    if not ec:
        for sd, end, title in rows:
            if sd <= now_date <= end:
                ec = title
                break
    if not ep and ec:
        cur_start = next((sd for sd, _end, title in rows if title == ec), None)
        if cur_start:
            prev_rows = [(end, title) for sd, end, title in rows if end < cur_start]
            if prev_rows:
                ep = sorted(prev_rows, key=lambda x: x[0])[-1][1]
    return ec, ep


def build_product_progress(timebox):
    base_error = {
        "source": "github",
        "planned": {"count": 0, "items": []},
        "in_progress": {"count": 0, "items": []},
        "completed": {"count": 0, "items": []},
        "completion_rate_pct": None,
    }
    token = (os.environ.get("TOKEN_GITHUB_PRODUCT") or os.environ.get("GITHUB_TOKEN") or "").strip()
    if not token:
        return {**base_error, "error": "TOKEN_GITHUB_PRODUCT não configurado", **_empty_pp_extras()}
    owner = (os.environ.get("OWNER_GITHUB_PRODUCT") or "").strip()
    repo = (os.environ.get("REPO_GITHUB_PRODUCT") or "").strip()
    project_number = (os.environ.get("PROJECT_NUMBER_GITHUB_PRODUCT") or "").strip()
    mode_a = bool(project_number and owner)
    label = "" if mode_a else (os.environ.get("LABEL_GITHUB_PRODUCT", "Produto").strip() if "LABEL_GITHUB_PRODUCT" in os.environ else "Produto")
    sprint_field = (os.environ.get("SPRINT_FIELD_NAME_GITHUB_PRODUCT") or "Sprint").strip() or "Sprint"
    sprint_current = (os.environ.get("SPRINT_CURRENT_GITHUB_PRODUCT") or "").strip()
    sprint_previous = (os.environ.get("SPRINT_PREVIOUS_GITHUB_PRODUCT") or "").strip()
    sprint_auto = (os.environ.get("SPRINT_AUTO_GITHUB_PRODUCT") or "1").strip().lower() not in ("0", "false", "no", "off")
    gh_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    owner_field = "organization" if os.environ.get("OWNER_TYPE_GITHUB_PRODUCT", "").lower() == "organization" else "user"
    if mode_a:
        sprint_current, sprint_previous = _github_resolve_sprint_titles(
            gh_headers, owner_field, owner, project_number, sprint_field, sprint_current, sprint_previous, sprint_auto, timebox.now.date()
        )
    use_sprint_mode = bool(sprint_current)
    status_planned = {s.strip().lower() for s in (os.environ.get("STATUS_PLANNED_GITHUB_PRODUCT") or "backlog,a fazer,todo,planejado,planejada").split(",") if s.strip()}
    status_in_progress = {s.strip().lower() for s in (os.environ.get("STATUS_IN_PROGRESS_GITHUB_PRODUCT") or "em andamento,aguardando aprovação,in progress,em progresso,in_progress").split(",") if s.strip()}
    status_done = {s.strip().lower() for s in (os.environ.get("STATUS_DONE_GITHUB_PRODUCT") or "concluído,concluída,done,feito").split(",") if s.strip()}
    bucket_cur = _new_bucket()
    bucket_prev = _new_bucket() if sprint_previous else None
    bucket_legacy = _new_bucket()
    sprint_current_norm = _normalize_sprint_title(sprint_current) if sprint_current else ""
    sprint_previous_norm = _normalize_sprint_title(sprint_previous) if sprint_previous else ""

    def _normalize_item(issue_or_node):
        title = (issue_or_node.get("title") or "?")[:100]
        assignees = issue_or_node.get("assignees") or []
        if isinstance(assignees, dict):
            assignees = [a.get("login", "") for a in assignees.get("nodes", [])]
        elif isinstance(assignees, list):
            assignees = [a.get("login", "") for a in assignees if isinstance(a, dict)]
        return {"title": title, "assignees": [a for a in assignees if a], "cycle_days": None, "due": None, "priority": "sem"}

    if project_number and owner:
        cursor = None
        while True:
            after_arg = f', after: "{cursor}"' if cursor else ""
            query = f"""
            query {{
              {owner_field}(login: "{_gql_escape(owner)}") {{
                projectV2(number: {project_number}) {{
                  items(first: 100{after_arg}) {{
                    pageInfo {{ hasNextPage endCursor }}
                    nodes {{
                      type
                      statusVal: fieldValueByName(name: "Status") {{ ... on ProjectV2ItemFieldSingleSelectValue {{ name }} }}
                      sprintVal: fieldValueByName(name: "{_gql_escape(sprint_field)}") {{
                        ... on ProjectV2ItemFieldIterationValue {{ title }}
                        ... on ProjectV2ItemFieldSingleSelectValue {{ name }}
                      }}
                      content {{
                        ... on Issue {{
                          title state createdAt closedAt
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
            resp = requests.post("https://api.github.com/graphql", headers=gh_headers, json={"query": query}, timeout=(10, 30))
            resp.raise_for_status()
            gql = resp.json()
            items_page = ((((gql.get("data") or {}).get(owner_field) or {}).get("projectV2") or {}).get("items") or {})
            nodes = items_page.get("nodes") or []
            for node in nodes:
                if node.get("type") != "ISSUE":
                    continue
                content = node.get("content") or {}
                if not content:
                    continue
                issue_labels = [l.get("name", "") for l in (content.get("labels") or {}).get("nodes", [])]
                if label and label.lower() not in [n.lower() for n in issue_labels]:
                    continue
                status_lower = ((node.get("statusVal") or {}).get("name") or "").strip().lower()
                created_dt = parse_dt(content.get("createdAt"))
                closed_dt = parse_dt(content.get("closedAt"))
                pri_key = github_priority_from_labels(issue_labels, label)
                item = _normalize_item(content)
                item["due"] = (content.get("dueOn") or {}).get("dueOn") if content.get("dueOn") else None
                item["priority"] = pri_key
                sprint_title_norm = _normalize_sprint_title(_sprint_title_from_fv(node.get("sprintVal")) or "")
                target = bucket_legacy
                if use_sprint_mode and sprint_title_norm == sprint_current_norm:
                    target = bucket_cur
                elif use_sprint_mode and bucket_prev and sprint_title_norm == sprint_previous_norm:
                    target = bucket_prev
                if status_lower in status_done:
                    if use_sprint_mode:
                        cycle_days = safe_round(days_between(created_dt, closed_dt)) if created_dt and closed_dt else None
                        item["cycle_days"] = cycle_days
                        target["completed"].append(item)
                        target["pri_done"][pri_key]["tasks"].append(item["title"])
                        if cycle_days is not None:
                            target["pri_done"][pri_key]["cts"].append(cycle_days)
                    elif closed_dt and timebox.week_start <= closed_dt <= timebox.week_end:
                        cycle_days = safe_round(days_between(created_dt, closed_dt)) if created_dt else None
                        item["cycle_days"] = cycle_days
                        target["completed"].append(item)
                        target["pri_done"][pri_key]["tasks"].append(item["title"])
                        if cycle_days is not None:
                            target["pri_done"][pri_key]["cts"].append(cycle_days)
                elif status_lower in status_in_progress:
                    target["in_progress"].append(item)
                elif status_lower in status_planned:
                    target["planned"].append(item)
            page_info = items_page.get("pageInfo") or {}
            if page_info.get("hasNextPage"):
                cursor = page_info.get("endCursor")
            else:
                break
    elif owner and repo:
        return {**base_error, "error": "Modo repo simplificado não suportado nesta refatoração.", **_empty_pp_extras()}
    else:
        return {**base_error, "error": "Configuração GitHub incompleta.", **_empty_pp_extras()}

    if use_sprint_mode:
        current = _finalize_slice(bucket_cur)
        previous = _finalize_slice(bucket_prev) if bucket_prev else None
        return {
            "source": "github",
            "sprint": {"current": sprint_current, "previous": sprint_previous or None},
            **current,
            "previous_sprint": previous,
        }
    legacy = _finalize_slice(bucket_legacy)
    return {"source": "github", "sprint": {"current": None, "previous": None}, "previous_sprint": None, **legacy}

