"""Template-based ES/EN texts (no AI). Turns the analysed snapshot into a per-language view model.

Every external item gets a GENERATED title from metadata only, e.g.
"Vídeo de progreso · VIctory · r/pcgaming" / "Progress video · VIctory · r/pcgaming".
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from common import I18N_DIR, config, load_yaml, parse_time

_i18n: dict[str, dict[str, Any]] = {}


def i18n(lang: str) -> dict[str, Any]:
    if lang not in _i18n:
        _i18n[lang] = load_yaml(I18N_DIR / f"{lang}.yml")
    return _i18n[lang]


def fmt(template: str, **kw: Any) -> str:
    out = template
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def rel_time(T: dict[str, Any], value: Any, now: datetime) -> str:
    dt = parse_time(value)
    if not dt:
        return T["time"]["never"]
    mins = int((now - dt).total_seconds() // 60)
    if mins < 2:
        return T["time"]["just_now"]
    if mins < 60:
        return fmt(T["time"]["min_ago"], n=mins)
    if mins < 48 * 60:
        return fmt(T["time"]["h_ago"], n=mins // 60)
    return fmt(T["time"]["d_ago"], n=mins // 1440)


def phase_name(T: dict[str, Any], level: int) -> str:
    return T["phases"]["heist"].get(level, T["phases"]["heist"].get(str(level), "")) if level else "—"


def phase_tech(T: dict[str, Any], level: int) -> str:
    return T["phases"]["tech"].get(level, T["phases"]["tech"].get(str(level), "")) if level else "—"


def phase_short(T: dict[str, Any], phase: dict[str, Any]) -> str:
    label = fmt(T["phases"]["label"], n=phase["level"])
    if phase.get("partial"):
        label += f" ({T['phases']['partial']})"
    return label


def join_list(T: dict[str, Any], parts: list[str]) -> str:
    parts = [p for p in parts if p]
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + f" {T['summary']['and']} " + parts[-1]


def range_text(T: dict[str, Any], a: int, b: int) -> str:
    if b <= 14:
        return fmt(T["eta"]["range_days"], a=a, b=b)
    if b <= 90:
        return fmt(T["eta"]["range_weeks"], a=max(1, round(a / 7)), b=max(2, round(b / 7)))
    return fmt(T["eta"]["range_months"], a=max(1, round(a / 30)), b=max(2, round(b / 30)))


def item_title(T: dict[str, Any], item: dict[str, Any], names: dict[str, str]) -> str:
    kind = T["kinds"].get(item.get("kind", "discussion"), T["kinds"]["discussion"])
    project = names.get(item.get("project") or "", T["kinds"]["general"])
    return " · ".join(x for x in (kind, project, item.get("source_label", "")) if x)


def event_title(T: dict[str, Any], e: dict[str, Any], names: dict[str, str]) -> str:
    name = names.get(e.get("project", ""), "")
    k = e["kind"]
    if k == "commits":
        head = T["kinds"]["commit_one"] if e["n"] == 1 else fmt(T["kinds"]["commits"], n=e["n"])
        areas = ", ".join(T["areas"].get(a, a) for a in e.get("areas", []))
        return " · ".join(x for x in (head, name, areas) if x)
    if k in ("phase_up", "phase_down"):
        return f"{fmt(T['kinds'][k], **{'from': phase_short(T, e['from']), 'to': phase_short(T, e['to'])})} · {name}"
    if k == "status_change":
        return f"{fmt(T['kinds']['status_change'], status=T['states'].get(e['status'], e['status']))} · {name}"
    return f"{T['kinds'].get(k, k)} · {name}"


def now_line(T: dict[str, Any], p: dict[str, Any]) -> str:
    if p["status"] in ("missing", "dmca"):
        return T["summary"]["now_missing"]
    if not p["commits_7d"]:
        return T["summary"]["now_quiet"]
    areas = [T["areas"].get(a, a) for a in list(p["areas_7d"])[:2] if a != "other"] or [T["areas"]["other"]]
    return fmt(T["summary"]["now_active"], n7=p["commits_7d"], n24=p["commits_24h"], areas=join_list(T, areas))


def signal_text(T: dict[str, Any], s: dict[str, Any]) -> str:
    code = s["code"]
    if code in ("rows_up", "rows_down"):
        return fmt(T["signals"][code], rows=join_list(T, [T["stages"].get(r, r) for r in dict.fromkeys(s["rows"])]))
    if code == "missing":
        return fmt(T["signals"]["missing"], code=s.get("code_http", "?"))
    params = {k: v for k, v in s.items() if k not in ("code", "evidence")}
    return fmt(T["signals"][code], **params)


def gpu_vendors_ok(p: dict[str, Any]) -> list[str]:
    return [v for v, g in (p.get("gpu") or {}).items() if g.get("status") in ("ok", "ok_with_issues")]


def _project_static(slug: str) -> dict[str, Any]:
    return next(x for x in config("projects")["projects"] if x["slug"] == slug)


def project_view(T: dict[str, Any], lang: str, p: dict[str, Any], now: datetime) -> dict[str, Any]:
    cfg = _project_static(p["slug"])
    strengths = [{"text": s[lang], "evidence": s["evidence"], "manual": True} for s in cfg.get("strengths", [])]
    weaknesses = [{"text": s[lang], "evidence": s["evidence"], "manual": True} for s in cfg.get("weaknesses", [])]
    good = [{"text": signal_text(T, s), "evidence": s["evidence"], "manual": False} for s in p["signals_good"]]
    bad = [{"text": signal_text(T, s), "evidence": s["evidence"], "manual": False} for s in p["signals_bad"]]
    estimates = []
    for e in p["estimates"]:
        if e.get("stale"):
            estimates.append(
                {
                    "to": e["to"],
                    "name": phase_name(T, e["to"]),
                    "tech": phase_tech(T, e["to"]),
                    "range": "—",
                    "reason": T["eta"]["stale"],
                }
            )
            continue
        estimates.append(
            {
                "to": e["to"],
                "name": phase_name(T, e["to"]),
                "tech": phase_tech(T, e["to"]),
                "range": range_text(T, e["min_days"], e["max_days"]),
                "reason": fmt(
                    T["eta"]["reason"],
                    **{
                        "from": e["from"],
                        "to": e["to"],
                        "steps": e["steps"],
                        "per": e["per"],
                        "factor": e["factor"],
                        "activity": T["eta"]["activity_" + e["activity"]],
                    },
                ),
            }
        )
    claims = []
    for c in p["claims"]:
        claims.append(
            {
                "text": fmt(
                    T["summary"]["pending_claim_text"],
                    auto=phase_short(T, c["auto"]),
                    name=phase_name(T, c["auto"]["level"]),
                    phase=phase_short(T, c["phase"]),
                ),
                "evidence": c["evidence"],
            }
        )
    gh = p["gh"]
    return {
        **p,
        "goal": cfg[f"goal_{lang}"],
        "approach": cfg[f"approach_{lang}"],
        "technical": cfg[f"technical_{lang}"],
        "requirements": cfg.get(f"requirements_{lang}", ""),
        "phase_note": (cfg.get("phase") or {}).get(f"note_{lang}", ""),
        "phase_label": phase_short(T, p["phase"]),
        "phase_name": phase_name(T, p["phase"]["level"]),
        "phase_tech": phase_tech(T, p["phase"]["level"]),
        "now_line": now_line(T, p),
        "good": good + strengths,
        "bad": bad + weaknesses,
        "best": (good or strengths or [None])[0],
        "worst": (bad or weaknesses or [None])[0],
        "gpu_ok": gpu_vendors_ok(p),
        "estimates_view": estimates,
        "claims_view": claims,
        "status_label": T["states"].get(p["status"], p["status"]),
        "last_commit_rel": rel_time(T, gh.get("last_commit"), now),
        "areas_24h_view": [(T["areas"].get(a, a), n) for a, n in p["areas_24h"].items()],
        "areas_7d_view": [(T["areas"].get(a, a), n) for a, n in p["areas_7d"].items()],
        "rows_view": [(T["stages"].get(r["stage"], r["stage"]), r["state"]) for r in gh.get("status_rows", [])],
    }


def hero(T: dict[str, Any], leader: dict[str, Any] | None, max_reached: int) -> dict[str, str]:
    if leader and leader["phase"]["level"] == 7 and not leader["phase"]["partial"] and leader["score"] >= 100:
        return {"answer": T["hero"]["answer_yes"], "tone": "yes", "tagline": T["hero"]["tagline_yes"]}
    if leader and leader["phase"]["level"] == 7:
        return {"answer": T["hero"]["answer_partial"], "tone": "partial", "tagline": T["hero"]["tagline_partial"]}
    return {"answer": T["hero"]["answer_no"], "tone": "no", "tagline": fmt(T["hero"]["tagline_no"], phase=max_reached)}


def feed_entries(T: dict[str, Any], data: dict[str, Any], names: dict[str, str]) -> list[dict[str, Any]]:
    out = []
    for e in data["events"][:40]:
        out.append({"title": event_title(T, e, names), "url": e["url"], "created": e["created"], "id": e["id"]})
    for it in data["items"][:40]:
        if it.get("credibility") == "false":
            continue
        out.append({"title": item_title(T, it, names), "url": it["url"], "created": it["created"], "id": it["id"]})
    out.sort(key=lambda x: x["created"] or "", reverse=True)
    return out[:50]


def in_window(value: Any, hours: int, now: datetime) -> bool:
    dt = parse_time(value)
    return bool(dt and timedelta(0) <= now - dt <= timedelta(hours=hours)) or bool(
        dt and dt > now and dt - now < timedelta(minutes=10)
    )
