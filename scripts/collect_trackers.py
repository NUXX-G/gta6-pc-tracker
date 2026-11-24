"""External trackers, read robustly to cross-check our phases and to find projects we do not track yet.

Per tracker we extract: last update time, the phase/stage it reports for each tracked project, and the list of
projects it follows (name, repo, stage). Their prose is never published: only names, repo links and stage numbers.
PortWatch also exposes images attached to its own news posts; those become media entries (origin "tracker").
"""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup

from common import Http, HttpError, config, domain_allowed, log, parse_time, sha

STAGE_RE = re.compile(r"\b(?:stage|phase|fase|etapa)\s*(\d)\s*(?:of|/|de)\s*7\b", re.IGNORECASE)
GH_RE = re.compile(r"https?://github\.com/([\w.-]+/[\w.-]+)", re.IGNORECASE)
CHALLENGE_RE = re.compile(r"just a moment|cf-challenge|challenges\.cloudflare\.com|enable javascript and cookies", re.I)


def _names(project: dict[str, Any]) -> list[str]:
    if not project.get("repo"):
        return [project["name"]]
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


def _pw_level(gta: dict[str, Any]) -> int:
    """PortWatch flags (0..1 each) mapped onto our 7 phases."""
    level = 1 if gta.get("loads") else 0
    if gta.get("boots"):
        level = 2
    if gta.get("menu"):
        level = 4
    if (gta.get("ingame") or 0) >= 0.5:
        level = 5
    fps = gta.get("fps") or 0
    if isinstance(fps, (int, float)) and fps >= 25 and gta.get("ingame"):
        level = 7
    return level


def phases_from_portwatch(data: dict[str, Any], projects: list[dict[str, Any]]) -> dict[str, int]:
    by_repo = {p["repo"].lower(): p["slug"] for p in projects if p.get("repo")}
    by_name = {p["name"].lower(): p["slug"] for p in projects}
    out: dict[str, int] = {}
    for eff in data.get("efforts", []) or []:
        repo = (eff.get("repo") or "").lower().removeprefix("https://github.com/").strip("/")
        slug = by_repo.get(repo) or (by_name.get((eff.get("name") or "").lower()) if not repo else None)
        gta = eff.get("gta") or {}
        if not slug or not isinstance(gta, dict):
            continue
        level = _pw_level(gta)
        if level:
            out[slug] = level
    return out


def projects_from_portwatch(data: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for eff in data.get("efforts", []) or []:
        repo = (eff.get("repo") or "").removeprefix("https://github.com/").strip("/") or None
        gta = eff.get("gta") if isinstance(eff.get("gta"), dict) else {}
        out.append(
            {
                "name": str(eff.get("name") or eff.get("id") or "")[:60],
                "repo": repo,
                "stage": _pw_level(gta),
                "watch": eff.get("tier") == "watchlist",
            }
        )
    return [p for p in out if p["name"]]


def media_from_portwatch(data: dict[str, Any], allowed: list[str]) -> list[dict[str, Any]]:
    """Images PortWatch attached to its own news posts. Only images hosted on allowlisted hosts are kept."""
    out = []
    for n in data.get("news", []) or []:
        img = n.get("image") or {}
        src = img.get("url") or ""
        if not src or not domain_allowed(src, allowed):
            continue
        nid = str(n.get("id") or "")
        page = (
            f"https://portwatch-vi.xyz/news/{nid}.html" if re.fullmatch(r"[\w-]+", nid) else "https://portwatch-vi.xyz/"
        )
        out.append(
            {
                "id": sha("portwatch" + src),
                "kind": "image",
                "url": page,
                "thumb": src,
                "source": "tracker",
                "source_label": "PortWatch VI",
                "origin": "tracker",
                "created": n.get("date"),
                "_texts": [n.get("title") or "", n.get("dek") or "", img.get("credit") or "", nid],
            }
        )
    return out


def projects_from_race(soup: BeautifulSoup) -> list[dict[str, Any]]:
    """GTA VI PC Race standings: <li class="board__row"> with an <h3>, an optional repo link and 'Stage N of 7'."""
    out = []
    for row in soup.select("li.board__row"):
        h = row.find("h3")
        if not h:
            continue
        a = h.find("a", href=True)
        m = GH_RE.search(a["href"]) if a else None
        label = row.get_text(" ")
        s = STAGE_RE.search(label)
        out.append(
            {
                "name": h.get_text(" ", strip=True)[:60],
                "repo": m.group(1).strip("/") if m else None,
                "stage": int(s.group(1)) if s else 0,
                "watch": False,
            }
        )
    for li in soup.select("ul.pairs li"):
        a = li.find("a", href=True)
        m = GH_RE.search(a["href"]) if a else None
        if a and m:
            out.append(
                {"name": a.get_text(" ", strip=True)[:60], "repo": m.group(1).strip("/"), "stage": 0, "watch": True}
            )
    return out


def merge_external(trackers: list[dict[str, Any]], projects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per repo (or name for private builds), with the stage each tracker reports and our own slug."""
    by_repo = {p["repo"].lower(): p["slug"] for p in projects if p.get("repo")}
    by_name = {p["name"].lower(): p["slug"] for p in projects}
    hidden = {str(n).lower() for n in (config("blocklist") or {}).get("names") or []}
    rows: dict[str, dict[str, Any]] = {}
    for t in trackers:
        for ep in t.get("projects", []):
            if ep["name"].lower() in hidden:
                continue  # config/blocklist.yml → names: never shown
            key = (ep["repo"] or ep["name"]).lower()
            row = rows.setdefault(
                key,
                {
                    "name": ep["name"],
                    "repo": ep["repo"],
                    "stages": {},
                    "watch": True,
                    "slug": by_repo.get((ep["repo"] or "").lower()) or by_name.get(ep["name"].lower()),
                },
            )
            row["stages"][t["name"]] = ep["stage"]
            row["watch"] = row["watch"] and ep["watch"]
    # A tracker that lists a project without its repo (e.g. after it was taken down) joins the row that has it.
    for key in [k for k, r in rows.items() if not r["repo"]]:
        twin = next((r for r in rows.values() if r["repo"] and r["name"].lower() == rows[key]["name"].lower()), None)
        if twin:
            for t, st in rows.pop(key)["stages"].items():
                twin["stages"][t] = max(st, twin["stages"].get(t, 0))
    out = list(rows.values())
    out.sort(key=lambda r: (-max(r["stages"].values() or [0]), r["slug"] is None, r["name"].lower()))
    return out


def collect(http: Http) -> dict[str, Any]:
    projects = config("projects")["projects"]
    allowed = list(config("allowlist")["domains"])
    trackers = []
    media: list[dict[str, Any]] = []
    exe: dict[str, Any] | None = None
    scam: dict[str, int] | None = None
    ok = 0
    for t in config("settings")["trackers"]:
        entry: dict[str, Any] = {
            "name": t["name"],
            "url": t["url"],
            "ok": False,
            "phases": {},
            "updated": None,
            "projects": [],
        }
        try:
            if t.get("json"):
                data = http.get_json(t["json"])
                entry["phases"] = phases_from_portwatch(data, projects)
                entry["projects"] = projects_from_portwatch(data)
                entry["updated"] = data.get("updated")
                media += media_from_portwatch(data, allowed)
                flagged = [f for f in data.get("flagged", []) or [] if isinstance(f, dict)]
                scam = {
                    "networks": len(flagged),
                    "copies": sum(int(f.get("count") or 0) for f in flagged if str(f.get("count") or "0").isdigit()),
                }
                e = data.get("exe") or {}
                if e.get("releasedAt"):
                    exe = {"released": e.get("releasedAt"), "by": str(e.get("by") or "")[:40]}
            else:
                html = http.get(t["url"]).text
                if CHALLENGE_RE.search(html[:6000]):
                    raise HttpError(403, t["url"], "(blocked by an anti-bot challenge; link only)")
                soup = BeautifulSoup(html, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                entry["projects"] = projects_from_race(soup)
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
    return {
        "trackers": trackers,
        "external": merge_external(trackers, projects),
        "media": media,
        "exe": exe,
        "scam": scam,
        "count": ok,
        "blocked": [t["name"] for t in trackers if not t["ok"]] or None,
    }
