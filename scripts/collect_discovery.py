"""Discovery: GitHub search for new or updated repos. Candidates never enter the ranking on their own."""

from __future__ import annotations

from typing import Any

from clone_detector import analyse
from collect_github import API, _count_from_link, fetch_readme, fetch_release_assets, search_url
from common import DATA_DIR, Http, HttpError, config, load_json, log, now_utc, parse_time
from legal_filter import text_violation
from spoiler_filter import find_spoiler


def collect(http: Http, tracked_readmes: dict[str, str]) -> dict[str, Any]:
    cfg = config("settings")["discovery"]
    tracked = {p["repo"].lower() for p in config("projects")["projects"] if p.get("repo")}
    previous = {
        c["full_name"].lower(): c for c in (load_json(DATA_DIR / "candidates.json", {}) or {}).get("candidates", [])
    }
    found: dict[str, dict[str, Any]] = {}
    failures = 0
    for term in cfg["terms"]:
        try:
            res = http.get_json(search_url(term, cfg["pushed_within_days"]), github=True)
        except HttpError as exc:
            failures += 1
            log.warning("discovery '%s': %s", term, exc)
            continue
        for repo in res.get("items", []):
            name = repo["full_name"]
            if name.lower() in tracked or repo.get("fork"):
                continue
            found.setdefault(name.lower(), repo)
    if failures == len(cfg["terms"]):
        raise HttpError(0, API, "all discovery searches failed")

    ranked = sorted(found.values(), key=lambda r: r.get("stargazers_count", 0), reverse=True)[: cfg["max_candidates"]]
    now = now_utc()
    candidates, dropped = [], 0
    for i, repo in enumerate(ranked):
        name = repo["full_name"]
        if text_violation(name) or find_spoiler(name.replace("-", " ").replace("_", " ")):
            dropped += 1
            continue
        created = parse_time(repo.get("created_at"))
        cand: dict[str, Any] = {
            "full_name": name,
            "url": repo.get("html_url"),
            "stars": repo.get("stargazers_count", 0),
            "created_at": repo.get("created_at"),
            "pushed_at": repo.get("pushed_at"),
            "age_days": (now - created).days if created else None,
            "language": repo.get("language"),
            "size_kb": repo.get("size", 0),
            "has_code": bool(repo.get("language")) and repo.get("size", 0) > 30,
            "commits": None,
            "first_seen": (previous.get(name.lower()) or {}).get("first_seen") or now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "flagged": False,
            "inspected": False,
            "reasons": [],
        }
        if i < cfg["inspect_top"]:
            try:
                r = http.get(f"{API}/repos/{name}/commits?per_page=1", github=True)
                cand["commits"] = _count_from_link(r.headers, len(r.json()))
            except HttpError:
                pass
            try:
                readme = fetch_readme(http, name)
            except HttpError:
                readme = ""
            try:
                assets = [a for rel in fetch_release_assets(http, name, 3) for a in rel["assets"]]
            except HttpError:
                assets = []
            verdict = analyse(
                {
                    "full_name": name,
                    "readme": readme,
                    "fork": False,
                    "release_assets": assets,
                    "has_code": cand["has_code"],
                },
                tracked_readmes,
            )
            cand["flagged"], cand["reasons"] = verdict["flagged"], verdict["reasons"]
            cand["similarity"] = verdict["similarity"]
            cand["inspected"] = True
        candidates.append(cand)
    return {"candidates": candidates, "dropped_by_filters": dropped, "count": len(candidates)}
