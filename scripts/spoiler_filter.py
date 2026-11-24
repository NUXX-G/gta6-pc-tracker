"""Spoiler filter: discards external items flagged as spoilers or containing a configured spoiler term."""

from __future__ import annotations

import re
from functools import lru_cache
from typing import Any

from common import config


@lru_cache(maxsize=1)
def spoiler_regex() -> re.Pattern[str]:
    terms = sorted({t.strip().lower() for t in config("spoiler_terms")["terms"] if t.strip()}, key=len, reverse=True)
    body = "|".join(re.escape(t).replace(r"\ ", r"\s+") for t in terms)
    return re.compile(rf"(?<![\w]){'(?:' + body + ')'}(?![\w])", re.IGNORECASE)


def find_spoiler(text: str | None) -> str | None:
    if not text:
        return None
    m = spoiler_regex().search(text)
    return m.group(0) if m else None


def is_spoiler(texts: list[str | None], flagged: bool = False) -> bool:
    if flagged:
        return True
    return any(find_spoiler(t) for t in texts)


def filter_items(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    kept, dropped = [], 0
    for it in items:
        if is_spoiler(it.get("_texts", []), bool(it.get("_spoiler_flag"))):
            dropped += 1
        else:
            kept.append(it)
    return kept, dropped
