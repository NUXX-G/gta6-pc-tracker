"""Normalised external items. Raw texts live in private '_texts' and are stripped before saving."""

from __future__ import annotations

import re
from typing import Any

import bleach

from common import config, iso, parse_time, sha

GTA_RE = re.compile(r"\bgta ?(6|vi)\b|\bgta6\b|grand theft auto (6|vi)\b", re.IGNORECASE)


def clean(text: str | None, limit: int = 4000) -> str:
    """Strip every tag; we never insert external HTML."""
    if not text:
        return ""
    return bleach.clean(str(text)[:limit], tags=[], attributes={}, strip=True).strip()


def relevant(texts: list[str]) -> bool:
    joined = " ".join(texts).lower()
    terms = config("settings")["social"]["relevance_terms"]
    return bool(GTA_RE.search(joined)) and any(t in joined for t in terms)


def detect_project(texts: list[str], urls: list[str]) -> str | None:
    joined = (" ".join(texts) + " " + " ".join(urls)).lower()
    best = None
    for p in config("projects")["projects"]:
        names = {p["name"].lower(), p["repo"].lower(), p["slug"].replace("-", " ")}
        if p["slug"] == "victory":
            names.add("victory")
        for n in names:
            if re.search(rf"(?<![\w]){re.escape(n)}(?![\w])", joined):
                # The bare word 'victory' is common; require a port context for it.
                if n == "victory" and not re.search(r"\bvictory\b.{0,60}\b(port|pc|repo|github|emulat|layer)", joined):
                    continue
                return p["slug"]
    return best


def make_item(
    *,
    source: str,
    source_label: str,
    url: str,
    created: Any,
    kind: str,
    texts: list[str],
    urls: list[str] | None = None,
    score: int | None = None,
    comments: int | None = None,
    spoiler_flag: bool = False,
    by_owner: bool = False,
    lang: str | None = None,
) -> dict[str, Any]:
    texts = [clean(t) for t in texts if t]
    urls = [u for u in (urls or []) if u]
    dt = parse_time(created)
    return {
        "id": sha(source + url),
        "source": source,
        "source_label": clean(source_label, 80),
        "url": url,
        "created": iso(dt),
        "kind": kind,
        "project": detect_project(texts, [url, *urls]),
        "score": score,
        "comments": comments,
        "lang": lang,
        "by_owner": by_owner,
        "_texts": texts,
        "_urls": urls,
        "_spoiler_flag": spoiler_flag,
    }


def public(item: dict[str, Any]) -> dict[str, Any]:
    """Drop private fields (raw third-party text) before saving or publishing."""
    return {k: v for k, v in item.items() if not k.startswith("_")}
