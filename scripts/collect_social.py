"""Free, key-less backups: Hacker News (Algolia), Bluesky (public search), Mastodon (hashtag), Lemmy (search).

Each sub-source reports its own health; run_all calls them separately.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from urllib.parse import quote

from bs4 import BeautifulSoup

from common import Http, HttpError, config, now_utc
from items import make_item, relevant


def _cfg() -> dict[str, Any]:
    return config("settings")["social"]


def _all_failed(errors: int, total: int, name: str, last: HttpError | None) -> None:
    if total and errors == total:
        raise HttpError(last.status if last else 0, name, "(all queries failed)")


def collect_hn(http: Http) -> dict[str, Any]:
    since = int((now_utc() - timedelta(days=7)).timestamp())
    items: dict[str, Any] = {}
    errors, last = 0, None
    for q in _cfg()["hn_queries"]:
        try:
            res = http.get_json(
                f"https://hn.algolia.com/api/v1/search_by_date?query={quote(q)}&tags=story"
                f"&numericFilters=created_at_i>{since}&hitsPerPage=50"
            )
        except HttpError as exc:
            errors, last = errors + 1, exc
            continue
        for h in res.get("hits", []):
            texts = [h.get("title") or "", (h.get("story_text") or "")[:3000]]
            if not relevant(texts):
                continue
            it = make_item(
                source="hn",
                source_label="Hacker News",
                url=f"https://news.ycombinator.com/item?id={h['objectID']}",
                created=h.get("created_at"),
                kind="discussion",
                texts=texts,
                urls=[h.get("url") or ""],
                score=h.get("points"),
                comments=h.get("num_comments"),
            )
            items[it["id"]] = it
    _all_failed(errors, len(_cfg()["hn_queries"]), "hn", last)
    return {"items": list(items.values()), "count": len(items)}


def collect_bluesky(http: Http) -> dict[str, Any]:
    items: dict[str, Any] = {}
    errors, last = 0, None
    for q in _cfg()["bluesky_queries"]:
        try:
            res = http.get_json(
                f"https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts?q={quote(q)}&limit=50&sort=latest"
            )
        except HttpError as exc:
            errors, last = errors + 1, exc
            continue
        for p in res.get("posts", []):
            rec = p.get("record", {})
            texts = [rec.get("text", "")]
            if not relevant(texts):
                continue
            handle = p.get("author", {}).get("handle", "")
            rkey = p.get("uri", "").rsplit("/", 1)[-1]
            ext = ((rec.get("embed") or {}).get("external") or {}).get("uri", "")
            it = make_item(
                source="bluesky",
                source_label="Bluesky",
                url=f"https://bsky.app/profile/{handle}/post/{rkey}",
                created=rec.get("createdAt"),
                kind="discussion",
                texts=texts,
                urls=[ext],
                score=p.get("likeCount"),
                comments=p.get("replyCount"),
                spoiler_flag=bool(p.get("labels")),
            )
            items[it["id"]] = it
    _all_failed(errors, len(_cfg()["bluesky_queries"]), "bluesky", last)
    return {"items": list(items.values()), "count": len(items)}


def collect_mastodon(http: Http) -> dict[str, Any]:
    items: dict[str, Any] = {}
    errors, last = 0, None
    for tag in _cfg()["mastodon_tags"]:
        try:
            res = http.get_json(f"https://mastodon.social/api/v1/timelines/tag/{quote(tag)}?limit=40")
        except HttpError as exc:
            errors, last = errors + 1, exc
            continue
        for s in res:
            text = BeautifulSoup(s.get("content", ""), "html.parser").get_text(" ")
            texts = [text]
            if not relevant(texts):
                continue
            acct = s.get("account", {}).get("acct", "")
            card = s.get("card") or {}
            it = make_item(
                source="mastodon",
                source_label="Mastodon",
                url=f"https://mastodon.social/@{acct}/{s['id']}",
                created=s.get("created_at"),
                kind="image" if s.get("media_attachments") else "discussion",
                texts=texts,
                urls=[card.get("url", "")],
                score=s.get("favourites_count"),
                comments=s.get("replies_count"),
                spoiler_flag=bool(s.get("sensitive") or s.get("spoiler_text")),
            )
            items[it["id"]] = it
    _all_failed(errors, len(_cfg()["mastodon_tags"]), "mastodon", last)
    return {"items": list(items.values()), "count": len(items)}


def collect_lemmy(http: Http) -> dict[str, Any]:
    items: dict[str, Any] = {}
    errors, last = 0, None
    for q in _cfg()["lemmy_queries"]:
        try:
            res = http.get_json(f"https://lemmy.world/api/v3/search?q={quote(q)}&type_=Posts&sort=New&limit=40")
        except HttpError as exc:
            errors, last = errors + 1, exc
            continue
        for pv in res.get("posts", []):
            post = pv.get("post", {})
            if post.get("nsfw"):
                continue
            texts = [post.get("name", ""), (post.get("body") or "")[:3000]]
            if not relevant(texts):
                continue
            counts = pv.get("counts", {})
            it = make_item(
                source="lemmy",
                source_label="Lemmy",
                url=f"https://lemmy.world/post/{post['id']}",
                created=post.get("published"),
                kind="discussion",
                texts=texts,
                urls=[post.get("url") or ""],
                score=counts.get("score"),
                comments=counts.get("comments"),
            )
            items[it["id"]] = it
    _all_failed(errors, len(_cfg()["lemmy_queries"]), "lemmy", last)
    return {"items": list(items.values()), "count": len(items)}
