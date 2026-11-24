"""Hourly pipeline: collect → filter → classify → analyse → save data/ → build the site.

  python scripts/run_all.py              # real run (network)
  python scripts/run_all.py --offline    # fixtures from tests/fixtures/, writes nothing under data/

If a collector fails the rest keep going; the failure is recorded in data/sources_health.json and shown on the site.
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import analyze
import clone_detector
import collect_discovery
import collect_dmca
import collect_github
import collect_news
import collect_reddit
import collect_social
import collect_trackers
import credibility
import legal_filter
import spoiler_filter
from common import (
    DATA_DIR,
    Http,
    config,
    hours_since,
    iso,
    load_json,
    log,
    now_utc,
    parse_time,
    save_json,
    setup_logging,
)
from items import public

SOURCES = ["github", "dmca", "discovery", "reddit", "hn", "bluesky", "mastodon", "lemmy", "news", "trackers"]
OFFLINE_NOW = "2026-11-24T12:00:00Z"


class Runner:
    def __init__(self, offline: bool) -> None:
        self.offline = offline
        self.now = now_utc()
        self.health: dict[str, Any] = (load_json(DATA_DIR / "sources_health.json", {}) or {}).get("sources", {})
        if offline:
            self.health = {}

    def run(self, name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any] | None:
        entry = self.health.setdefault(name, {"last_ok": None, "last_fail": None, "items": 0, "error": None})
        try:
            result = fn()
        except Exception as exc:
            log.error("source %s failed: %s", name, exc)
            log.debug(traceback.format_exc())
            entry.update({"last_fail": iso(self.now), "ok_this_run": False, "error": str(exc)[:240]})
            if not entry.get("failing_since"):
                entry["failing_since"] = iso(self.now)
            return None
        entry.update(
            {"last_ok": iso(self.now), "ok_this_run": True, "items": int(result.get("count", 0)), "failing_since": None}
        )
        extra = result.get("blocked") or result.get("seen")
        entry["note"] = extra if extra else None
        return result


def _alerts(projects, candidates, forks_flagged, items, health, dmca, dropped, now) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in projects:
        if p["status"] == "dmca":
            out.append({"code": "dmca", "project": p["slug"], "name": p["name"], "url": p["dmca"]["url"]})
        elif p["status"] == "missing":
            out.append({"code": "missing", "project": p["slug"], "name": p["name"], "http": p["gh"].get("http_status")})
        if any(r.get("has_binaries") for r in p["gh"].get("releases", [])):
            out.append({"code": "binaries", "project": p["slug"], "name": p["name"]})
    for c in candidates + forks_flagged:
        if c.get("flagged"):
            out.append({"code": "clone", "repo": c["full_name"], "reasons": c["reasons"]})
    for it in items:
        if it.get("credibility") == "false":
            out.append({"code": "hoax", "item": it["id"]})
    warn_h = config("settings")["health"]["warn_after_hours"]
    for name, h in health.items():
        since = h.get("failing_since")
        if since and (hours_since(since, now) or 0) > warn_h:
            out.append({"code": "source_down", "source": name, "h": warn_h})
    if dmca:
        for n in dmca.get("gta_notices", [])[:5]:
            out.append({"code": "gta_notice", "url": n["url"], "date": n["date"]})
    if dropped:
        out.append({"code": "dropped", "n": dropped})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--offline", action="store_true", help="use tests/fixtures instead of the network")
    ap.add_argument("--no-build", action="store_true", help="collect and analyse only")
    ap.add_argument("--out", default=None, help="site output dir (default _site)")
    args = ap.parse_args(argv)
    setup_logging()
    if args.offline:
        os.environ.setdefault("TRACKER_NOW", OFFLINE_NOW)
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    http = Http(offline=args.offline, token=token)
    runner = Runner(args.offline)
    now = runner.now
    prev = {} if args.offline else (load_json(DATA_DIR / "latest.json", {}) or {})
    projects_cfg = config("projects")["projects"]
    tracked_repos = [p["repo"] for p in projects_cfg]

    gh = runner.run("github", lambda: collect_github.collect(http)) or {"projects": {}}
    dmca = runner.run("dmca", lambda: collect_dmca.collect(http)) or {}
    trackers = (runner.run("trackers", lambda: collect_trackers.collect(http)) or {}).get("trackers")
    if trackers is None:
        trackers = prev.get("trackers", [])
    readmes = {v["repo"]: v.get("_readme", "") for v in gh["projects"].values() if v.get("_readme")}
    disc = runner.run("discovery", lambda: collect_discovery.collect(http, readmes))

    raw_items: list[dict[str, Any]] = []
    for name, fn in [
        ("reddit", lambda: collect_reddit.collect(http)),
        ("hn", lambda: collect_social.collect_hn(http)),
        ("bluesky", lambda: collect_social.collect_bluesky(http)),
        ("mastodon", lambda: collect_social.collect_mastodon(http)),
        ("lemmy", lambda: collect_social.collect_lemmy(http)),
        ("news", lambda: collect_news.collect(http)),
    ]:
        res = runner.run(name, fn)
        if res:
            raw_items += res["items"]
        else:  # keep last known items of a failing source so the page does not go blank
            raw_items += [dict(i, _texts=[], _urls=[]) for i in prev.get("items", []) if i.get("source") == name]

    # Filters: legal first, then spoilers. Items without a date or older than 7 days are dropped.
    items, dropped_legal = legal_filter.filter_items(raw_items)
    items, dropped_spoiler = spoiler_filter.filter_items(items)
    items = [i for i in items if i.get("created") and now - parse_time(i["created"]) <= timedelta(days=7)]
    items = list({i["id"]: i for i in items}.values())

    # Analysis
    hist_before = {
        "hourly": load_json(analyze.HISTORY_DIR / "hourly.json", []) or [],
        "daily": load_json(analyze.HISTORY_DIR / "daily.json", []) or [],
    }
    if args.offline:
        hist_before = {"hourly": [], "daily": []}
    day_ago = analyze.snapshot_ago(hist_before, 24, now)
    prev_projects = {p["slug"]: p for p in prev.get("projects", [])}
    projects = []
    for p in projects_cfg:
        projects.append(
            analyze.analyse_project(
                p,
                gh["projects"].get(p["slug"]),
                prev_projects.get(p["slug"]),
                day_ago.get(p["slug"]),
                trackers,
                dmca.get("hits", {}),
            )
        )
    projects.sort(key=lambda x: (-x["score"], -(x["gh"].get("stars") or 0)))
    max_reached = max((analyze.reached_level(p["phase"]) for p in projects), default=0)
    credibility.apply(items, max_reached, tracked_repos)
    # A thread confirmed by the project itself is "confirmed"
    for it in items:
        if it.get("by_owner") and it["credibility"] in ("community", "unverified"):
            it["credibility"] = "confirmed"

    # Forks through the clone detector
    forks_flagged = []
    for v in gh["projects"].values():
        for f in v.get("_forks", []):
            verdict = clone_detector.analyse(f, readmes)
            if verdict["flagged"] and not legal_filter.text_violation(f["full_name"]):
                forks_flagged.append(verdict)

    events = analyze.build_events(projects, prev, now)
    events = events if args.offline else analyze.merge_events(events, now)
    history = analyze.update_history(projects, now, args.offline)
    candidates = (disc or {}).get("candidates")
    if candidates is None:
        candidates = (
            (load_json(DATA_DIR / "candidates.json", {}) or {}).get("candidates", []) if not args.offline else []
        )

    cfg_r = config("settings")["reddit"]
    weekly = sorted(
        [
            i
            for i in items
            if i["source"] in ("reddit", "hn", "lemmy")
            and (i.get("score") or 0) >= cfg_r["weekly_min_score"]
            and (i.get("comments") or 0) > cfg_r["weekly_min_comments"]
            and i["credibility"] != "false"
        ],
        key=lambda i: -(i.get("score") or 0),
    )[:10]
    media = sorted(
        [
            i
            for i in items
            if i["kind"] in ("video", "image") and (i.get("project") or i["credibility"] in ("confirmed", "community"))
        ],
        key=lambda i: i["created"],
        reverse=True,
    )[:12]
    alerts = _alerts(
        projects,
        candidates,
        forks_flagged,
        items,
        runner.health,
        dmca,
        dropped_legal + ((disc or {}).get("dropped_by_filters") or 0),
        now,
    )

    latest = {
        "generated": iso(now),
        "offline": args.offline,
        "projects": projects,
        "items": sorted([public(i) for i in items], key=lambda i: i["created"], reverse=True),
        "events": events,
        "weekly": [i["id"] for i in weekly],
        "media": [i["id"] for i in media],
        "alerts": alerts,
        "trackers": [{k: t.get(k) for k in ("name", "url", "ok", "updated", "phases")} for t in trackers],
        "dmca": {"hits": dmca.get("hits", {}), "scanned": dmca.get("scanned")},
        "max_reached": max_reached,
        "stats": {"dropped_legal": dropped_legal, "dropped_spoiler": dropped_spoiler, "http_calls": http.calls},
        "phase_changes": history["changes"],
    }
    health_doc = {"generated": iso(now), "sources": runner.health}
    if not args.offline:
        save_json(DATA_DIR / "latest.json", latest)
        save_json(DATA_DIR / "events.json", events)
        save_json(DATA_DIR / "sources_health.json", health_doc)
        save_json(
            DATA_DIR / "candidates.json",
            {"generated": iso(now), "candidates": candidates, "forks_flagged": forks_flagged},
        )
    log.info(
        "projects=%d items=%d events=%d candidates=%d dropped legal=%d spoiler=%d http=%d",
        len(projects),
        len(items),
        len(events),
        len(candidates),
        dropped_legal,
        dropped_spoiler,
        http.calls,
    )
    if not args.no_build:
        import build_site
        import make_og_image

        out = build_site.build(latest, health_doc, candidates, out_dir=args.out)
        make_og_image.write(out)
        log.info("site built in %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
