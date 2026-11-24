"""GitHub collector: repo health, activity, PRs, problem issues, releases, README (hash + status table) and forks.

Third-party free text (commit messages, issue titles, README) is used only for classification in memory.
What is stored: numbers, dates, canonical labels, hashes and github.com URLs.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from urllib.parse import quote

from common import DATA_DIR, Http, HttpError, config, iso, load_json, log, now_utc, parse_time, save_json, sha
from readme_parser import extract_status_table, normalise_table

API = "https://api.github.com"
CACHE = DATA_DIR / "cache" / "commit_areas.json"
MAX_DETAILS_PER_PROJECT = 25


def _count_from_link(resp_headers: dict[str, str], fallback: int) -> int:
    link = resp_headers.get("link", "")
    m = re.search(r"[?&]page=(\d+)>;\s*rel=\"last\"", link)
    return int(m.group(1)) if m else fallback


def classify_commit(message: str, files: list[str]) -> list[str]:
    areas = []
    msg = (message or "").lower()
    for area, rule in config("scoring")["commit_areas"].items():
        if any(re.search(rule["files"], f.lower()) for f in files) or re.search(rule["message"], msg):
            areas.append(area)
    return areas or ["other"]


def _problem_keywords(text: str) -> list[str]:
    low = text.lower()
    return [k for k in config("settings")["github"]["problem_keywords"] if re.search(rf"\b{re.escape(k)}\b", low)]


def fetch_readme(http: Http, repo: str) -> str:
    return http.get(f"{API}/repos/{repo}/readme", headers={"Accept": "application/vnd.github.raw"}, github=True).text


def fetch_release_assets(http: Http, repo: str, limit: int = 5) -> list[dict[str, Any]]:
    out = []
    for rel in http.get_json(f"{API}/repos/{repo}/releases?per_page={limit}", github=True):
        out.append(
            {
                "url": rel.get("html_url"),
                "date": rel.get("published_at") or rel.get("created_at"),
                "assets": [a.get("name", "") for a in rel.get("assets", [])],
            }
        )
    return out


def collect_project(http: Http, project: dict[str, Any], cache: dict[str, list[str]]) -> dict[str, Any]:
    repo = project["repo"]
    now = now_utc()
    out: dict[str, Any] = {"slug": project["slug"], "repo": repo, "url": f"https://github.com/{repo}"}
    try:
        info = http.get_json(f"{API}/repos/{repo}", github=True)
    except HttpError as exc:
        if exc.status in (404, 451, 410):
            out.update({"http_status": exc.status, "exists": False})
            return out
        raise
    out.update(
        {
            "exists": True,
            "http_status": 200,
            "full_name": info.get("full_name", repo),
            "stars": info.get("stargazers_count", 0),
            "forks": info.get("forks_count", 0),
            "open_issues": info.get("open_issues_count", 0),
            "language": info.get("language") or project.get("language"),
            "license": (info.get("license") or {}).get("spdx_id"),
            "pushed_at": info.get("pushed_at"),
            "created_at": info.get("created_at"),
            "is_fork": bool(info.get("fork")),
            "default_branch": info.get("default_branch", "main"),
            "archived": bool(info.get("archived")),
        }
    )
    # Contributors (count through pagination header)
    try:
        r = http.get(f"{API}/repos/{repo}/contributors?per_page=1&anon=1", github=True)
        out["contributors"] = _count_from_link(r.headers, len(r.json()) if r.text.strip() else 0)
    except HttpError as exc:
        log.warning("contributors %s: %s", repo, exc)
        out["contributors"] = None

    # Exact commit counts for 24 h and 7 days (per_page=1 + the "last" page of the Link header).
    since = iso(now - timedelta(days=7))
    counts = {}
    for key, delta in (("24h", timedelta(hours=24)), ("7d", timedelta(days=7))):
        r = http.get(f"{API}/repos/{repo}/commits?since={iso(now - delta)}&per_page=1", github=True)
        counts[key] = _count_from_link(r.headers, len(r.json()) if r.text.strip() else 0)
    # The most recent commits (up to 200) are classified by area.
    commits: list[dict[str, Any]] = []
    for page in (1, 2):
        batch = http.get_json(f"{API}/repos/{repo}/commits?since={since}&per_page=100&page={page}", github=True)
        commits.extend(batch)
        if len(batch) < 100:
            break
    if not commits:
        try:
            last = http.get_json(f"{API}/repos/{repo}/commits?per_page=1", github=True)
            out["last_commit"] = (last[0]["commit"]["committer"] or {}).get("date") if last else None
        except HttpError:
            out["last_commit"] = None
    else:
        out["last_commit"] = (commits[0]["commit"].get("committer") or {}).get("date")

    details_left = MAX_DETAILS_PER_PROJECT
    recent = []
    for c in commits:
        sha_ = c["sha"]
        date = (c["commit"].get("committer") or {}).get("date") or (c["commit"].get("author") or {}).get("date")
        message = c["commit"].get("message", "")
        areas = cache.get(sha_)
        if areas is None:
            files: list[str] = []
            if details_left > 0:
                details_left -= 1
                try:
                    detail = http.get_json(f"{API}/repos/{repo}/commits/{sha_}", github=True)
                    files = [f.get("filename", "") for f in detail.get("files", [])[:300]]
                except HttpError as exc:
                    log.warning("commit detail %s@%s: %s", repo, sha_[:7], exc)
            areas = classify_commit(message, files)
            if files or details_left <= 0:
                cache[sha_] = areas
        recent.append({"sha": sha_[:10], "url": c.get("html_url"), "date": date, "areas": areas})
    out["commits"] = recent
    out["commits_24h"] = max(
        counts["24h"], sum(1 for c in recent if now - (parse_time(c["date"]) or now) <= timedelta(hours=24))
    )
    out["commits_7d"] = max(counts["7d"], len(recent))
    out["commits_sampled"] = len(recent)

    # Pull requests
    try:
        r = http.get(f"{API}/repos/{repo}/pulls?state=open&per_page=1", github=True)
        out["prs_open"] = _count_from_link(r.headers, len(r.json()))
        closed = http.get_json(
            f"{API}/repos/{repo}/pulls?state=closed&sort=updated&direction=desc&per_page=50", github=True
        )
        out["prs_merged_24h"] = sum(
            1 for p in closed if p.get("merged_at") and now - parse_time(p["merged_at"]) <= timedelta(hours=24)
        )
    except HttpError as exc:
        log.warning("pulls %s: %s", repo, exc)
        out["prs_open"], out["prs_merged_24h"] = None, None

    # Open issues mentioning problems (titles/bodies are not stored)
    problems = []
    gpu_issue_texts = []
    try:
        issues = http.get_json(f"{API}/repos/{repo}/issues?state=open&sort=updated&per_page=100", github=True)
        for it in issues:
            if "pull_request" in it:
                continue
            text = f"{it.get('title', '')}\n{(it.get('body') or '')[:3000]}"
            kws = _problem_keywords(text)
            if kws:
                problems.append({"number": it["number"], "url": it.get("html_url"), "keywords": kws})
            gpu_issue_texts.append({"url": it.get("html_url"), "text": text, "problem": bool(kws)})
    except HttpError as exc:
        log.warning("issues %s: %s", repo, exc)
    out["problem_issues"] = problems
    out["_issue_texts"] = gpu_issue_texts

    # Releases
    try:
        rels = fetch_release_assets(http, repo, 10)
        from clone_detector import BINARY_EXT

        out["releases"] = [
            {"url": r["url"], "date": r["date"], "has_binaries": any(BINARY_EXT.search(a) for a in r["assets"])}
            for r in rels
        ]
    except HttpError as exc:
        log.warning("releases %s: %s", repo, exc)
        out["releases"] = []

    # README
    try:
        readme = fetch_readme(http, repo)
        out["readme_hash"] = sha(readme)
        out["status_rows"] = normalise_table(extract_status_table(readme))
        out["_readme"] = readme
    except HttpError as exc:
        log.warning("readme %s: %s", repo, exc)
        out["readme_hash"], out["status_rows"], out["_readme"] = None, [], ""

    # Popular forks (for the clone detector)
    forks = []
    n = config("settings")["github"]["forks_to_check"]
    try:
        for f in http.get_json(f"{API}/repos/{repo}/forks?sort=stargazers&per_page={n}", github=True)[:n]:
            fn = f["full_name"]
            entry = {"full_name": fn, "stars": f.get("stargazers_count", 0), "fork": True, "parent": repo}
            try:
                entry["readme"] = fetch_readme(http, fn)
            except HttpError:
                entry["readme"] = ""
            try:
                entry["release_assets"] = [a for r in fetch_release_assets(http, fn, 3) for a in r["assets"]]
            except HttpError:
                entry["release_assets"] = []
            forks.append(entry)
    except HttpError as exc:
        log.warning("forks %s: %s", repo, exc)
    out["_forks"] = forks
    return out


def collect(http: Http) -> dict[str, Any]:
    projects = config("projects")["projects"]
    cache: dict[str, list[str]] = load_json(CACHE, {}) or {}
    results: dict[str, Any] = {}
    errors = 0
    for p in projects:
        try:
            results[p["slug"]] = collect_project(http, p, cache)
        except HttpError as exc:
            errors += 1
            log.error("github %s failed: %s", p["repo"], exc)
            results[p["slug"]] = {"slug": p["slug"], "repo": p["repo"], "error": str(exc)}
    # keep the cache bounded
    if len(cache) > 6000:
        cache = dict(list(cache.items())[-4000:])
    if not http.offline:
        save_json(CACHE, cache)
    if errors == len(projects):
        raise HttpError(0, API, "all projects failed")
    return {"projects": results, "count": len(results) - errors}


def search_url(q: str, days: int) -> str:
    since = (now_utc() - timedelta(days=days)).strftime("%Y-%m-%d")
    return f"{API}/search/repositories?q={quote(q)}+pushed:>{since}&sort=updated&order=desc&per_page=20"
