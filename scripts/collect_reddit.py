"""Reddit: public JSON search per subreddit, RSS as fallback. If Reddit blocks (403/429) it is logged, never fatal."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import feedparser

from common import Http, HttpError, config, log
from items import make_item, relevant


def _owners() -> set[str]:
    return {p["repo"].split("/")[0].lower() for p in config("projects")["projects"]}


def _kind(d: dict[str, Any]) -> str:
    domain = (d.get("domain") or "").lower()
    hint = d.get("post_hint") or ""
    flair = (d.get("link_flair_text") or "").lower()
    if d.get("is_video") or domain == "v.redd.it" or hint in ("hosted:video", "rich:video"):
        return "video"
    if hint == "image" or domain == "i.redd.it" or d.get("is_gallery"):
        return "image"
    if "progress" in flair or "news" in flair:
        return "progress"
    return "discussion"


def from_json(payload: dict[str, Any], sub: str) -> list[dict[str, Any]]:
    owners = _owners()
    out = []
    for child in payload.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("over_18"):
            continue
        texts = [d.get("title", ""), (d.get("selftext") or "")[:3000], d.get("link_flair_text") or ""]
        if not relevant(texts):
            continue
        permalink = "https://www.reddit.com" + d.get("permalink", "")
        link = d.get("url_overridden_by_dest") or d.get("url") or ""
        urls = [link] if link and link != permalink and not link.startswith("/") else []
        out.append(
            make_item(
                source="reddit",
                source_label=f"r/{d.get('subreddit', sub)}",
                url=permalink,
                created=d.get("created_utc"),
                kind=_kind(d),
                texts=texts,
                urls=urls,
                score=d.get("score"),
                comments=d.get("num_comments"),
                spoiler_flag=bool(d.get("spoiler")),
                by_owner=(d.get("author") or "").lower() in owners,
            )
        )
    return out


def from_rss(text: str, sub: str) -> list[dict[str, Any]]:
    feed = feedparser.parse(text)
    out = []
    for e in feed.entries:
        texts = [e.get("title", ""), (e.get("summary") or "")[:3000]]
        if not relevant(texts):
            continue
        out.append(
            make_item(
                source="reddit",
                source_label=f"r/{sub}",
                url=e.get("link", ""),
                created=e.get("published") or e.get("updated"),
                kind="discussion",
                texts=texts,
            )
        )
    return out


def collect(http: Http) -> dict[str, Any]:
    cfg = config("settings")["reddit"]
    q = quote(cfg["query"])
    items: list[dict[str, Any]] = []
    blocked: list[str] = []
    ok = 0
    for sub in cfg["subreddits"]:
        try:
            payload = http.get_json(
                f"https://www.reddit.com/r/{sub}/search.json?q={q}&restrict_sr=1&sort=new&t=week&limit=50"
            )
            items += from_json(payload, sub)
            ok += 1
            continue
        except (HttpError, ValueError) as exc:
            log.warning("reddit json r/%s: %s", sub, exc)
            if isinstance(exc, HttpError) and exc.status in (403, 429):
                blocked.append(f"json:{exc.status}")
        try:
            text = http.get(f"https://www.reddit.com/r/{sub}/search.rss?q={q}&restrict_sr=on&sort=new&t=week").text
            items += from_rss(text, sub)
            ok += 1
        except HttpError as exc:
            log.warning("reddit rss r/%s: %s", sub, exc)
            if exc.status in (403, 429):
                blocked.append(f"rss:{exc.status}")
    if ok == 0:
        detail = ", ".join(sorted(set(blocked))) or "no response"
        raise HttpError(403 if blocked else 0, "https://www.reddit.com", f"(blocked from this runner: {detail})")
    dedup = {i["id"]: i for i in items}
    return {"items": list(dedup.values()), "count": len(dedup), "blocked": sorted(set(blocked))}
