"""Media catalogue: images and videos from every origin, each labelled by where it comes from and how credible it is.

Origins (shown on the site as a tag next to the credibility badge):
  project   images/videos in a tracked project's own README          → confirmed
  tracker   images attached by an external tracker to its own posts  → community
  press     (reserved for press thumbnails; not collected for now)
  community Reddit / Mastodon / Lemmy posts that carry media         → classified like any community post
  video     YouTube search results                                    → classified like any community post

Nothing is embedded or loaded until the viewer clicks: we only store the page URL, the preview URL and,
for YouTube, the video id. Third-party text (alt texts, video titles) is kept private for filtering only.
"""

from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any
from urllib.parse import quote, urljoin

from common import Http, HttpError, config, domain_allowed, iso, log, now_utc, parse_time, sha
from items import GTA_RE

MD_IMG = re.compile(r"!\[([^\]]*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
HTML_IMG = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
HTML_ALT = re.compile(r"\balt=[\"']([^\"']*)[\"']", re.IGNORECASE)
GH_ASSET = re.compile(r"^\s*(https://github\.com/user-attachments/assets/[0-9a-f-]{36})\s*$", re.IGNORECASE | re.M)
YT_ID = re.compile(r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/|live/)|youtu\.be/)([\w-]{11})")
IMG_EXT = re.compile(r"\.(png|jpe?g|gif|webp|avif)(?:$|\?)", re.IGNORECASE)
REL_TIME = re.compile(
    r"(\d+)\s*(s|sec|second|m|min|minute|h|hr|hour|d|day|w|wk|week|mo|month|y|yr|year)s?\b", re.IGNORECASE
)
_UNIT_H = {"s": 0, "m": 1 / 60, "h": 1, "d": 24, "w": 168, "mo": 720, "y": 8760}


def _cfg() -> dict[str, Any]:
    return config("settings")["media"]


def _skip(url: str) -> bool:
    low = url.lower()
    return any(p in low for p in _cfg()["skip_image_patterns"]) or low.endswith(".svg")


def _resolve(src: str, repo: str, branch: str) -> str | None:
    src = src.strip()
    if src.startswith(("http://", "https://")):
        m = re.match(r"https://github\.com/([^/]+/[^/]+)/(?:blob|raw)/([^/]+)/(.+)$", src)
        if m:  # a blob link to an image in a repo → the raw file
            return f"https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3).split('?')[0]}"
        return src
    if src.startswith(("#", "data:", "mailto:")):
        return None
    base = f"https://raw.githubusercontent.com/{repo}/{branch}/"
    return urljoin(base, src.lstrip("/") if src.startswith("/") else src)


# ---------------------------------------------------------------- strict relevance: is this really GTA VI on PC?


def _words(terms: list[str]) -> re.Pattern[str]:
    return re.compile(r"(?<![\w])(?:" + "|".join(terms) + r")(?![\w])", re.IGNORECASE)


def _pc_re() -> re.Pattern[str]:
    names = [re.escape(p["name"].split(" (")[0]) for p in config("projects")["projects"]]
    return _words(list(config("settings")["news"]["context_terms"]) + names)


def other_game(texts: list[str]) -> str | None:
    """The first other-game or junk term found in these texts, else None."""
    cfg = _cfg()
    joined = " ".join(t for t in texts if t)
    m = _words(cfg["other_games"]).search(joined) or _words([re.escape(t) for t in cfg["junk_terms"]]).search(joined)
    return m.group(0) if m else None


def about_gta_pc(texts: list[str], need_gta: bool = True) -> bool:
    """True only if these texts name GTA VI (unless need_gta is False) AND are about PC / the ports,
    AND mention no other game or junk wording."""
    joined = " ".join(t for t in texts if t)
    if other_game(texts):
        return False
    if need_gta and not GTA_RE.search(joined):
        return False
    return bool(_pc_re().search(joined)) or not need_gta


def is_progress_footage(texts: list[str], need_gta: bool = True) -> bool:
    """Strictest check, for titles/captions: GTA VI + PC/ports + describes the game running + not commentary."""
    cfg = _cfg()
    joined = " ".join(t for t in texts if t)
    if not about_gta_pc(texts, need_gta=need_gta):
        return False
    if "?" in (texts[0] if texts else ""):
        return False
    if _words(cfg["commentary_terms"]).search(joined):
        return False
    return bool(_words(cfg["footage_terms"]).search(joined))


GH_IMG = re.compile(
    r"!\[([^\]]*)\]\((https://(?:github\.com/user-attachments/assets/[0-9a-f-]{36}|"
    r"user-images\.githubusercontent\.com/[^)\s]+|private-user-images\.githubusercontent\.com/[^)\s]+))\)"
    r"|<img\b[^>]*?src=[\"'](https://(?:github\.com/user-attachments/assets/[0-9a-f-]{36}|"
    r"user-images\.githubusercontent\.com/[^\"']+))[\"']",
    re.IGNORECASE,
)


def media_from_posts(posts: list[dict[str, Any]], slug: str, repo: str) -> list[dict[str, Any]]:
    """Screenshots/clips attached to a project's own PRs and issues, kept only when the post is about GTA VI
    progress on PC (title + body), names no other game and does not read as speculation."""
    gta_only = slug in _cfg().get("gta_only_projects", [])
    out: list[dict[str, Any]] = []
    for post in posts:
        title, body = post.get("title", ""), post.get("body", "")
        texts = [title, body[:1500]]
        if other_game([title, body[:1500]]):
            continue
        if not gta_only and not GTA_RE.search(title + " " + body[:1500]):
            continue
        if not _words(_cfg()["footage_terms"]).search(title + " " + body[:1500]):
            continue
        if _words(_cfg()["error_terms"]).search(title + " " + body[:1500]):
            continue  # a bug report, not a progress capture
        shots = []
        for m in GH_IMG.finditer(body):
            shots.append(("image", m.group(2) or m.group(3)))
        for m in GH_ASSET.finditer(body):
            shots.append(("video", m.group(1)))
        for kind, src in shots[:3]:
            out.append(
                {
                    "id": sha("post" + src),
                    "kind": kind,
                    "url": post["url"] if kind == "image" else src,
                    "thumb": src if kind == "image" else None,
                    "embed": None,
                    "source": "github",
                    "source_label": f"GitHub · {repo.split('/')[1]}",
                    "origin": "project",
                    "project": slug,
                    "credibility": "confirmed" if post.get("trusted") else "community",
                    "created": post.get("date"),
                    "_texts": texts,
                }
            )
    return out[:8]


def _context(readme: str, pos: int) -> tuple[str, str]:
    """(nearest heading above, the lines around) for a match at `pos`."""
    before = readme[:pos]
    heading = ""
    for line in reversed(before.splitlines()):
        st = line.strip()
        if st.startswith("#") or re.match(r"<h[1-4]\b", st, re.I):
            heading = re.sub(r"<[^>]+>", " ", st).lstrip("#").strip()
            break
    lines = readme.splitlines()
    idx = before.count(chr(10))
    # The line itself plus its neighbours, never crossing into another section (a heading line is a boundary).
    near = [lines[k] for k in (idx - 1, idx, idx + 1) if 0 <= k < len(lines)]
    near = [ln for ln in near if not re.match(r"\s*(#|<h[1-4]\b)", ln, re.I) or ln is lines[idx]]
    window = " ".join(near)
    return heading, re.sub(r"<[^>]+>", " ", window)


def media_from_readme(readme: str, repo: str, branch: str, slug: str, created: str | None) -> list[dict[str, Any]]:
    """Screenshots and videos a project shows in its own README, ONLY when they are about GTA VI.

    Generic layers show other games in their READMEs (Minecraft, demos...): an image is kept only if its alt text,
    its section heading or the lines around it name GTA VI (or the project is GTA-only), and none of them names
    another game. Diagrams, progress charts, logos and badges are skipped.
    """
    readme = readme or ""
    found: list[tuple[int, str, str, str]] = []  # (pos, kind, url, alt)
    for m in MD_IMG.finditer(readme):
        found.append((m.start(), "image", m.group(2), m.group(1)))
    for m in HTML_IMG.finditer(readme):
        alt = HTML_ALT.search(m.group(0))
        found.append((m.start(), "image", m.group(1), alt.group(1) if alt else ""))
    for m in GH_ASSET.finditer(readme):
        found.append((m.start(1), "video", m.group(1), ""))
    for m in YT_ID.finditer(readme):
        found.append((m.start(), "youtube", m.group(1), ""))
    found.sort()

    gta_only = slug in _cfg().get("gta_only_projects", [])
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    page = f"https://github.com/{repo}#readme"
    for pos, kind, src, alt in found:
        heading, window = _context(readme, pos)
        texts = [alt, heading, window]
        if other_game(texts) or not (gta_only or GTA_RE.search(" ".join(texts))):
            continue
        if kind == "youtube":
            item = _yt_item(src, source="github", label="README · " + repo.split("/")[1], created=created, texts=[alt])
            item.update({"origin": "project", "project": slug, "credibility": "confirmed"})
        else:
            url = _resolve(src, repo, branch)
            if not url or _skip(url) or url in seen:
                continue
            if kind == "image" and not IMG_EXT.search(url):
                continue
            seen.add(url)
            item = {
                "id": sha("readme" + url),
                "kind": kind,
                "url": url if kind == "video" else page,
                "thumb": url if kind == "image" else None,
                "embed": None,
                "source": "github",
                "source_label": "README · " + repo.split("/")[1],
                "origin": "project",
                "project": slug,
                "credibility": "confirmed",
                "created": created,
                "_texts": [alt],
            }
        out.append(item)
        if len(out) >= _cfg()["readme_max_per_project"]:
            break
    return out


def collect_readmes(gh_projects: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for slug, v in gh_projects.items():
        if not v.get("_readme") or not v.get("repo"):
            continue
        out += media_from_readme(v["_readme"], v["repo"], v.get("default_branch") or "main", slug, v.get("pushed_at"))
    for slug, v in gh_projects.items():
        if v.get("_posts") and v.get("repo"):
            out += media_from_posts(v["_posts"], slug, v["repo"])
    return out


# ---------------------------------------------------------------- YouTube (no key: the public results page)


def _yt_item(video_id: str, *, source: str, label: str, created: str | None, texts: list[str]) -> dict[str, Any]:
    return {
        "id": sha("yt" + video_id),
        "kind": "video",
        "url": f"https://www.youtube.com/watch?v={video_id}",
        "thumb": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        "embed": video_id,
        "source": source,
        "source_label": label,
        "created": created,
        "_texts": texts,
    }


def _rel_to_iso(text: str | None) -> str | None:
    if not text:
        return None
    m = REL_TIME.search(text)
    if not m:
        return None
    unit = m.group(2).lower()
    key = "mo" if unit.startswith("mo") else unit[0]
    if key == "m" and unit.startswith("min"):
        key = "m"
    hours = int(m.group(1)) * _UNIT_H.get(key, 24)
    return iso(now_utc() - timedelta(hours=hours))


def _walk(o: Any, out: list[dict[str, Any]]) -> None:
    if isinstance(o, dict):
        vr = o.get("videoRenderer")
        if isinstance(vr, dict) and vr.get("videoId"):
            out.append(vr)
        for v in o.values():
            _walk(v, out)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out)


def _runs(obj: Any) -> str:
    if not isinstance(obj, dict):
        return ""
    if "simpleText" in obj:
        return str(obj["simpleText"])
    return "".join(str(r.get("text", "")) for r in obj.get("runs", []) if isinstance(r, dict))


def parse_youtube(html: str) -> list[dict[str, Any]]:
    m = re.search(r"var ytInitialData\s*=\s*(\{.*?\});\s*</script>", html, re.S)
    if not m:
        raise ValueError("ytInitialData not found")
    renderers: list[dict[str, Any]] = []
    _walk(json.loads(m.group(1)), renderers)
    out = []
    for vr in renderers:
        title = _runs(vr.get("title"))
        channel = _runs(vr.get("ownerText"))
        desc = " ".join(
            _runs(s.get("snippetText")) for s in vr.get("detailedMetadataSnippets", []) if isinstance(s, dict)
        )
        out.append(
            {
                "video_id": vr["videoId"],
                "channel": channel[:60],
                "created": _rel_to_iso(_runs(vr.get("publishedTimeText"))),
                "texts": [title, desc, channel],
                "live": bool(vr.get("badges") and "LIVE" in json.dumps(vr.get("badges"))),
            }
        )
    return out


def collect_youtube(http: Http) -> dict[str, Any]:
    cfg = _cfg()
    items: dict[str, dict[str, Any]] = {}
    errors, last = 0, None
    max_age = timedelta(days=cfg["youtube_days"])
    for q in cfg["youtube_queries"]:
        try:
            html = http.get(
                f"https://www.youtube.com/results?search_query={quote(q)}&sp=CAI%253D",
                headers={"Accept-Language": "en-US,en;q=0.8", "Cookie": "CONSENT=YES+cb; SOCS=CAI"},
            ).text
            videos = parse_youtube(html)
        except (HttpError, ValueError) as exc:
            errors, last = errors + 1, exc
            log.warning("youtube %r: %s", q, exc)
            continue
        for v in videos[: cfg["youtube_max_per_query"]]:
            title, _desc, channel = v["texts"]
            # The TITLE itself must name GTA VI and be about PC/the ports; title + channel must name no other game.
            if not v["created"] or not is_progress_footage([title]) or other_game([title, channel]):
                continue
            if now_utc() - parse_time(v["created"]) > max_age:
                continue
            it = _yt_item(
                v["video_id"],
                source="youtube",
                label=f"YouTube · {v['channel']}",
                created=v["created"],
                texts=v["texts"],
            )
            items[it["id"]] = it
    if errors == len(cfg["youtube_queries"]):
        raise HttpError(getattr(last, "status", 0) or 0, "youtube", "(all queries failed)")
    return {"items": list(items.values()), "count": len(items)}


def finalize(entries: list[dict[str, Any]], allowed: list[str]) -> list[dict[str, Any]]:
    """Drop anything whose link or preview is outside the allowlist; fill defaults."""
    out = []
    for e in entries:
        if not e.get("url") or not domain_allowed(e["url"], allowed):
            continue
        if e.get("thumb") and not domain_allowed(e["thumb"], allowed):
            e["thumb"] = None
        e.setdefault("embed", None)
        e.setdefault("origin", "community")
        e.setdefault("project", None)
        out.append(e)
    return out
