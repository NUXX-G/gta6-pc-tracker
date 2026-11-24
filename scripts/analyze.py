"""Analysis: phases and %, commit areas, good/bad signals, GPU matrix, estimates, diffs, events and history."""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from common import DATA_DIR, config, hours_since, iso, load_json, now_utc, parse_time, save_json
from readme_parser import compare_rows

HISTORY_DIR = DATA_DIR / "history"
EVENTS_FILE = DATA_DIR / "events.json"

# ---------------------------------------------------------------- phases and score


def _negated(text: str, start: int) -> bool:
    cfg = config("credibility")
    window = text[max(0, start - cfg["negation_window"]) : start].lower()
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", window) for n in cfg["negations"])


def phase_from_text(text: str) -> int:
    """Highest phase whose keyword rule matches (ignoring negated matches)."""
    low = (text or "").lower()
    rules = config("scoring")["phase_rules"]
    for level in sorted(rules, key=int, reverse=True):
        for pattern in rules[level]:
            for m in re.finditer(pattern, low):
                if not _negated(low, m.start()):
                    return int(level)
    return 0


def phase_from_rows(rows: list[dict[str, Any]]) -> tuple[int, int]:
    """(highest fully reached phase, highest partially reached phase) from normalised README rows."""
    mapping = config("scoring")["stage_phase"]
    full, partial = 0, 0
    for r in rows:
        level = mapping.get(r["stage"])
        if not level:
            continue
        if r["state"] == "ok":
            full = max(full, level)
        elif r["state"] == "partial":
            partial = max(partial, level)
    return full, partial


def auto_phase(readme: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    full, partial = phase_from_rows(rows)
    text_level = phase_from_text(readme)
    level = max(full, text_level)
    if partial > level:
        return {"level": partial, "partial": True}
    return {"level": level, "partial": False}


def score_for(level: int, partial: bool, stable: bool = False) -> float:
    phases = config("scoring")["phases"]
    if level <= 0:
        return 0.0
    if level == 7 and stable:
        return float(config("scoring")["stable_score"])
    cur = phases[level]["score"]
    if not partial:
        return float(cur)
    prev = phases[level - 1]["score"] if level > 1 else 0
    return (prev + cur) / 2


def phase_value(p: dict[str, Any]) -> float:
    """Comparable value: partial phase N sits between N-1 and N."""
    return p["level"] - (0.5 if p.get("partial") else 0)


def reached_level(p: dict[str, Any]) -> int:
    return p["level"] - (1 if p.get("partial") else 0)


# ---------------------------------------------------------------- GPU


def gpu_matrix(readme: str, issue_texts: list[dict[str, Any]], repo_url: str) -> dict[str, Any]:
    cfg = config("scoring")["gpu"]
    vendors = {v: re.compile(rx, re.IGNORECASE) for v, rx in cfg["vendors"].items()}
    model_re = re.compile(cfg["model"], re.IGNORECASE)
    ok_re = re.compile(cfg["ok_context"], re.IGNORECASE)
    problem_re = re.compile(r"\b(" + "|".join(re.escape(k) for k in cfg["problem_keywords"]) + r")\b", re.IGNORECASE)
    out: dict[str, Any] = {v: {"status": "untested", "models": [], "evidence": None} for v in vendors}

    def vendor_of_model(model: str) -> str | None:
        for v, rx in vendors.items():
            if rx.search(model):
                return v
        return None

    for line in re.split(r"[\n.;]", readme or ""):
        if not any(rx.search(line) for rx in vendors.values()):
            continue
        has_problem = bool(problem_re.search(line))
        for v, rx in vendors.items():
            if not rx.search(line):
                continue
            models = sorted(
                {
                    re.sub(r"\s+", " ", m).upper().replace("RTX", "RTX ").replace("  ", " ").strip()
                    for m in model_re.findall(line)
                    if vendor_of_model(m) == v
                }
            )
            if ok_re.search(line) and not has_problem:
                if out[v]["status"] == "untested":
                    out[v]["status"] = "ok"
                    out[v]["evidence"] = repo_url
                out[v]["models"] = sorted(set(out[v]["models"]) | set(models))[:8]
            elif has_problem:
                out[v]["known_issue"] = True
                out[v]["evidence_issue"] = repo_url
    for it in issue_texts:
        if not it.get("problem"):
            continue
        for v, rx in vendors.items():
            if rx.search(it["text"]):
                out[v]["known_issue"] = True
                out[v]["evidence_issue"] = it.get("url") or repo_url
                if out[v]["status"] == "untested":
                    out[v]["status"] = "issues"
    for v in out:
        if out[v].get("known_issue") and out[v]["status"] == "ok":
            out[v]["status"] = "ok_with_issues"
        out[v]["models"] = [re.sub(r"\s+", " ", m) for m in out[v]["models"]]
    return out


# ---------------------------------------------------------------- estimates


def estimate(level: int, partial: bool, commits_7d: int, status: str) -> list[dict[str, Any]]:
    cfg = config("scoring")["estimates"]
    if status in ("missing", "dmca", "stale"):
        return [{"to": lvl, "stale": True} for lvl in range(level + (0 if partial else 1), 8)]
    activity = "normal"
    if commits_7d >= cfg["high_commits_7d"]:
        activity = "high"
    elif commits_7d <= cfg["low_commits_7d"]:
        activity = "low"
    factor = cfg["activity_factor"][activity]
    out = []
    lo_sum = hi_sum = 0.0
    start = level if partial else level + 1
    for target in range(max(start, 2), 8):
        lo, hi = cfg["days_per_phase"][target]
        if target == level and partial:
            lo, hi = lo / 2, hi / 2
        lo_sum += lo * factor
        hi_sum += hi * factor
        out.append(
            {
                "to": target,
                "from": reached_level({"level": level, "partial": partial}),
                "steps": target - reached_level({"level": level, "partial": partial}),
                "min_days": max(1, round(lo_sum)),
                "max_days": max(2, round(hi_sum)),
                "activity": activity,
                "factor": factor,
                "per": f"{cfg['days_per_phase'][target][0]}–{cfg['days_per_phase'][target][1]}",
            }
        )
    return out


# ---------------------------------------------------------------- per project


def _areas(commits: list[dict[str, Any]], hours: int, now) -> dict[str, int]:
    counts: dict[str, int] = {}
    for c in commits:
        dt = parse_time(c.get("date"))
        if dt and now - dt <= timedelta(hours=hours):
            for a in c.get("areas", ["other"]):
                counts[a] = counts.get(a, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def analyse_project(
    project: dict[str, Any],
    gh: dict[str, Any] | None,
    prev: dict[str, Any] | None,
    prev_day: dict[str, Any] | None,
    trackers: list[dict[str, Any]],
    dmca_hits: dict[str, Any],
) -> dict[str, Any]:
    now = now_utc()
    gh = gh or {}
    prev = prev or {}
    th = config("scoring")["signals"]
    stale_h = config("settings")["github"]["stale_hours"]
    available = "error" not in gh and bool(gh)
    # If the GitHub call failed this hour, carry the previous numbers so the page stays coherent.
    base = gh if available else prev.get("gh", {})

    rows = base.get("status_rows") or []
    readme = gh.get("_readme", "")
    auto = auto_phase(readme, rows) if available and readme else prev.get("auto_phase", {"level": 0, "partial": False})
    manual = project.get("phase")
    if manual:
        eff = {"level": int(manual["level"]), "partial": bool(manual.get("partial")), "source": "manual"}
    else:
        eff = {"level": auto["level"], "partial": auto["partial"], "source": "auto"}
    score = score_for(eff["level"], eff["partial"], bool((manual or {}).get("stable")))

    exists = base.get("exists", True)
    dmca = dmca_hits.get(project["slug"])
    if dmca:
        status = "dmca"
    elif base and not exists:
        status = "missing"
    elif not base:
        status = "unknown"
    else:
        h = hours_since(base.get("last_commit"), now)
        status = "stale" if (h is None or h > stale_h) else "active"

    commits = base.get("commits", [])
    a24, a7 = _areas(commits, 24, now), _areas(commits, 24 * 7, now)
    n24, n7 = base.get("commits_24h", 0) or 0, base.get("commits_7d", 0) or 0

    # Tracker corroboration
    tracker_phases = [
        {"tracker": t["name"], "url": t["url"], "phase": t["phases"][project["slug"]]}
        for t in trackers
        if t.get("ok") and project["slug"] in t.get("phases", {})
    ]
    best_tracker = max(tracker_phases, key=lambda x: x["phase"], default=None)

    # Signals
    good: list[dict[str, Any]] = []
    bad: list[dict[str, Any]] = []
    repo_url = f"https://github.com/{project['repo']}"
    commits_url = f"{repo_url}/commits"
    avg = (n7 - n24) / 6 if n7 > n24 else 0.0
    if n7 >= th["min_commits_for_ratio"]:
        if n24 >= th["min_commits_for_ratio"] and n24 > max(avg, 0.5) * th["activity_ratio_good"]:
            good.append({"code": "activity_up", "n24": n24, "avg": round(avg, 1), "evidence": commits_url})
        elif avg >= th["min_commits_for_ratio"] and n24 < avg * th["activity_ratio_bad"]:
            bad.append({"code": "activity_down", "n24": n24, "avg": round(avg, 1), "evidence": commits_url})
    h_last = hours_since(base.get("last_commit"), now)
    if exists and h_last is not None and h_last > th["stale_hours_bad"]:
        bad.append({"code": "stale", "h": int(h_last), "evidence": commits_url})
    prev_rows = (prev.get("gh") or {}).get("status_rows") or []
    if available and prev_rows and rows:
        up, down = compare_rows(prev_rows, rows)
        if up:
            good.append({"code": "rows_up", "rows": up, "evidence": f"{repo_url}#readme"})
        if down:
            bad.append({"code": "rows_down", "rows": down, "evidence": f"{repo_url}#readme"})
    ref = prev_day or {}
    if ref.get("stars") is not None and base.get("stars") is not None:
        ds = base["stars"] - ref["stars"]
        if ds >= th["stars_growth_good_24h"]:
            good.append({"code": "stars_up", "n": ds, "evidence": f"{repo_url}/stargazers"})
    if ref.get("contributors") is not None and base.get("contributors") is not None:
        dc = base["contributors"] - ref["contributors"]
        if dc >= th["contributors_growth_good"]:
            good.append({"code": "contributors_up", "n": dc, "evidence": f"{repo_url}/graphs/contributors"})
    problems = base.get("problem_issues", [])
    if len(problems) >= th["crash_issues_bad"]:
        bad.append({"code": "crash_issues", "n": len(problems), "evidence": f"{repo_url}/issues"})
    if best_tracker and best_tracker["phase"] >= eff["level"] - (1 if eff["partial"] else 0):
        good.append(
            {
                "code": "tracker_confirms",
                "tracker": best_tracker["tracker"],
                "phase": best_tracker["phase"],
                "evidence": best_tracker["url"],
            }
        )
    if status == "missing":
        bad.append({"code": "missing", "code_http": base.get("http_status"), "evidence": repo_url})
    if status == "dmca":
        bad.append({"code": "dmca", "evidence": dmca["url"]})
    bin_rel = [r for r in base.get("releases", []) if r.get("has_binaries")]
    if bin_rel:
        bad.append({"code": "binaries", "evidence": f"{repo_url}/releases"})

    # Pending claim: README suggests more than our confirmed level, and no tracker backs it.
    claims = []
    if phase_value(auto) > phase_value(eff):
        backed = best_tracker and best_tracker["phase"] >= reached_level(auto)
        if not backed:
            claims.append({"auto": auto, "phase": eff, "evidence": f"{repo_url}#readme"})

    gpus = gpu_matrix(readme, gh.get("_issue_texts", []), repo_url) if available else prev.get("gpu", {})

    return {
        "slug": project["slug"],
        "name": project["name"],
        "repo": project["repo"],
        "url": repo_url,
        "status": status,
        "available": available,
        "gh": {k: v for k, v in base.items() if not k.startswith("_")},
        "auto_phase": auto,
        "phase": eff,
        "score": round(score, 1),
        "areas_24h": a24,
        "areas_7d": a7,
        "commits_24h": n24,
        "commits_7d": n7,
        "signals_good": good,
        "signals_bad": bad,
        "trackers": tracker_phases,
        "claims": claims,
        "gpu": gpus,
        "estimates": estimate(eff["level"], eff["partial"], n7, status),
        "dmca": dmca,
    }


# ---------------------------------------------------------------- events / diffs


def build_events(projects: list[dict[str, Any]], prev: dict[str, Any], now) -> list[dict[str, Any]]:
    """New events since the previous snapshot (commits, README, releases, phase, status)."""
    prev_projects = {p["slug"]: p for p in prev.get("projects", [])}
    prev_time = parse_time(prev.get("generated")) or (now - timedelta(hours=24))
    events = []
    for p in projects:
        if not p["available"]:
            continue
        old = prev_projects.get(p["slug"], {})
        gh = p["gh"]
        new_commits = [c for c in gh.get("commits", []) if (parse_time(c["date"]) or now) > prev_time]
        if new_commits:
            areas: dict[str, int] = {}
            for c in new_commits:
                for a in c["areas"]:
                    areas[a] = areas.get(a, 0) + 1
            events.append(
                {
                    "kind": "commits",
                    "n": len(new_commits),
                    "areas": sorted(areas, key=lambda a: -areas[a])[:3],
                    "created": max(c["date"] for c in new_commits),
                    "url": f"{p['url']}/commits",
                }
            )
        if old and old.get("gh", {}).get("readme_hash") and gh.get("readme_hash") != old["gh"]["readme_hash"]:
            events.append({"kind": "readme", "created": iso(now), "url": f"{p['url']}#readme"})
        old_rel = {r["url"] for r in old.get("gh", {}).get("releases", [])}
        for r in gh.get("releases", []):
            if old and r["url"] not in old_rel:
                events.append(
                    {
                        "kind": "release",
                        "created": r.get("date") or iso(now),
                        "url": r["url"],
                        "binaries": r.get("has_binaries"),
                    }
                )
        if old and phase_value(old.get("phase", p["phase"])) != phase_value(p["phase"]):
            up = phase_value(p["phase"]) > phase_value(old["phase"])
            events.append(
                {
                    "kind": "phase_up" if up else "phase_down",
                    "from": old["phase"],
                    "to": p["phase"],
                    "created": iso(now),
                    "url": p["url"],
                }
            )
        if old and old.get("status") != p["status"] and p["status"] in ("missing", "dmca"):
            events.append({"kind": "status_change", "status": p["status"], "created": iso(now), "url": p["url"]})
        for e in events:
            e.setdefault("project", p["slug"])
    for e in events:
        e["source"] = "github"
        e["credibility"] = "confirmed"
        e["id"] = f"{e['project']}-{e['kind']}-{e['created']}"
    return events


def merge_events(new: list[dict[str, Any]], now) -> list[dict[str, Any]]:
    old = load_json(EVENTS_FILE, []) or []
    by_id = {e["id"]: e for e in old}
    for e in new:
        by_id[e["id"]] = e
    keep = [e for e in by_id.values() if (now - (parse_time(e["created"]) or now)) <= timedelta(days=7)]
    keep.sort(key=lambda e: e["created"], reverse=True)
    return keep


# ---------------------------------------------------------------- history


def update_history(projects: list[dict[str, Any]], now, offline: bool) -> dict[str, Any]:
    hourly = load_json(HISTORY_DIR / "hourly.json", []) or []
    daily = load_json(HISTORY_DIR / "daily.json", []) or []
    changes = load_json(HISTORY_DIR / "phase_changes.json", []) or []
    snap = {
        "t": iso(now),
        "p": {
            p["slug"]: {
                "phase": p["phase"]["level"],
                "partial": p["phase"]["partial"],
                "score": p["score"],
                "stars": p["gh"].get("stars"),
                "forks": p["gh"].get("forks"),
                "contributors": p["gh"].get("contributors"),
                "commits_24h": p["commits_24h"],
                "status": p["status"],
            }
            for p in projects
        },
    }
    last_known: dict[str, Any] = {}
    for c in changes:
        last_known[c["project"]] = c["to"]
    for p in projects:
        cur = {"level": p["phase"]["level"], "partial": p["phase"]["partial"]}
        before = last_known.get(p["slug"])
        if before is None:
            changes.append({"t": iso(now), "project": p["slug"], "from": None, "to": cur})
        elif before != cur:
            changes.append({"t": iso(now), "project": p["slug"], "from": before, "to": cur})
    hourly.append(snap)
    cutoff = now - timedelta(days=config("settings")["history"]["hourly_days"])
    old = [s for s in hourly if parse_time(s["t"]) < cutoff]
    hourly = [s for s in hourly if parse_time(s["t"]) >= cutoff]
    days = {d["t"][:10]: d for d in daily}
    for s in old:  # compact: keep the last snapshot of each day
        days[s["t"][:10]] = s
    daily = [days[k] for k in sorted(days)]
    if not offline:
        save_json(HISTORY_DIR / "hourly.json", hourly)
        save_json(HISTORY_DIR / "daily.json", daily)
        save_json(HISTORY_DIR / "phase_changes.json", changes)
    return {"hourly": hourly, "daily": daily, "changes": changes}


def snapshot_ago(history: dict[str, Any], hours: int, now) -> dict[str, Any]:
    """Project values from the snapshot closest to `hours` ago (for 24 h deltas)."""
    target = now - timedelta(hours=hours)
    best, best_diff = None, None
    for s in history.get("hourly", []) + history.get("daily", []):
        t = parse_time(s["t"])
        if not t:
            continue
        diff = abs((t - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best, best_diff = s, diff
    if best is None or best_diff > 12 * 3600:
        return {}
    return best["p"]
