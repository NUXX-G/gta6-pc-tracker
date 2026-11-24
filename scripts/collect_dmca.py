"""GitHub DMCA log reader (public repo github/dmca).

Lists this and last month's notice files, opens the ones whose name hints at GTA / Rockstar / Take-Two or a
tracked owner, and looks for tracked repos. A hit means "DMCA CONFIRMED" (vs. a 404 with no notice: removed by owners).
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from common import Http, HttpError, config, log, now_utc

API = "https://api.github.com/repos/github/dmca"
NAME_HINTS = re.compile(r"take-?two|rockstar|gta|grand-theft|leonida|victory|xbox", re.IGNORECASE)
MAX_FILES = 15


def _month_dirs() -> list[str]:
    now = now_utc()
    prev = now.replace(day=1) - timedelta(days=1)
    return [now.strftime("%Y/%m"), prev.strftime("%Y/%m")]


def collect(http: Http) -> dict[str, Any]:
    projects = config("projects")["projects"]
    owners = {p["repo"].split("/")[0].lower() for p in projects}
    files: list[dict[str, Any]] = []
    for d in _month_dirs():
        try:
            listing = http.get_json(f"{API}/contents/{d}", github=True)
        except HttpError as exc:
            if exc.status == 404:
                continue
            raise
        files.extend(f for f in listing if f.get("type") == "file")
    candidates = [f for f in files if NAME_HINTS.search(f["name"]) or any(o in f["name"].lower() for o in owners)]
    candidates.sort(key=lambda f: f["name"], reverse=True)
    hits: dict[str, dict[str, Any]] = {}
    gta_notices: list[dict[str, Any]] = []
    for f in candidates[:MAX_FILES]:
        try:
            text = http.get(
                f"{API}/contents/{f['path']}", headers={"Accept": "application/vnd.github.raw"}, github=True
            ).text
        except HttpError as exc:
            log.warning("dmca file %s: %s", f["path"], exc)
            continue
        low = text.lower()
        notice = {"name": f["name"], "url": f.get("html_url"), "date": f["name"][:10]}
        if re.search(r"\bgta\b|grand theft auto|rockstar|take-two", low):
            gta_notices.append(notice)
        for p in projects:
            if p["repo"].lower() in low:
                hits[p["slug"]] = notice
    return {
        "scanned": len(files),
        "opened": min(len(candidates), MAX_FILES),
        "hits": hits,
        "gta_notices": gta_notices,
        "count": len(files),
    }
