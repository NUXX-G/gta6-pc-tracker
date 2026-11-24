"""Credibility classification (rules in config/credibility.yml, documented in the README)."""

from __future__ import annotations

import re
from typing import Any

from common import config, domain_allowed

LEVELS = ["confirmed", "community", "unverified", "false"]
EMOJI = {"confirmed": "🟢", "community": "🟡", "unverified": "🔴", "false": "⛔"}


def _cfg() -> dict[str, Any]:
    return config("credibility")


def has_rumour(text: str) -> bool:
    low = text.lower()
    return any(re.search(rf"(?<!\w){re.escape(t)}(?!\w)", low) for t in _cfg()["rumour_terms"])


def _negated(text: str, start: int) -> bool:
    cfg = _cfg()
    window = text[max(0, start - cfg["negation_window"]) : start].lower()
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", window) for n in cfg["negations"])


def impossible_claim(text: str, max_phase: int) -> str | None:
    """Return the matched claim if `text` asserts something no project can have reached, else None."""
    cfg = _cfg()
    if max_phase >= cfg["false_if_max_phase_below"]:
        return None
    for rule in cfg["impossible_claims"]:
        if rule["requires_phase"] <= max_phase:
            continue
        for m in re.finditer(rule["pattern"], text, re.IGNORECASE):
            if not _negated(text, m.start()):
                return m.group(0)
    return None


def classify(item: dict[str, Any], max_phase: int, tracked_repos: list[str]) -> str:
    """item: {source, url, _texts, _urls, score, by_owner, corroborated}."""
    cfg = _cfg()
    source = item.get("source", "")
    text = " ".join(t for t in item.get("_texts", []) if t)

    if source in cfg["project_sources"] or item.get("corroborated"):
        return "confirmed"
    if source in cfg["tracker_sources"]:
        return "confirmed" if item.get("corroborated") else "community"

    rumour = has_rumour(text)
    claim = impossible_claim(text, max_phase)

    if source in cfg["press_sources"] or domain_allowed(item.get("url", ""), cfg["press_domains"]):
        if claim and rumour:
            return "false"
        return "unverified" if (rumour or claim) else "community"

    # Community posts (reddit, hn, lemmy, bluesky, mastodon)
    tracked_low = [r.lower() for r in tracked_repos]
    links = [u for u in [item.get("url", ""), *item.get("_urls", [])] if u]
    links_repo = any(
        domain_allowed(u, cfg["evidence"]["link_domains"]) and any(r in u.lower() for r in tracked_low) for u in links
    )
    links_code = any(domain_allowed(u, cfg["evidence"]["link_domains"]) for u in links)
    # Evidence: posted by a project owner, links a tracked repo, or a well-voted post that links code.
    evidence = bool(
        item.get("by_owner") or links_repo or (links_code and (item.get("score") or 0) >= cfg["evidence"]["min_score"])
    )
    if claim and (rumour or not evidence):
        return "false"
    if rumour:
        return "unverified"
    return "community" if evidence else "unverified"


def apply(items: list[dict[str, Any]], max_phase: int, tracked_repos: list[str]) -> None:
    for it in items:
        it["credibility"] = classify(it, max_phase, tracked_repos)
