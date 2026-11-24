"""Static site builder (Jinja2, autoescape on). Output: _site/ respecting the base path from config/settings.yml."""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape

import legal_filter
import spoiler_filter
from common import ROOT, SITE_DIR, config, iso, load_json, parse_time
from summarize import (
    event_title,
    feed_entries,
    fmt,
    hero,
    i18n,
    in_window,
    item_title,
    phase_name,
    phase_short,
    phase_tech,
    project_view,
    range_text,
    rel_time,
)

TEMPLATES = ROOT / "templates"
STATIC = ROOT / "static"
LANGS = ["es", "en"]
SECTION_PATHS = {
    "es": {"home": "es/", "project": "es/proyectos/{slug}/", "status": "es/estado/", "feed": "es/feed.xml"},
    "en": {"home": "en/", "project": "en/projects/{slug}/", "status": "en/status/", "feed": "en/feed.xml"},
}
CSP = (
    "default-src 'self'; img-src 'self' https://preview.redd.it https://i.redd.it; script-src 'self'; "
    "style-src 'self'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'"
)


def site() -> dict[str, Any]:
    s = config("settings")["site"]
    base = "/" + s["base_path"].strip("/") + "/" if s["base_path"].strip("/") else "/"
    return {"origin": s["url"].rstrip("/"), "base": base, "repo": s["repo"], "show_thumbnails": s["show_thumbnails"]}


def path_for(lang: str, kind: str, slug: str = "") -> str:
    return SECTION_PATHS[lang][kind].format(slug=slug)


def env() -> Environment:
    e = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"], default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    e.globals["fmt"] = fmt
    e.filters["num"] = lambda v: f"{v:,}".replace(",", " ") if isinstance(v, int) and not isinstance(v, bool) else "—"
    return e


def _strip_urls(text: str) -> str:
    """Error messages may contain API URLs: show only the host, never a link or a non-allowlisted URL."""
    import re

    from common import domain_of

    return re.sub(r"https?://\S+", lambda m: domain_of(m.group(0)), text)


def _safe_link(url: str | None) -> str | None:
    """Every external URL rendered passes the allowlist + legal filter."""
    if not url:
        return None
    return url if legal_filter.url_violation(url) is None else None


def curated(lang: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {"press": [], "tracker": [], "thread": [], "repo": []}
    for link in config("curated_links")["links"]:
        title, why = link[f"title_{lang}"], link[f"why_{lang}"]
        if not _safe_link(link["url"]) or spoiler_filter.find_spoiler(title) or spoiler_filter.find_spoiler(why):
            continue
        groups.setdefault(link["type"], []).append(
            {
                "url": link["url"],
                "title": title,
                "why": why,
                "lang": link["lang"],
                "project": link.get("project"),
                "date": link.get("date"),
                "type": link["type"],
            }
        )
    for g in groups.values():
        g.sort(key=lambda x: x.get("date") or "", reverse=True)
    return groups


def _entry(T, it: dict[str, Any], names: dict[str, str], now, is_event: bool) -> dict[str, Any]:
    title = event_title(T, it, names) if is_event else item_title(T, it, names)
    return {
        "id": it["id"],
        "title": title,
        "url": _safe_link(it.get("url")),
        "created": it.get("created"),
        "rel": rel_time(T, it.get("created"), now),
        "credibility": it.get("credibility", "unverified"),
        "project": it.get("project"),
        "external": not is_event,
        "source": it.get("source"),
        "kind": it.get("kind"),
    }


def _group(entries: list[dict[str, Any]], names: dict[str, str], T) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        groups.setdefault(e.get("project") or "", []).append(e)
    order = sorted(groups, key=lambda k: (k == "", -len(groups[k])))
    return [{"name": names.get(k, T["kinds"]["general"]), "slug": k, "entries": groups[k]} for k in order]


def view_model(
    lang: str, data: dict[str, Any], health: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    T = i18n(lang)
    now = parse_time(data["generated"])
    names = {p["slug"]: p["name"] for p in data["projects"]}
    projects = [project_view(T, lang, p, now) for p in data["projects"]]
    leader = projects[0] if projects else None
    items_by_id = {i["id"]: i for i in data["items"]}

    entries = [_entry(T, e, names, now, True) for e in data["events"]]
    entries += [_entry(T, i, names, now, False) for i in data["items"] if i.get("credibility") != "false"]
    entries = [e for e in entries if e["url"]]
    entries.sort(key=lambda e: e["created"] or "", reverse=True)
    hour = [e for e in entries if in_window(e["created"], 1, now)]
    day = [e for e in entries if in_window(e["created"], 24, now)]
    media = [_entry(T, items_by_id[i], names, now, False) for i in data["media"] if i in items_by_id]
    weekly = [_entry(T, items_by_id[i], names, now, False) for i in data["weekly"] if i in items_by_id]
    media = [m for m in media if m["url"]]
    weekly = [m for m in weekly if m["url"]]

    # Milestones: who reached each phase, else the most optimistic indicative range among active projects.
    milestones = []
    from analyze import reached_level

    for level in range(2, 8):
        reached = [p for p in projects if reached_level(p["phase"]) >= level]
        if reached:
            milestones.append(
                {
                    "level": level,
                    "name": phase_name(T, level),
                    "tech": phase_tech(T, level),
                    "reached": fmt(T["eta"]["reached"], name=", ".join(p["name"] for p in reached[:3])),
                }
            )
            continue
        best = None
        for p in projects:
            for est in p["estimates"]:
                if (
                    est["to"] == level
                    and not est.get("stale")
                    and (best is None or est["min_days"] < best[1]["min_days"])
                ):
                    best = (p, est)
        if best:
            p, est = best
            pv = next(x for x in projects if x["slug"] == p["slug"])
            ev = next(x for x in pv["estimates_view"] if x["to"] == level)
            milestones.append(
                {
                    "level": level,
                    "name": phase_name(T, level),
                    "tech": phase_tech(T, level),
                    "range": range_text(T, est["min_days"], est["max_days"]),
                    "who": p["name"],
                    "reason": ev["reason"],
                }
            )
        else:
            milestones.append(
                {
                    "level": level,
                    "name": phase_name(T, level),
                    "tech": phase_tech(T, level),
                    "range": "—",
                    "reason": T["eta"]["stale"],
                }
            )

    # Alerts
    alerts_view = []
    for a in data["alerts"]:
        c = a["code"]
        if c == "dmca":
            alerts_view.append(
                {
                    "kind": "bad",
                    "title": T["alerts"]["dmca"],
                    "text": fmt(T["alerts"]["dmca_text"], name=a["name"]),
                    "url": _safe_link(a["url"]),
                }
            )
        elif c == "missing":
            alerts_view.append(
                {
                    "kind": "gone",
                    "title": T["alerts"]["missing"],
                    "text": fmt(T["alerts"]["missing_text"], name=a["name"], code=a.get("http") or "?"),
                }
            )
        elif c == "binaries":
            alerts_view.append(
                {
                    "kind": "warn",
                    "title": T["alerts"]["binaries"],
                    "text": fmt(T["alerts"]["binaries_text"], name=a["name"]),
                }
            )
        elif c == "clone":
            reasons = []
            for r in a["reasons"]:
                reasons.append(fmt(T["clone_reasons"][r["code"]], pct=r.get("pct", ""), repo=r.get("repo", "")))
            alerts_view.append(
                {
                    "kind": "bad",
                    "title": T["alerts"]["clone"],
                    "plain": True,
                    "text": fmt(T["alerts"]["clone_text"], repo=a["repo"], reasons="; ".join(reasons)),
                }
            )
        elif c == "hoax" and a["item"] in items_by_id:
            it = items_by_id[a["item"]]
            alerts_view.append(
                {
                    "kind": "hoax",
                    "title": T["alerts"]["hoax"],
                    "strike": True,
                    "text": fmt(T["alerts"]["hoax_text"], title=item_title(T, it, names)),
                }
            )
        elif c == "source_down":
            label = T["system"]["sources"].get(a["source"], a["source"])
            alerts_view.append(
                {
                    "kind": "warn",
                    "title": T["alerts"]["source_down"],
                    "text": fmt(T["alerts"]["source_down_text"], source=label, h=a["h"]),
                }
            )
    active_alerts = len(alerts_view)

    # Candidates
    radar = []
    for c in candidates[:15]:
        radar.append(
            {
                **c,
                "url": None if c.get("flagged") else _safe_link(c.get("url")),
                "reasons_text": "; ".join(
                    fmt(T["clone_reasons"][r["code"]], pct=r.get("pct", ""), repo=r.get("repo", ""))
                    for r in c.get("reasons", [])
                ),
            }
        )

    # History (phase changes), newest first
    history = []
    for ch in reversed(data.get("phase_changes", [])):
        if ch["project"] not in names:
            continue
        key = "initial" if ch["from"] is None else "change"
        history.append(
            {
                "t": ch["t"],
                "project": ch["project"],
                "text": fmt(
                    T["history"][key],
                    name=names[ch["project"]],
                    **{"from": phase_short(T, ch["from"]) if ch["from"] else "", "to": phase_short(T, ch["to"])},
                ),
            }
        )

    # Sources health
    sources = []
    warn_h = config("settings")["health"]["warn_after_hours"]
    for name in ["github", "dmca", "discovery", "reddit", "hn", "bluesky", "mastodon", "lemmy", "news", "trackers"]:
        h = (health.get("sources") or {}).get(name, {})
        failing_h = None
        if h.get("failing_since"):
            failing_h = (now - parse_time(h["failing_since"])).total_seconds() / 3600
        sources.append(
            {
                "name": T["system"]["sources"].get(name, name),
                "key": name,
                "ok": h.get("ok_this_run", False),
                "last_ok": rel_time(T, h.get("last_ok"), now),
                "last_ok_iso": h.get("last_ok"),
                "last_fail": rel_time(T, h.get("last_fail"), now) if h.get("last_fail") else "—",
                "items": h.get("items", 0),
                "warn": failing_h is not None and failing_h > warn_h,
                "error": _strip_urls(h.get("error") or "")[:160] if not h.get("ok_this_run") else "",
            }
        )

    gpu_faq = [{"name": p["name"], "req": p["requirements"]} for p in projects if p["requirements"]]
    max_reached = data.get("max_reached", 0)
    ticker = [e for e in entries if e["credibility"] != "false"][:3]
    return {
        "T": T,
        "lang": lang,
        "now_iso": data["generated"],
        "projects": projects,
        "leader": leader,
        "hero": hero(T, leader, max_reached),
        "max_phase_label": phase_short(T, leader["phase"]) if leader else "—",
        "max_phase_name": phase_name(T, leader["phase"]["level"]) if leader else "",
        "count_hour": len(hour),
        "count_day": len(day),
        "news_hour": _group(hour, names, T),
        "news_day": _group(day, names, T),
        "media": media,
        "weekly": weekly,
        "milestones": milestones,
        "radar": radar,
        "deeper": curated(lang),
        "alerts": alerts_view,
        "alerts_count": active_alerts,
        "history": history,
        "sources": sources,
        "gpu_faq": gpu_faq,
        "warn_h": warn_h,
        "ticker": ticker,
        "entries": entries,
        "names": names,
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def faq_jsonld(T: dict[str, Any], vm: dict[str, Any]) -> str:
    entities = []
    for f in T["faq"]:
        answer = f["a"]
        if f is T["faq"][3]:
            answer += " " + " ".join(f"{g['name']}: {g['req']}" for g in vm["gpu_faq"])
        entities.append({"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": answer}})
    doc = {"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": T["locale"], "mainEntity": entities}
    # Escape '<' so the JSON can never close the script element.
    return json.dumps(doc, ensure_ascii=False).replace("<", "\\u003c")


def rss(lang: str, data: dict[str, Any], s: dict[str, Any]) -> str:
    T = i18n(lang)
    names = {p["slug"]: p["name"] for p in data["projects"]}
    home = s["origin"] + s["base"] + path_for(lang, "home")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">',
        "<channel>",
        f"<title>{xml_escape(T['feed']['title'])}</title>",
        f"<link>{xml_escape(home)}</link>",
        f"<description>{xml_escape(T['feed']['description'])}</description>",
        f"<language>{lang}</language>",
        f'<atom:link href="{xml_escape(s["origin"] + s["base"] + path_for(lang, "feed"))}" rel="self" type="application/rss+xml"/>',
        f"<lastBuildDate>{parse_time(data['generated']).strftime('%a, %d %b %Y %H:%M:%S +0000')}</lastBuildDate>",
    ]
    for e in feed_entries(T, data, names):
        url = _safe_link(e["url"])
        if not url:
            continue
        dt = parse_time(e["created"])
        parts += [
            "<item>",
            f"<title>{xml_escape(e['title'])}</title>",
            f"<link>{xml_escape(url)}</link>",
            f'<guid isPermaLink="false">{xml_escape(e["id"])}</guid>',
            f"<description>{xml_escape(T['external'])}</description>",
            f"<pubDate>{dt.strftime('%a, %d %b %Y %H:%M:%S +0000') if dt else ''}</pubDate>",
            "</item>",
        ]
    parts += ["</channel>", "</rss>", ""]
    return "\n".join(parts)


def build(
    data: dict[str, Any], health: dict[str, Any], candidates: list[dict[str, Any]], out_dir: str | None = None
) -> Path:
    out = Path(out_dir) if out_dir else SITE_DIR
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(STATIC, out / "assets")
    write(out / ".nojekyll", "")
    s = site()
    e = env()
    pages: list[dict[str, Any]] = []  # for the sitemap

    def alternates(kind: str, slug: str = "") -> dict[str, str]:
        return {lng: s["base"] + path_for(lng, kind, slug) for lng in LANGS}

    for lang in LANGS:
        vm = view_model(lang, data, health, candidates)
        other = "en" if lang == "es" else "es"
        common = {
            **vm,
            "site": s,
            "csp": CSP,
            "other": other,
            "path_for": path_for,
            "faq_jsonld": faq_jsonld(vm["T"], vm),
            "feed_url": s["base"] + path_for(lang, "feed"),
            "ui": config("settings")["ui"],
        }
        alt = alternates("home")
        write(
            out / path_for(lang, "home") / "index.html",
            e.get_template("index.html").render(
                **common,
                page="home",
                alt=alt,
                canonical=s["origin"] + alt[lang],
                title=vm["T"]["meta"]["title"],
                description=vm["T"]["meta"]["description"],
            ),
        )
        pages.append({"alt": alt, "priority": "1.0"})
        for p in vm["projects"]:
            alt = alternates("project", p["slug"])
            write(
                out / path_for(lang, "project", p["slug"]) / "index.html",
                e.get_template("project.html").render(
                    **common,
                    page="project",
                    p=p,
                    alt=alt,
                    canonical=s["origin"] + alt[lang],
                    title=fmt(vm["T"]["meta"]["project_title"], name=p["name"]),
                    description=fmt(vm["T"]["meta"]["project_description"], name=p["name"]),
                    deeper_project=[x for g in vm["deeper"].values() for x in g if x.get("project") == p["slug"]],
                    media_project=[m for m in vm["media"] if m.get("project") == p["slug"]],
                ),
            )
            if lang == "es":
                pages.append({"alt": alt, "priority": "0.8"})
        alt = alternates("status")
        write(
            out / path_for(lang, "status") / "index.html",
            e.get_template("status.html").render(
                **common,
                page="status",
                alt=alt,
                canonical=s["origin"] + alt[lang],
                title=vm["T"]["meta"]["status_title"],
                description=vm["T"]["meta"]["status_description"],
            ),
        )
        if lang == "es":
            pages.append({"alt": alt, "priority": "0.3"})
        write(out / path_for(lang, "feed"), rss(lang, data, s))

    write(
        out / "index.html",
        e.get_template("root.html").render(site=s, csp=CSP, es=i18n("es"), en=i18n("en"), alt=alternates("home")),
    )
    write(
        out / "404.html",
        e.get_template("404.html").render(site=s, csp=CSP, alt=alternates("home"), es=i18n("es"), en=i18n("en")),
    )
    write(out / "robots.txt", f"User-agent: *\nAllow: /\nSitemap: {s['origin']}{s['base']}sitemap.xml\n")
    lastmod = data["generated"][:10]
    sm = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">',
    ]
    for pg in pages:
        for lng in LANGS:
            sm.append("<url>")
            sm.append(f"<loc>{xml_escape(s['origin'] + pg['alt'][lng])}</loc>")
            for l2 in LANGS:
                sm.append(
                    f'<xhtml:link rel="alternate" hreflang="{l2}" href="{xml_escape(s["origin"] + pg["alt"][l2])}"/>'
                )
            sm.append(
                f'<xhtml:link rel="alternate" hreflang="x-default" href="{xml_escape(s["origin"] + pg["alt"]["en"])}"/>'
            )
            sm.append(f"<lastmod>{lastmod}</lastmod><priority>{pg['priority']}</priority>")
            sm.append("</url>")
    sm.append("</urlset>\n")
    write(out / "sitemap.xml", "\n".join(sm))
    return out


def main() -> None:
    from common import DATA_DIR, setup_logging

    setup_logging()
    data = load_json(DATA_DIR / "latest.json")
    health = load_json(DATA_DIR / "sources_health.json", {}) or {}
    cands = (load_json(DATA_DIR / "candidates.json", {}) or {}).get("candidates", [])
    if not data:
        raise SystemExit("data/latest.json missing: run scripts/run_all.py first")
    out = build(data, health, cands)
    import make_og_image

    make_og_image.write(out)
    print(f"built {out} at {iso(datetime.now().astimezone())}")


if __name__ == "__main__":
    main()
