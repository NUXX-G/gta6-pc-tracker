"""README helpers: extract the status table and normalise its rows to neutral canonical stages.

Raw README text is never published: rows are reduced to (canonical stage, state, hash).
"""

from __future__ import annotations

import re
from typing import Any

from common import config, sha

_HEADING_MD = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_HEADING_HTML = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE | re.DOTALL)
_STATUS_WORDS = re.compile(r"\b(status|estado)\b", re.IGNORECASE)
_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _split_row(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|"):
        line = line[:-1]
    cells = re.split(r"(?<!\\)\|", line)
    return [c.strip() for c in cells]


def _is_heading(line: str) -> str | None:
    m = _HEADING_MD.match(line)
    if m:
        return m.group(2)
    m = _HEADING_HTML.search(line)
    if m:
        return re.sub(r"<[^>]+>", "", m.group(2))
    return None


def extract_status_table(markdown: str) -> dict[str, Any] | None:
    """First markdown table located under a heading that contains 'Status' or 'Estado'.

    Returns {"header": [...], "rows": [[...], ...]} or None.
    """
    lines = markdown.splitlines()
    under_status = False
    i = 0
    while i < len(lines):
        heading = _is_heading(lines[i])
        if heading is not None:
            under_status = bool(_STATUS_WORDS.search(heading))
            i += 1
            continue
        if under_status and "|" in lines[i] and i + 1 < len(lines) and _SEPARATOR.match(lines[i + 1]):
            header = _split_row(lines[i])
            rows: list[list[str]] = []
            j = i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                rows.append(_split_row(lines[j]))
                j += 1
            if rows:
                return {"header": header, "rows": rows}
            i = j
            continue
        i += 1
    return None


def _strip_md(text: str) -> str:
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"[*_`]", "", text)
    return re.sub(r"<[^>]+>", " ", text).strip()


def canonical_stage(text: str) -> str:
    low = _strip_md(text).lower()
    for rule in config("scoring")["table_stages"]:
        if re.search(rule["re"], low):
            return rule["stage"]
    return "other"


def canonical_state(text: str) -> str:
    low = _strip_md(text).lower()
    rules = config("scoring")["table_states"]
    # The opening words (or emoji) of a cell say the most; fall back to the whole cell.
    for window in (low[:40], low):
        for rule in rules:
            if re.search(rule["re"], window):
                return rule["state"]
    return "partial"


def normalise_table(table: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not table:
        return []
    header = [h.lower() for h in table["header"]]
    state_col = next(
        (i for i, h in enumerate(header) if re.search(r"state|status|estado|today", h) and i > 0),
        1 if len(header) > 1 else 0,
    )
    out = []
    for row in table["rows"]:
        if not row:
            continue
        stage_text = row[0]
        state_text = row[state_col] if state_col < len(row) else ""
        out.append(
            {
                "stage": canonical_stage(stage_text),
                "state": canonical_state(state_text),
                "hash": sha(stage_text + "|" + state_text),
            }
        )
    return out


STATE_LEVEL = {"no": 0, "partial": 1, "ok": 2}


def compare_rows(prev: list[dict[str, Any]], cur: list[dict[str, Any]]) -> tuple[list[str], list[str]]:
    """Return (improved stages, worsened stages) comparing rows matched by stage + occurrence index."""

    def keyed(rows: list[dict[str, Any]]) -> dict[tuple[str, int], str]:
        seen: dict[str, int] = {}
        out = {}
        for r in rows:
            n = seen.get(r["stage"], 0)
            seen[r["stage"]] = n + 1
            out[(r["stage"], n)] = r["state"]
        return out

    a, b = keyed(prev), keyed(cur)
    improved, worsened = [], []
    for key, state in b.items():
        if key in a and STATE_LEVEL[state] > STATE_LEVEL[a[key]]:
            improved.append(key[0])
        elif key in a and STATE_LEVEL[state] < STATE_LEVEL[a[key]]:
            worsened.append(key[0])
    return improved, worsened
