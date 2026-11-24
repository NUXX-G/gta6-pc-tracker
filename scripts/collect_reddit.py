"""Reddit without the official API or OAuth: four public layers, run every hour and merged by post id.

  1. rss          Reddit's own RSS (search.rss per term, new.rss per subreddit)          → partial (no score/comments)
  2. arcticshift  Arctic Shift archive API (/api/posts/search)                           → full once a post is ~36 h old
  3. pullpush     PullPush search API (/reddit/search/submission/)                        → same as Arctic Shift
  4. bing         Bing search RSS limited to reddit.com/r/<allowed sub>/comments/...     → partial (discovery only)

Rules: the project's own User-Agent, no proxies/IP rotation/browser impersonation. A layer that blocks us is logged
and skipped; PullPush, which refuses automated clients, is paused for a day after a refusal instead of being retried.
Every item keeps the same schema as before plus `via` (the layer that gave the best data), `vias` (all layers that
saw it) and `completeness` (full|partial). Third-party text stays private (`_texts`) for the filters only.
"""

from __future__ import annotations

import re
import time
from datetime import timedelta
from statistics import mean
from typing import Any
from urllib.parse import quote

import feedparser

from collect_media import YT_ID
from common import Http, HttpError, SourcePaused, config, iso, log, now_utc, parse_time
from items import make_item, relevant

POST_RE = re.compile(r"reddit\.com/r/([A-Za-z0-9_]+)/comments/([a-z0-9]{1,12})(?![a-z0-9])", re.IGNORECASE)
LAYERS = ["reddit_rss", "arcticshift", "pullpush", "bing"]
VIA = {"reddit_rss": "rss", "arcticshift": "arcticshift", "pullpush": "pullpush", "bing": "bing"}
PREFER = {"arcticshift": 0, "pullpush": 1, "rss": 2, "bing": 3}  # tie-break when completeness is equal


def _cfg() -> dict[str, Any]:
    return config("sources")["reddit"]


def _owners() -> set[str]:
    return {p["repo"].split("/")[0].lower() for p in config("projects")["projects"] if p.get("repo")}


def _allowed_subs() -> dict[str, str]:
    return {s.lower(): s for s in _cfg()["subreddits"]}


def post_id(url: str) -> tuple[str, str] | None:
    """(subreddit, id) from any reddit permalink, else None."""
    m = POST_RE.search(url or "")
    return (m.group(1), m.group(2).lower()) if m else None


def canonical(sub: str, pid: str) -> str:
    return f"https://www.reddit.com/r/{sub}/comments/{pid}/"


def _kind(d: dict[str, Any]) -> str:
    domain = (d.get("domain") or "").lower()
    hint = d.get("post_hint") or ""
    flair = (d.get("link_flair_text") or "").lower()
    if (
        d.get("is_video")
        or domain in ("v.redd.it", "youtube.com", "youtu.be")
        or hint in ("hosted:video", "rich:video")
    ):
        return "video"
    if hint == "image" or domain == "i.redd.it" or d.get("is_gallery"):
        return "image"
    if "progress" in flair or "news" in flair:
        return "progress"
    return "discussion"


def _finish(item: dict[str, Any], via: str, completeness: str, sub: str, pid: str) -> dict[str, Any]:
    item.update({"via": via, "vias": [via], "completeness": completeness, "post_id": pid, "subreddit": sub})
    return item


# ---------------------------------------------------------------- archive JSON (Arctic Shift, PullPush)


def from_post_dict(d: dict[str, Any], via: str, now=None) -> dict[str, Any] | None:
    """One Reddit post object (the shape Arctic Shift and PullPush return) → normalised item, or None."""
    now = now or now_utc()
    if not isinstance(d, dict) or d.get("over_18"):
        return None
    sub, pid = d.get("subreddit") or "", str(d.get("id") or "").lower().removeprefix("t3_")
    if not sub or not pid or sub.lower() not in _allowed_subs():
        return None
    texts = [d.get("title", ""), (d.get("selftext") or "")[:3000], d.get("link_flair_text") or ""]
    if not relevant(texts):
        return None
    url = canonical(sub, pid)
    link = d.get("url_overridden_by_dest") or d.get("url") or ""
    urls = [link] if link and not post_id(link) and not link.startswith("/") else []
    thumb = None
    try:
        thumb = d["preview"]["images"][0]["resolutions"][-1]["url"].replace("&amp;", "&")
    except (KeyError, IndexError, TypeError):
        if str(d.get("thumbnail") or "").startswith("https://"):
            thumb = d["thumbnail"]
    yt = YT_ID.search(link)
    created = parse_time(d.get("created_utc"))
    age_h = (now - created).total_seconds() / 3600 if created else 0
    item = make_item(
        source="reddit",
        source_label=f"r/{sub}",
        url=url,
        created=d.get("created_utc"),
        kind=_kind(d),
        texts=texts,
        urls=urls,
        score=d.get("score"),
        comments=d.get("num_comments"),
        spoiler_flag=bool(d.get("spoiler")),
        by_owner=(d.get("author") or "").lower() in _owners(),
        thumb=thumb,
        embed=yt.group(1) if yt else None,
    )
    # Archived at posting time: score/comments only become final ~36 h later.
    completeness = "full" if age_h >= _cfg()["full_after_hours"] else "partial"
    return _finish(item, via, completeness, sub, pid)


def _archive_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError(f"unexpected format: {str(payload)[:120]}")
    return payload["data"]


def collect_arcticshift(http: Http) -> dict[str, Any]:
    cfg = _cfg()["layers"]["arcticshift"]
    if not cfg.get("enabled"):
        return {"items": [], "count": 0, "seen": "disabled"}
    base = cfg["url"]
    urls: list[str] = []
    for sub in _cfg()["subreddits"]:
        urls.append(f"{base}?subreddit={sub}&after={cfg['after']}&limit={cfg['limit']}&sort=desc")
        for term in _cfg()["terms"][: cfg["term_queries"]]:
            urls.append(
                f"{base}?subreddit={sub}&query={quote(term)}&after={cfg['after']}&limit={cfg['limit']}&sort=desc"
            )
    items: dict[str, dict[str, Any]] = {}
    ok, errors, last = 0, 0, None
    for url in urls[: cfg["max_requests"]]:
        for attempt in range(2):
            try:
                rows = _archive_rows(http.get_json(url, retries=0))
                ok += 1
                for d in rows:
                    it = from_post_dict(d, "arcticshift")
                    if it:
                        items[it["post_id"]] = it
                break
            except HttpError as exc:
                last = exc
                if exc.status == 422 and attempt == 0:  # "Timeout. Maybe slow down a bit"
                    if not http.offline:
                        time.sleep(cfg["timeout_retry_wait_s"])
                    continue
                errors += 1
                if exc.status == 429:  # rate limited: stop for this run
                    break
                break
            except ValueError as exc:
                last = exc
                errors += 1
                break
        if isinstance(last, HttpError) and last.status == 429:
            break
    if ok == 0:
        raise HttpError(getattr(last, "status", 0) or 0, "arcticshift", f"(no successful query: {last})")
    return _result(list(items.values()), note=f"{ok} ok / {errors} failed queries" if errors else None)


def collect_pullpush(http: Http, health: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = _cfg()["layers"]["pullpush"]
    if not cfg.get("enabled"):
        return {"items": [], "count": 0, "seen": "disabled"}
    paused = (health or {}).get("paused_until")
    now = now_utc()
    if paused and parse_time(paused) and parse_time(paused) > now:
        raise SourcePaused(paused, (health or {}).get("pause_reason") or "refused automated access")
    after = int((now - timedelta(hours=cfg["hours"])).timestamp())
    items: dict[str, dict[str, Any]] = {}
    ok, last = 0, None
    for sub in _cfg()["subreddits"]:
        url = f"{cfg['url']}?subreddit={sub}&after={after}&size={cfg['size']}&sort=desc&sort_type=created_utc"
        try:
            rows = _archive_rows(http.get_json(url, retries=0))
        except HttpError as exc:
            if exc.status in (403, 429):
                until = iso(now + timedelta(hours=cfg["pause_hours_on_refusal"]))
                raise SourcePaused(until, f"HTTP {exc.status}: refuses automated clients") from exc
            last = exc
            continue
        except ValueError as exc:
            last = exc
            continue
        ok += 1
        for d in rows:
            it = from_post_dict(d, "pullpush")
            if it:
                items[it["post_id"]] = it
    if ok == 0:
        raise HttpError(getattr(last, "status", 0) or 0, "pullpush", f"(no successful query: {last})")
    return _result(list(items.values()))


# ---------------------------------------------------------------- Reddit RSS


def from_rss(text: str, via: str = "rss", completeness: str = "partial") -> list[dict[str, Any]]:
    feed = feedparser.parse(text)
    out = []
    for e in feed.entries:
        pid = post_id(e.get("link", ""))
        if not pid or pid[0].lower() not in _allowed_subs():
            continue
        sub = _allowed_subs()[pid[0].lower()]
        summary = e.get("summary") or ""
        if e.get("content"):
            summary = " ".join(c.get("value", "") for c in e["content"]) or summary
        texts = [e.get("title", ""), re.sub(r"<[^>]+>", " ", summary)[:3000]]
        if not relevant(texts):
            continue
        links = re.findall(r'href="(https?://[^"]+)"', summary)
        media = next((u for u in links if re.search(r"i\.redd\.it|v\.redd\.it|youtu", u)), "")
        kind = (
            "video" if re.search(r"v\.redd\.it|youtu", media) else ("image" if "i.redd.it" in media else "discussion")
        )
        thumb = (e.get("media_thumbnail") or [{}])[0].get("url") if e.get("media_thumbnail") else None
        yt = YT_ID.search(media)
        item = make_item(
            source="reddit",
            source_label=f"r/{sub}",
            url=canonical(sub, pid[1]),
            created=e.get("published") or e.get("updated"),
            kind=kind,
            texts=texts,
            urls=[u for u in links if not post_id(u) and "reddit.com/user" not in u][:3],
            thumb=thumb,
            embed=yt.group(1) if yt else None,
        )
        out.append(_finish(item, via, completeness, sub, pid[1]))
    return out


def collect_rss(http: Http) -> dict[str, Any]:
    cfg = _cfg()["layers"]["rss"]
    if not cfg.get("enabled"):
        return {"items": [], "count": 0, "seen": "disabled"}
    urls = []
    for sub in _cfg()["subreddits"]:
        for term in _cfg()["terms"]:
            urls.append(cfg["search"].format(sub=sub, q=quote(term)))
        urls.append(cfg["new"].format(sub=sub))
    items: dict[str, dict[str, Any]] = {}
    ok, blocks_in_row, blocked, last = 0, 0, [], None
    for url in urls[: cfg["max_requests"]]:
        try:
            text = http.get(url, retries=0).text
        except HttpError as exc:
            last = exc
            if exc.status in (403, 429):
                blocks_in_row += 1
                blocked.append(str(exc.status))
                if blocks_in_row >= cfg["stop_after_blocks"]:
                    log.warning("reddit rss: %d blocks in a row, stopping for this run", blocks_in_row)
                    break
            continue
        blocks_in_row = 0
        ok += 1
        for it in from_rss(text):
            items[it["post_id"]] = it
    if ok == 0:
        detail = ", ".join(sorted(set(blocked))) or str(last)
        raise HttpError(
            int(blocked[-1]) if blocked else 0, "https://www.reddit.com", f"(blocked from this runner: {detail})"
        )
    return _result(list(items.values()), note=("blocked: " + ", ".join(sorted(set(blocked)))) if blocked else None)


# ---------------------------------------------------------------- Bing discovery


def from_bing(text: str) -> list[dict[str, Any]]:
    feed = feedparser.parse(text)
    out = []
    for e in feed.entries:
        pid = post_id(e.get("link", ""))
        if not pid or pid[0].lower() not in _allowed_subs():
            continue
        if not e.get("published") and not e.get("updated"):
            continue  # undated → cannot be placed in time; skipped
        sub = _allowed_subs()[pid[0].lower()]
        texts = [e.get("title", ""), re.sub(r"<[^>]+>", " ", e.get("summary") or "")[:1000]]
        if not relevant(texts):
            continue
        item = make_item(
            source="reddit",
            source_label=f"r/{sub}",
            url=canonical(sub, pid[1]),
            created=e.get("published") or e.get("updated"),
            kind="discussion",
            texts=texts,
        )
        out.append(_finish(item, "bing", "partial", sub, pid[1]))
    return out


def collect_bing(http: Http) -> dict[str, Any]:
    cfg = _cfg()["layers"]["bing"]
    if not cfg.get("enabled"):
        return {"items": [], "count": 0, "seen": "disabled"}
    items: dict[str, dict[str, Any]] = {}
    ok, last = 0, None
    for q in cfg["queries"]:
        try:
            text = http.get(cfg["url"].format(q=quote(q)), retries=0).text
        except HttpError as exc:
            last = exc
            if exc.status in (403, 429):
                break
            continue
        ok += 1
        for it in from_bing(text):
            items[it["post_id"]] = it
    if ok == 0:
        raise HttpError(getattr(last, "status", 0) or 0, "bing", f"(no successful query: {last})")
    return _result(list(items.values()))


# ---------------------------------------------------------------- merge + freshness


def _result(items: list[dict[str, Any]], note: str | None = None) -> dict[str, Any]:
    return {"items": items, "count": len(items), "lag_h": lag_hours(items), "seen": note}


def lag_hours(items: list[dict[str, Any]], now=None) -> float | None:
    """Mean age, in hours, of the 10 newest items of a layer (how far behind it runs)."""
    now = now or now_utc()
    ages = sorted(
        (now - parse_time(i["created"])).total_seconds() / 3600 for i in items if parse_time(i.get("created"))
    )
    return round(mean(ages[:10]), 1) if ages else None


def _rank(it: dict[str, Any]) -> tuple:
    return (it.get("completeness") == "full", -PREFER.get(it.get("via", ""), 9), it.get("score") or 0)


def merge(layers: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Deduplicate by post id. The most complete record wins (full > partial, then archive > RSS > Bing), and the
    highest score/comment counts seen in any layer are kept, together with every layer that saw the post."""
    by_id: dict[str, dict[str, Any]] = {}
    for layer in layers:
        for it in layer:
            pid = it["post_id"]
            cur = by_id.get(pid)
            if cur is None:
                by_id[pid] = dict(it)
                continue
            best, other = (it, cur) if _rank(it) > _rank(cur) else (cur, it)
            merged = dict(best)
            merged["vias"] = sorted(set(cur.get("vias", [])) | set(it.get("vias", [])))
            for k in ("score", "comments"):
                vals = [v for v in (cur.get(k), it.get(k)) if isinstance(v, int)]
                merged[k] = max(vals) if vals else None
            for k in ("thumb", "embed"):
                merged[k] = best.get(k) or other.get(k)
            if merged.get("kind") == "discussion" and other.get("kind") in ("image", "video"):
                merged["kind"] = other["kind"]
            merged["_texts"] = best.get("_texts") or other.get("_texts", [])
            merged["_urls"] = sorted(set(best.get("_urls", [])) | set(other.get("_urls", [])))
            merged["_spoiler_flag"] = bool(best.get("_spoiler_flag") or other.get("_spoiler_flag"))
            by_id[pid] = merged
    return list(by_id.values())


def freshness(items: list[dict[str, Any]], now=None) -> dict[str, Any]:
    """Newest Reddit item and whether the data runs late (> stale_after_hours, or nothing at all)."""
    now = now or now_utc()
    dates = [parse_time(i.get("created")) for i in items if i.get("source") == "reddit"]
    dates = [d for d in dates if d]
    if not dates:
        return {"newest": None, "lag_h": None, "stale": True}
    newest = max(dates)
    lag = round((now - newest).total_seconds() / 3600, 1)
    return {"newest": iso(newest), "lag_h": lag, "stale": lag > _cfg()["stale_after_hours"]}
