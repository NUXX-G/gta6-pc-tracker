"""External trackers, read robustly to cross-check our phases. Their text is never published.

Per tracker we extract: last update time and, per tracked project, the phase/stage that tracker reports.
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from common import Http, HttpError, config, log, parse_time

STAGE_RE = re.compile(r"\b(?:stage|phase|fase|etapa)\s*(\d)\s*(?:of|/|de)\s*7\b", re.IGNORECASE)


def _names(project: dict[str, Any]) -> list[str]:
    return [project["name"], project["repo"], project["repo"].split("/")[1]]


def phases_from_text(text: str, projects: list[dict[str, Any]]) -> dict[str, int]:
    """Look right after each mention of a project for 'stage N of 7'. Keeps the highest per project."""
    out: dict[str, int] = {}
    for p in projects:
        for name in _names(p):
            for m in re.finditer(rf"(?<![\w/]){re.escape(name)}(?![\w])", text, re.IGNORECASE):
                window = text[m.end() : m.end() + 160]
                s = STAGE_RE.search(window)
                if s and 1 <= int(s.group(1)) <= 7:
                    out[p["slug"]] = max(out.get(p["slug"], 0), int(s.group(1)))
    return out


def phases_from_portwatch(data: dict[str, Any], projects: list[dict[str, Any]]) -> dict[str, int]:
    by_repo = {p["repo"].lower(): p["slug"] for p in projects}
    out: dict[str, int] = {}
    for eff in data.get("efforts", []) or []:
        repo = (eff.get("repo") or "").lower().removeprefix("https://github.com/").strip("/")
        slug = by_repo.get(repo)
        gta = eff.get("gta") or {}
        if not slug or not isinstance(gta, dict):
            continue
        level = 1 if gta.get("loads") else 0
        if gta.get("boots"):
            level = 2
        if gta.get("menu"):
            level = 4
        if gta.get("ingame"):
            level = 5
        fps = gta.get("fps") or 0
        if isinstance(fps, (int, float)) and fps >= 25 and gta.get("ingame"):
            level = 7
        if level:
            out[slug] = level
    return out


def collect(http: Http) -> dict[str, Any]:
    projects = config("projects")["projects"]
    trackers = []
    ok = 0
    for t in config("settings")["trackers"]:
        entry: dict[str, Any] = {"name": t["name"], "url": t["url"], "ok": False, "phases": {}, "updated": None}
        try:
            if t.get("json"):
                data = http.get_json(t["json"])
                entry["phases"] = phases_from_portwatch(data, projects)
                entry["updated"] = data.get("updated")
            else:
                html = http.get(t["url"]).text
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                text = soup.get_text(" ")
                entry["phases"] = phases_from_text(text, projects)
                times = [parse_time(x.get("datetime")) for x in soup.find_all("time") if x.get("datetime")]
                times = [x for x in times if x]
                entry["updated"] = max(times).strftime("%Y-%m-%dT%H:%M:%SZ") if times else None
            entry["ok"] = True
            ok += 1
        except (HttpError, ValueError) as exc:
            log.warning("tracker %s: %s", t["name"], exc)
            entry["error"] = str(exc)[:200]
        trackers.append(entry)
    if ok == 0:
        raise HttpError(0, "trackers", "(all trackers failed)")
    return {"trackers": trackers, "count": ok}
