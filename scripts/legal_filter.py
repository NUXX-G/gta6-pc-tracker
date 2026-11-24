"""Legal filter: applied to every external text and URL before anything is stored or published.

An item is dropped WHOLE if any text or URL hits a blocked pattern, or if any URL is outside the allowlist.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from common import config, domain_allowed, domain_of

BLOCKED_SUBSTRINGS: list[str] = [
    "torrent",
    "magnet:",
    "mega.nz",
    "mediafire",
    "gofile",
    "pixeldrain",
    "1fichier",
    "drive.google",
    "dropbox",
    "cyberleek.",
    "sha256",
    "sha-256",
]
BLOCKED_WORDS: list[str] = ["download", "descargar", "descarga", "password", "contraseña", "crack"]
BLOCKED_EXTENSIONS: list[str] = [".exe", ".zip", ".rar", ".7z", ".iso"]

_WORD_RE = re.compile(r"(?<![a-z])(" + "|".join(re.escape(w) for w in BLOCKED_WORDS) + r")", re.IGNORECASE)
_EXT_RE = re.compile(r"(" + "|".join(re.escape(e) for e in BLOCKED_EXTENSIONS) + r")(?![a-z0-9])", re.IGNORECASE)
_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def allowlist() -> list[str]:
    allowed = list(config("allowlist")["domains"])
    blocked = set(config("blocklist").get("domains") or [])
    return [d for d in allowed if d not in blocked]


def text_violation(text: str | None) -> str | None:
    """Return the reason a text is not publishable, or None."""
    if not text:
        return None
    low = text.lower()
    for s in BLOCKED_SUBSTRINGS:
        if s in low:
            return f"blocked:{s}"
    m = _WORD_RE.search(low)
    if m:
        return f"blocked-word:{m.group(1)}"
    m = _EXT_RE.search(low)
    if m:
        return f"blocked-ext:{m.group(1)}"
    for url in _URL_RE.findall(text):
        if not domain_allowed(url, allowlist()):
            return f"domain:{domain_of(url)}"
    return None


def url_violation(url: str | None) -> str | None:
    if not url:
        return "empty-url"
    if not domain_allowed(url, allowlist()):
        return f"domain:{domain_of(url)}"
    return text_violation(url)


def item_violation(texts: Iterable[str | None], urls: Iterable[str | None]) -> str | None:
    for u in urls:
        reason = url_violation(u)
        if reason:
            return reason
    for t in texts:
        reason = text_violation(t)
        if reason:
            return reason
    return None


def is_allowed(texts: Iterable[str | None], urls: Iterable[str | None]) -> bool:
    return item_violation(texts, urls) is None


def filter_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Items carry '_texts' (raw texts, never published) and 'url' / '_urls'."""
    kept: list[dict[str, Any]] = []
    dropped = 0
    for it in items:
        urls = [it.get("url"), *it.get("_urls", [])]
        if item_violation(it.get("_texts", []), urls) is None:
            kept.append(it)
        else:
            dropped += 1
    return kept, dropped
