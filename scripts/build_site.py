"""Static site builder (Jinja2, autoescape on). Output: _site/ respecting the base path from config/settings.yml."""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

import legal_filter
import spoiler_filter
from common import ROOT, SITE_DIR, config, domain_of, iso, load_json, load_yaml, parse_time
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
CONTENT = ROOT / "content"
LANGS = ["es", "en"]
SECTION_PATHS = {
    "es": {
        "home": "es/",
        "project": "es/proyectos/{slug}/",
        "status": "es/estado/",
        "feed": "es/feed.xml",
        "how": "es/como-funciona/",
        "fake": "es/descargas-falsas/",
        "media": "es/galeria/",
        "news": "es/noticias/",
    },
    "en": {
        "home": "en/",
        "project": "en/projects/{slug}/",
        "status": "en/status/",
        "feed": "en/feed.xml",
        "how": "en/how-it-works/",
        "fake": "en/fake-downloads/",
        "media": "en/gallery/",
        "news": "en/news/",
    },
}
# Hosts a preview image may load from (only after a click; see static/js/site.js). Video embeds: youtube-nocookie.
IMG_HOSTS = [
    "github.com",  # github.com/user-attachments/assets/... (redirects to private-user-images)
    "private-user-images.githubusercontent.com",
    "raw.githubusercontent.com",
    "user-images.githubusercontent.com",
    "opengraph.githubassets.com",
    "i.redd.it",
    "preview.redd.it",
    "external-preview.redd.it",
    "files.mastodon.social",
    "lemmy.world",
    "i.ytimg.com",
    "portwatch-vi.xyz",
]
CSP = (
    "default-src 'self'; img-src 'self' "
    + " ".join(f"https://{h}" for h in IMG_HOSTS)
    + "; script-src 'self'; style-src 'self'; frame-src https://www.youtube-nocookie.com; "
    "object-src 'none'; base-uri 'none'; form-action 'none'"
)


def site() -> dict[str, Any]:
    s = config("settings")["site"]
    base = "/" + s["base_path"].strip("/") + "/" if s["base_path"].strip("/") else "/"
    return {"origin": s["url"].rstrip("/"), "base": base, "repo": s["repo"], "show_thumbnails": s["show_thumbnails"]}


def path_for(lang: str, kind: str, slug: str = "") -> str:
    return SECTION_PATHS[lang][kind].format(slug=slug)


def md_inline(text: str) -> Markup:
    """Escape, then allow **bold** only. Used for our own long-form content."""
    safe = str(escape(text))
    return Markup(re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe))


def env() -> Environment:
    e = Environment(
        loader=FileSystemLoader(str(TEMPLATES)),
        autoescape=select_autoescape(["html", "xml"], default=True),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    e.globals["fmt"] = fmt
    e.filters["num"] = lambda v: f"{v:,}".replace(",", " ") if isinstance(v, int) and not isinstance(v, bool) else "—"
    e.filters["md"] = md_inline
    e.filters["host"] = domain_of
    return e


def _strip_urls(text: str) -> str:
    """Error messages may contain API URLs: show only the host, never a link or a non-allowlisted URL."""
    return re.sub(r"https?://\S+", lambda m: domain_of(m.group(0)), text)


def _safe_link(url: str | None) -> str | None:
    """Every external URL rendered passes the allowlist + legal filter."""
    if not url:
        return None
    return url if legal_filter.url_violation(url) is None else None


def _safe_img(url: str | None) -> str | None:
    if not url or _safe_link(url) is None:
        return None
    host = domain_of(url)
    return url if any(host == h or host.endswith("." + h) for h in IMG_HOSTS) else None


def content(name: str, lang: str) -> dict[str, Any]:
    return load_yaml(CONTENT / f"{name}.yml")[lang]


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
                "host": domain_of(link["url"]),
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


def media_view(T, m: dict[str, Any], names: dict[str, str], now) -> dict[str, Any] | None:
    url = _safe_link(m.get("url"))
    if not url:
        return None
    thumb = _safe_img(m.get("thumb"))
    embed = m.get("embed") if m.get("embed") and re.fullmatch(r"[\w-]{11}", m["embed"]) else None
    project = names.get(m.get("project") or "")
    kind = T["kinds"].get(m.get("kind", "image"), T["kinds"]["image"])
    return {
        "id": m["id"],
        "kind": m.get("kind", "image"),
        "kind_label": kind,
        "title": " · ".join(x for x in (kind, project or T["kinds"]["general"]) if x),
        "source_label": m.get("source_label") or "",
        "url": url,
        "thumb": thumb,
        "thumb_host": domain_of(thumb) if thumb else "",
        "embed": embed,
        "origin": m.get("origin", "community"),
        "origin_label": T["origins"].get(m.get("origin", "community"), m.get("origin", "")),
        "credibility": m.get("credibility", "unverified"),
        "project": m.get("project"),
        "created": m.get("created"),
        "rel": rel_time(T, m.get("created"), now) if m.get("created") else "",
    }


def press_view(T, it: dict[str, Any], names: dict[str, str], now) -> dict[str, Any] | None:
    url = _safe_link(it.get("url"))
    if not url:
        return None
    headline = it.get("headline") or ""
    if headline and (spoiler_filter.find_spoiler(headline) or legal_filter.text_violation(headline)):
        headline = ""
    return {
        "id": it["id"],
        "url": url,
        "headline": headline or item_title(T, it, names),
        "outlet": it.get("source_label") or domain_of(url),
        "host": domain_of(url),
        "lang": (it.get("lang") or "").upper(),
        "created": it.get("created"),
        "rel": rel_time(T, it.get("created"), now),
        "credibility": it.get("credibility", "community"),
        "project": names.get(it.get("project") or ""),
    }


def external_view(T, rows: list[dict[str, Any]], names: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for r in rows or []:
        if legal_filter.text_violation(r.get("name") or "") or spoiler_filter.find_spoiler(r.get("name") or ""):
            continue
        repo = r.get("repo")
        url = _safe_link(f"https://github.com/{repo}") if repo else None
        stages = r.get("stages") or {}
        out.append(
            {
                "name": names.get(r.get("slug") or "", r.get("name")),
                "repo": repo,
                "url": url,
                "slug": r.get("slug"),
                "stages": stages,
                "best": max(stages.values() or [0]),
                "watch": r.get("watch"),
            }
        )
    return out


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
    weekly = [_entry(T, items_by_id[i], names, now, False) for i in data["weekly"] if i in items_by_id]
    weekly = [m for m in weekly if m["url"]]

    media_src = data.get("media_items")
    if media_src is None:  # older snapshots: only ids of items
        media_src = [items_by_id[i] for i in data.get("media", []) if i in items_by_id]
    media = [v for v in (media_view(T, m, names, now) for m in media_src) if v]
    origins = [o for o in ("project", "tracker", "community", "video", "press") if any(m["origin"] == o for m in media)]

    press = [
        v for v in (press_view(T, items_by_id[i], names, now) for i in data.get("press", []) if i in items_by_id) if v
    ]

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
    clones = 0
    hoaxes = 0
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
            clones += 1
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
            hoaxes += 1
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
    flagged_candidates = sum(1 for c in candidates if c.get("flagged"))

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
    for name in [
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
    ]:
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

    trackers_view = []
    for t in data.get("trackers", []):
        trackers_view.append({"name": t["name"], "url": _safe_link(t["url"]), "ok": t.get("ok")})

    ms = data.get("milestones") or config("settings")["milestones"]

    def parts(value: Any) -> dict[str, str]:
        dt = parse_time(value)
        secs = max(0, int((now - dt).total_seconds())) if dt else 0
        return {"d": str(secs // 86400), "h": f"{secs % 86400 // 3600:02d}", "m": f"{secs % 3600 // 60:02d}"}

    counters = {
        "console_parts": parts(ms.get("console_launch")),
        "exe_parts": parts(ms.get("exe_leak")),
        "console": ms.get("console_launch"),
        "exe": ms.get("exe_leak"),
        "exe_by": ms.get("exe_leak_by") or "",
        "exe_source": _safe_link(ms.get("exe_leak_source")),
    }

    gpu_faq = [{"name": p["name"], "req": p["requirements"]} for p in projects if p["requirements"]]
    max_reached = data.get("max_reached", 0)
    ticker = [e for e in entries if e["credibility"] != "false"][:3]
    faq = list(T["faq"]) + list(T.get("faq_extra", []))
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
        "media_origins": origins,
        "press": press,
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
        "external": external_view(T, data.get("external", []), names),
        "trackers": trackers_view,
        "counters": counters,
        "faq": faq,
        "scam": {
            "clones": flagged_candidates + clones,
            "copies": (data.get("scam") or {}).get("copies") or 0,
            "hoaxes": hoaxes,
        },
    }


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _ld(doc: Any) -> str:
    # Escape '<' so the JSON can never close the script element.
    return json.dumps(doc, ensure_ascii=False).replace("<", "\\u003c")


def faq_jsonld(T: dict[str, Any], vm: dict[str, Any]) -> dict[str, Any]:
    entities = []
    for i, f in enumerate(vm["faq"]):
        answer = f["a"]
        if i == 3:
            answer += " " + " ".join(f"{g['name']}: {g['req']}" for g in vm["gpu_faq"])
        entities.append({"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": answer}})
    return {"@type": "FAQPage", "inLanguage": T["locale"], "mainEntity": entities}


def home_jsonld(T, vm, s, lang) -> str:
    home = s["origin"] + s["base"] + path_for(lang, "home")
    graph = [
        {
            "@type": "WebSite",
            "@id": home + "#website",
            "url": home,
            "name": T["meta"]["site_name"],
            "description": T["meta"]["description"],
            "inLanguage": T["locale"],
        },
        {
            "@type": "WebPage",
            "@id": home + "#page",
            "url": home,
            "name": T["meta"]["title"],
            "isPartOf": {"@id": home + "#website"},
            "dateModified": vm["now_iso"],
            "inLanguage": T["locale"],
            "about": {
                "@type": "VideoGame",
                "name": "Grand Theft Auto VI",
                "gamePlatform": ["PlayStation 5", "Xbox Series X|S"],
            },
        },
        {
            "@type": "ItemList",
            "name": T["sections"]["projects"],
            "itemListOrder": "https://schema.org/ItemListOrderDescending",
            "numberOfItems": len(vm["projects"]),
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "position": i + 1,
                    "name": p["name"],
                    "url": s["origin"] + s["base"] + path_for(lang, "project", p["slug"]),
                }
                for i, p in enumerate(vm["projects"])
            ],
        },
        faq_jsonld(T, vm),
    ]
    return _ld({"@context": "https://schema.org", "@graph": graph})


def page_jsonld(T, s, lang, kind: str, title: str, description: str, now_iso: str, crumb: str, extra=None) -> str:
    url = s["origin"] + s["base"] + path_for(lang, kind) if kind != "project" else extra["url"]
    home = s["origin"] + s["base"] + path_for(lang, "home")
    crumbs = [{"@type": "ListItem", "position": 1, "name": T["crumbs"]["home"], "item": home}]
    if kind == "project":
        crumbs.append({"@type": "ListItem", "position": 2, "name": T["crumbs"]["projects"], "item": home + "#projects"})
    crumbs.append({"@type": "ListItem", "position": len(crumbs) + 1, "name": crumb, "item": url})
    graph: list[dict[str, Any]] = [{"@type": "BreadcrumbList", "itemListElement": crumbs}]
    if kind in ("how", "fake"):
        graph.append(
            {
                "@type": "Article",
                "headline": title[:110],
                "description": description,
                "inLanguage": T["locale"],
                "dateModified": now_iso,
                "mainEntityOfPage": url,
                "author": {"@type": "Organization", "name": T["meta"]["site_name"], "url": home},
                "publisher": {"@type": "Organization", "name": T["meta"]["site_name"], "url": home},
                "image": s["origin"] + s["base"] + "assets/og.png",
            }
        )
    elif kind in ("media", "news"):
        graph.append(
            {
                "@type": "CollectionPage",
                "name": title,
                "description": description,
                "url": url,
                "inLanguage": T["locale"],
                "dateModified": now_iso,
            }
        )
    elif kind == "project" and extra:
        doc = {
            "@type": "SoftwareSourceCode" if extra.get("repo_url") else "CreativeWork",
            "name": extra["name"],
            "description": description,
            "url": url,
            "inLanguage": T["locale"],
            "dateModified": now_iso,
        }
        if extra.get("repo_url"):
            doc["codeRepository"] = extra["repo_url"]
            if extra.get("language"):
                doc["programmingLanguage"] = extra["language"]
        graph.append(doc)
    return _ld({"@context": "https://schema.org", "@graph": graph})


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
        T = vm["T"]
        other = "en" if lang == "es" else "es"
        common = {
            **vm,
            "site": s,
            "csp": CSP,
            "other": other,
            "path_for": path_for,
            "feed_url": s["base"] + path_for(lang, "feed"),
            "ui": config("settings")["ui"],
        }

        def render(kind: str, template: str, title: str, description: str, ld: str, slug: str = "", **kw: Any) -> None:
            alt = alternates(kind, slug)
            write(
                out / path_for(lang, kind, slug) / "index.html",
                e.get_template(template).render(
                    **common,
                    page=kind,
                    alt=alt,
                    canonical=s["origin"] + alt[lang],
                    title=title,
                    description=description,
                    jsonld=ld,
                    **kw,
                ),
            )

        render(
            "home",
            "index.html",
            T["meta"]["title"],
            T["meta"]["description"],
            home_jsonld(T, vm, s, lang),
        )
        if lang == "es":
            pages.append({"alt": alternates("home"), "priority": "1.0"})
        for p in vm["projects"]:
            title = fmt(T["meta"]["project_title"], name=p["name"])
            desc = fmt(T["meta"]["project_description"], name=p["name"], phase=p["phase_label"])
            url = s["origin"] + s["base"] + path_for(lang, "project", p["slug"])
            render(
                "project",
                "project.html",
                title,
                desc,
                page_jsonld(
                    T,
                    s,
                    lang,
                    "project",
                    title,
                    desc,
                    vm["now_iso"],
                    p["name"],
                    {
                        "url": url,
                        "name": p["name"],
                        "repo_url": None if p["private"] else p["url"],
                        "language": p["gh"].get("language"),
                    },
                ),
                slug=p["slug"],
                p=p,
                deeper_project=[x for g in vm["deeper"].values() for x in g if x.get("project") == p["slug"]],
                media_project=[m for m in vm["media"] if m.get("project") == p["slug"]],
                press_project=[x for x in vm["press"] if x.get("project") == p["name"]],
            )
            if lang == "es":
                pages.append({"alt": alternates("project", p["slug"]), "priority": "0.8"})
        for kind, name, template, prio in (
            ("how", "explainer", "how.html", "0.9"),
            ("fake", "fake_downloads", "fake.html", "0.9"),
        ):
            C = content(name, lang)
            render(
                kind,
                template,
                C["title"],
                C["description"],
                page_jsonld(T, s, lang, kind, C["title"], C["description"], vm["now_iso"], C["h1"]),
                C=C,
            )
            if lang == "es":
                pages.append({"alt": alternates(kind), "priority": prio})
        render(
            "media",
            "media.html",
            T["meta"]["media_title"],
            T["meta"]["media_description"],
            page_jsonld(
                T,
                s,
                lang,
                "media",
                T["meta"]["media_title"],
                T["meta"]["media_description"],
                vm["now_iso"],
                T["sections"]["media"],
            ),
        )
        render(
            "news",
            "news.html",
            T["meta"]["news_title"],
            T["meta"]["news_description"],
            page_jsonld(
                T,
                s,
                lang,
                "news",
                T["meta"]["news_title"],
                T["meta"]["news_description"],
                vm["now_iso"],
                T["sections"]["press"],
            ),
        )
        render(
            "status",
            "status.html",
            T["meta"]["status_title"],
            T["meta"]["status_description"],
            page_jsonld(
                T,
                s,
                lang,
                "status",
                T["meta"]["status_title"],
                T["meta"]["status_description"],
                vm["now_iso"],
                T["system"]["title"],
            ),
        )
        if lang == "es":
            pages.append({"alt": alternates("media"), "priority": "0.7"})
            pages.append({"alt": alternates("news"), "priority": "0.7"})
            pages.append({"alt": alternates("status"), "priority": "0.3"})
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
            sm.append(
                f"<lastmod>{lastmod}</lastmod><changefreq>hourly</changefreq><priority>{pg['priority']}</priority>"
            )
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
