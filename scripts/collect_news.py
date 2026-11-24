"""News: Google News RSS (ES and EN) plus a few publisher feeds. Only direct, allowlisted article URLs survive.

Google News wraps links in opaque news.google.com redirects that cannot be resolved without running their JS,
so most Google News entries are counted as "seen" and dropped (we never link news.google.com).
"""

from __future__ import annotations

import base64
import re
from typing import Any

import feedparser

from common import Http, HttpError, config, log
from items import GTA_RE, make_item


def _decode_google(url: str) -> str | None:
    """Old-style Google News ids embed the article URL in base64; new ones do not. Best effort."""
    m = re.search(r"/articles/([A-Za-z0-9_-]+)", url)
    if not m:
        return None
    token = m.group(1)
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
    except (ValueError, TypeError):
        return None
    found = re.search(rb"https?://[\x21-\x7e]+", raw)
    if not found:
        return None
    candidate = found.group(0).decode("ascii", "ignore")
    return None if "news.google" in candidate else candidate


def _relevant(texts: list[str]) -> bool:
    cfg = config("settings")["news"]
    joined = " ".join(texts).lower()
    return bool(GTA_RE.search(joined)) and any(t in joined for t in cfg["context_terms"])


def _parse(text: str, lang: str, google: bool) -> tuple[list[dict[str, Any]], int]:
    feed = feedparser.parse(text)
    out, seen = [], 0
    for e in feed.entries:
        texts = [e.get("title", ""), (e.get("summary") or "")[:2000]]
        if not _relevant(texts):
            continue
        seen += 1
        link = e.get("link", "")
        if google:
            link = _decode_google(link) or ""
            if not link:
                continue
        source = (e.get("source") or {}).get("title") or ""
        out.append(
            make_item(
                source="news",
                source_label=source or re.sub(r"^https?://(www\.)?([^/]+).*$", r"\2", link),
                url=link,
                created=e.get("published") or e.get("updated"),
                kind="news",
                texts=texts,
                lang=lang,
            )
        )
    return out, seen


def collect(http: Http) -> dict[str, Any]:
    cfg = config("settings")["news"]
    items: dict[str, dict[str, Any]] = {}
    seen_total, ok, errors = 0, 0, []
    for feed in cfg["google_news"]:
        try:
            got, seen = _parse(http.get(feed["url"]).text, feed["lang"], google=True)
            ok += 1
            seen_total += seen
            items.update({i["id"]: i for i in got})
        except HttpError as exc:
            log.warning("google news %s: %s", feed["lang"], exc)
            errors.append(str(exc))
    for feed in cfg["publisher_feeds"]:
        try:
            got, seen = _parse(http.get(feed["url"]).text, feed["lang"], google=False)
            ok += 1
            seen_total += seen
            items.update({i["id"]: i for i in got})
        except HttpError as exc:
            log.warning("publisher feed %s: %s", feed["url"], exc)
            errors.append(str(exc))
    if ok == 0:
        raise HttpError(0, "news", "(all feeds failed)")
    return {"items": list(items.values()), "count": len(items), "seen": seen_total, "feeds_failed": len(errors)}
