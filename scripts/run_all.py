"""Hourly pipeline: collect → filter → classify → analyse → save data/ → build the site.

  python scripts/run_all.py              # real run (network)
  python scripts/run_all.py --offline    # fixtures from tests/fixtures/, writes nothing under data/

If a collector fails the rest keep going; the failure is recorded in data/sources_health.json and shown on the site.
"""

from __future__ import annotations

import argparse
import html
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
import collect_media
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
    domain_allowed,
    hours_since,
    iso,
    load_json,
    log,
    now_utc,
    parse_time,
    save_json,
    setup_logging,
)
from items import clean, detect_project, public

SOURCES = [
    "github",
    "dmca",
    "discovery",
    "reddit",
    "hn",
    "bluesky",
    "mastodon",
    "lemmy",
    "youtube",
    "news",
    "trackers",
]
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
    hoaxes = [it for it in items if it.get("credibility") == "false"]
    for it in sorted(hoaxes, key=lambda i: i.get("created") or "", reverse=True)[:8]:
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


def build_media(
    items: list[dict[str, Any]], gh: dict[str, Any], tr: dict[str, Any], prev: dict[str, Any], now
) -> list[dict[str, Any]]:
    """Every image/video that passes the legal and spoiler filters, labelled by origin and credibility.

    Hoaxes (credibility 'false') are never listed. Nothing here is loaded by the page until the viewer clicks.
    """
    allowed = legal_filter.allowlist()
    out: list[dict[str, Any]] = []
    for it in items:
        if it["kind"] not in ("video", "image") or it["credibility"] == "false":
            continue
        origin = "video" if it["source"] == "youtube" else "community"
        out.append({**public(it), "origin": origin})
    extra = collect_media.collect_readmes(gh.get("projects", {}))
    for m in (tr or {}).get("media", []):
        m["project"] = detect_project(m.get("_texts", []), [m["url"]])
        m["credibility"] = "community"
        extra.append(m)
    for m in extra:
        if legal_filter.url_violation(m["url"]) or (m.get("thumb") and legal_filter.url_violation(m["thumb"])):
            continue
        if spoiler_filter.is_spoiler(m.get("_texts", [])):
            continue
        out.append(public(m))
    if not tr and not gh.get("projects"):  # sources down: keep the last catalogue
        out += [m for m in prev.get("media_items", []) if m.get("origin") in ("project", "tracker")]
    out = collect_media.finalize(list({m["id"]: m for m in out}.values()), allowed)
    out = [m for m in out if not m.get("thumb") or domain_allowed(m["thumb"], allowed)]
    out.sort(key=lambda m: m.get("created") or "", reverse=True)
    # Round-robin across origins so one origin (e.g. README screenshots) never fills the whole grid.
    by_origin: dict[str, list[dict[str, Any]]] = {}
    for m in out:
        by_origin.setdefault(m["origin"], []).append(m)
    mixed: list[dict[str, Any]] = []
    while any(by_origin.values()):
        for key in ("project", "tracker", "community", "video"):
            if by_origin.get(key):
                mixed.append(by_origin[key].pop(0))
        for key in [k for k in by_origin if k not in ("project", "tracker", "community", "video")]:
            if by_origin[key]:
                mixed.append(by_origin[key].pop(0))
    return mixed[: config("settings")["media"]["max_items"]]


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
    tracked_repos = [p["repo"] for p in projects_cfg if p.get("repo")]

    gh = runner.run("github", lambda: collect_github.collect(http)) or {"projects": {}}
    dmca = runner.run("dmca", lambda: collect_dmca.collect(http)) or {}
    tr = runner.run("trackers", lambda: collect_trackers.collect(http)) or {}
    trackers = tr.get("trackers")
    if trackers is None:
        trackers = prev.get("trackers", [])
    external = tr.get("external") if tr else prev.get("external", [])
    readmes = {v["repo"]: v.get("_readme", "") for v in gh["projects"].values() if v.get("_readme")}
    disc = runner.run("discovery", lambda: collect_discovery.collect(http, readmes))

    raw_items: list[dict[str, Any]] = []
    for name, fn in [
        ("reddit", lambda: collect_reddit.collect(http)),
        ("hn", lambda: collect_social.collect_hn(http)),
        ("bluesky", lambda: collect_social.collect_bluesky(http)),
        ("mastodon", lambda: collect_social.collect_mastodon(http)),
        ("lemmy", lambda: collect_social.collect_lemmy(http)),
        ("youtube", lambda: collect_media.collect_youtube(http)),
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
    projects.sort(key=lambda x: (-x["score"], x.get("priority", 99), -(x["gh"].get("stars") or 0)))
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
    media = build_media(items, gh, tr, prev, now)
    show_headlines = config("settings")["news"].get("show_press_headlines")
    for it in items:
        if it["source"] == "news" and show_headlines and it.get("_texts"):
            title = html.unescape(clean(it["_texts"][0], 300))
            label = it.get("source_label") or ""
            if label and title.endswith(" - " + label):
                title = title[: -len(label) - 3]
            it["headline"] = title[:160].strip()
    press = sorted(
        [i for i in items if i["source"] == "news" and i["credibility"] != "false"],
        key=lambda i: i["created"],
        reverse=True,
    )[:30]
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
        "media": [i["id"] for i in media if i["id"] in {x["id"] for x in items}],
        "media_items": media,
        "press": [i["id"] for i in press],
        "external": external,
        "scam": (tr or {}).get("scam") or prev.get("scam"),
        "milestones": config("settings")["milestones"],
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
