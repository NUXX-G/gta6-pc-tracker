"""Test suite: parsers, filters, clone detector, credibility, scoring, commit areas, signals, i18n, escaping and
the generated site (no spoiler terms, no non-allowlisted external URLs)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("TRACKER_NOW", "2026-11-24T12:00:00Z")

import analyze
import build_site
import clone_detector
import credibility
import legal_filter
import readme_parser
import run_all
import spoiler_filter
from collect_github import classify_commit
from common import I18N_DIR, ROOT, domain_allowed, load_yaml

FIX = ROOT / "tests" / "fixtures"


# ------------------------------------------------------------------ README table parser

README = (FIX / "readme.md").read_text(encoding="utf-8")


def test_extracts_first_table_under_status_heading():
    table = readme_parser.extract_status_table(README)
    assert table is not None
    assert table["header"] == ["Stage", "State"]
    assert len(table["rows"]) == 5


def test_ignores_tables_outside_status_heading():
    md = (
        "# Intro\n| a | b |\n|---|---|\n| x | y |\n## Estado\n| Etapa | Estado |\n|--|--|\n| Arranque | ✅ funciona |\n"
    )
    table = readme_parser.extract_status_table(md)
    assert table["rows"] == [["Arranque", "✅ funciona"]]


def test_no_status_table_returns_none():
    assert readme_parser.extract_status_table("# Hi\nno tables here") is None


def test_rows_are_normalised_to_neutral_stages():
    rows = readme_parser.normalise_table(readme_parser.extract_status_table(README))
    stages = [r["stage"] for r in rows]
    states = [r["state"] for r in rows]
    assert stages == ["boot", "video", "menus", "world", "world"]
    assert states == ["ok", "ok", "ok", "ok", "partial"]
    assert all("hash" in r and "text" not in r for r in rows)


def test_compare_rows_detects_improvement_and_regression():
    prev = [{"stage": "menus", "state": "partial"}, {"stage": "world", "state": "ok"}]
    cur = [{"stage": "menus", "state": "ok"}, {"stage": "world", "state": "no"}]
    up, down = readme_parser.compare_rows(prev, cur)
    assert up == ["menus"] and down == ["world"]


# ------------------------------------------------------------------ legal filter


@pytest.mark.parametrize(
    "text",
    [
        "grab it here magnet:?xt=urn:btih:abc",
        "mirror on https://mega.nz/file/xyz",
        "run GTA6.exe now",
        "Descargar GTA 6 PC gratis",
        "free download inside",
        "see https://example.org/page",
        "password is 1234",
        "sha256: deadbeef",
    ],
)
def test_legal_filter_blocks(text):
    assert legal_filter.text_violation(text) is not None


def test_legal_filter_blocks_urls_outside_allowlist():
    assert legal_filter.url_violation("https://evil.example.com/x") is not None
    assert legal_filter.url_violation("https://github.com/Marvisak/VIctory") is None
    assert legal_filter.url_violation("https://www.reddit.com/r/pcgaming/comments/1/x/") is None
    assert legal_filter.url_violation("https://github.com/x/y/releases/download/v1/a.zip") is not None


def test_legal_filter_allows_clean_text():
    assert legal_filter.text_violation("VIctory reaches the menus at 30 fps") is None


def test_filter_items_drops_whole_item():
    items = [
        {"url": "https://www.reddit.com/r/a/comments/1/x/", "_texts": ["ok text"], "_urls": []},
        {"url": "https://www.reddit.com/r/a/comments/2/x/", "_texts": ["ok"], "_urls": ["https://mediafire.com/x"]},
    ]
    kept, dropped = legal_filter.filter_items(items)
    assert len(kept) == 1 and dropped == 1


# ------------------------------------------------------------------ spoiler filter


def test_spoiler_terms_detected_on_word_boundaries():
    assert spoiler_filter.find_spoiler("thoughts on the ENDING")
    assert spoiler_filter.find_spoiler("la trama completa")
    assert spoiler_filter.find_spoiler("final mission walkthrough")
    assert not spoiler_filter.find_spoiler("the history of the port")
    assert not spoiler_filter.find_spoiler("pending review")
    assert not spoiler_filter.find_spoiler("Historial de cambios")


def test_spoiler_flag_discards_item():
    kept, dropped = spoiler_filter.filter_items([{"_texts": ["fine"], "_spoiler_flag": True}, {"_texts": ["fine"]}])
    assert len(kept) == 1 and dropped == 1


# ------------------------------------------------------------------ clone detector


def test_similar_readme_not_declared_fork_is_flagged():
    verdict = clone_detector.analyse(
        {"full_name": "shady/copy", "readme": README + "\nextra line", "fork": False, "release_assets": []},
        {"Marvisak/VIctory": README},
    )
    assert verdict["flagged"]
    assert any(r["code"] == "similar" for r in verdict["reasons"])


def test_declared_fork_with_same_readme_is_not_flagged():
    verdict = clone_detector.analyse(
        {"full_name": "fan/VIctory", "readme": README, "fork": True, "release_assets": []},
        {"Marvisak/VIctory": README},
    )
    assert not verdict["flagged"]


def test_binary_release_is_flagged():
    verdict = clone_detector.analyse(
        {"full_name": "x/y", "readme": "something new", "fork": True, "release_assets": ["GTA6_Setup.exe"]}, {}
    )
    assert verdict["flagged"] and verdict["reasons"][0]["code"] == "binaries"


def test_unrelated_readme_not_flagged():
    verdict = clone_detector.analyse(
        {"full_name": "x/y", "readme": "A Vulkan renderer for my own engine.", "fork": False, "release_assets": []},
        {"Marvisak/VIctory": README},
    )
    assert not verdict["flagged"]


# ------------------------------------------------------------------ credibility

TRACKED = ["Marvisak/VIctory", "ivall/xcl"]


def test_project_source_is_confirmed():
    assert credibility.classify({"source": "github", "_texts": []}, 5, TRACKED) == "confirmed"


def test_press_is_community():
    it = {"source": "news", "url": "https://vgtimes.com/x.html", "_texts": ["VIctory reaches menus, not playable yet"]}
    assert credibility.classify(it, 5, TRACKED) == "community"


def test_thread_linking_repo_is_community_and_anonymous_is_unverified():
    with_link = {
        "source": "reddit",
        "url": "https://www.reddit.com/r/a/1",
        "_texts": ["progress"],
        "_urls": ["https://github.com/Marvisak/VIctory"],
        "score": 5,
    }
    anon = {"source": "reddit", "url": "https://www.reddit.com/r/a/2", "_texts": ["it boots on my pc"], "score": 5}
    assert credibility.classify(with_link, 5, TRACKED) == "community"
    assert credibility.classify(anon, 5, TRACKED) == "unverified"


def test_rumour_plus_impossible_claim_is_false():
    it = {
        "source": "reddit",
        "url": "https://www.reddit.com/r/a/3",
        "_texts": ["Insider says the port is fully playable at 60 fps already"],
        "score": 30,
    }
    assert credibility.classify(it, 5, TRACKED) == "false"


def test_negated_claim_is_not_false():
    it = {"source": "reddit", "url": "https://www.reddit.com/r/a/4", "_texts": ["it is not playable yet"], "score": 1}
    assert credibility.classify(it, 5, TRACKED) == "unverified"


def test_claim_not_false_once_phase_is_reached():
    it = {"source": "reddit", "url": "https://www.reddit.com/r/a/5", "_texts": ["rumor: playable at 60 fps"]}
    assert credibility.classify(it, 6, TRACKED) != "false"


# ------------------------------------------------------------------ phases and percentage


def test_auto_phase_from_readme():
    rows = readme_parser.normalise_table(readme_parser.extract_status_table(README))
    assert analyze.auto_phase(README, rows) == {"level": 5, "partial": False}


def test_negated_boot_does_not_count():
    assert analyze.phase_from_text("Early. The game does not boot yet.") == 0


@pytest.mark.parametrize(
    "level,partial,expected",
    [
        (1, False, 20),
        (1, True, 10),
        (2, False, 25),
        (3, False, 40),
        (4, False, 55),
        (4, True, 47.5),
        (5, False, 70),
        (6, False, 80),
        (7, False, 90),
        (5, True, 62.5),
    ],
)
def test_scores(level, partial, expected):
    assert analyze.score_for(level, partial) == expected


def test_stable_is_100():
    assert analyze.score_for(7, False, stable=True) == 100


# ------------------------------------------------------------------ commit areas


def test_commit_classification_by_files_and_message():
    assert "gpu" in classify_commit("fix stuff", ["src/d3d12/device.rs"])
    assert "shaders" in classify_commit("translate wave64 ops in shader", [])
    assert "audio" in classify_commit("xma decode", [])
    assert "docs" in classify_commit("update", ["README.md"])
    assert classify_commit("misc", ["Cargo.lock"]) == ["other"]


# ------------------------------------------------------------------ signals


def _project(**gh):
    base = {
        "exists": True,
        "stars": 1000,
        "contributors": 10,
        "commits": [],
        "commits_24h": 0,
        "commits_7d": 0,
        "last_commit": "2026-11-24T11:00:00Z",
        "status_rows": [],
        "problem_issues": [],
        "releases": [],
        "_readme": README,
        "_issue_texts": [],
    }
    base.update(gh)
    return base


CFG = {"slug": "victory", "name": "VIctory", "repo": "Marvisak/VIctory", "phase": {"level": 5, "partial": False}}


def codes(p, kind):
    return [s["code"] for s in p["signals_" + kind]]


def test_good_signals_activity_and_stars_and_tracker():
    gh = _project(commits_24h=20, commits_7d=32, stars=1500)
    trackers = [{"name": "T", "url": "https://vuyp.github.io/gta6-pc-race/", "ok": True, "phases": {"victory": 5}}]
    p = analyze.analyse_project(CFG, gh, None, {"stars": 1000, "contributors": 10}, trackers, {})
    assert {"activity_up", "stars_up", "tracker_confirms"} <= set(codes(p, "good"))


def test_bad_signals_stale_crashes_missing_dmca():
    gh = _project(
        last_commit="2026-11-20T00:00:00Z",
        problem_issues=[{"number": i, "url": "u", "keywords": ["crash"]} for i in range(4)],
        releases=[{"url": "https://github.com/a/b/releases/1", "has_binaries": True}],
    )
    p = analyze.analyse_project(CFG, gh, None, None, [], {})
    assert {"stale", "crash_issues", "binaries"} <= set(codes(p, "bad"))
    gone = analyze.analyse_project(CFG, {"exists": False, "http_status": 404}, None, None, [], {})
    assert gone["status"] == "missing" and "missing" in codes(gone, "bad")
    dm = analyze.analyse_project(CFG, gh, None, None, [], {"victory": {"url": "https://github.com/github/dmca/x"}})
    assert dm["status"] == "dmca" and "dmca" in codes(dm, "bad")


def test_readme_row_changes_become_signals():
    prev = {"gh": {"status_rows": [{"stage": "world", "state": "no"}]}}
    gh = _project(status_rows=[{"stage": "world", "state": "partial"}])
    p = analyze.analyse_project(CFG, gh, prev, None, [], {})
    assert "rows_up" in codes(p, "good")


def test_pending_claim_when_readme_exceeds_manual_phase():
    cfg = dict(CFG, phase={"level": 3, "partial": False})
    p = analyze.analyse_project(cfg, _project(), None, None, [], {})
    assert p["claims"], "README suggests phase 5 while confirmed is 3"


# ------------------------------------------------------------------ i18n


def _keys(obj, prefix=""):
    out = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.add(f"{prefix}{k}")
            out |= _keys(v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        out.add(f"{prefix}[{len(obj)}]")
        for i, v in enumerate(obj):
            out |= _keys(v, f"{prefix}{i}.")
    return out


def test_i18n_keys_match():
    es, en = load_yaml(I18N_DIR / "es.yml"), load_yaml(I18N_DIR / "en.yml")
    assert _keys(es) - _keys(en) == set()
    assert _keys(en) - _keys(es) == set()


def test_projects_have_both_languages():
    for p in load_yaml(ROOT / "config" / "projects.yml")["projects"]:
        for f in ("goal", "approach", "technical"):
            assert p[f"{f}_es"] and p[f"{f}_en"]
        for s in p.get("strengths", []) + p.get("weaknesses", []):
            assert s["es"] and s["en"] and domain_allowed(s["evidence"], legal_filter.allowlist())


# ------------------------------------------------------------------ generated site


@pytest.fixture(scope="session")
def site(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("site")
    assert run_all.main(["--offline", "--out", str(out)]) == 0
    return out


def _html_files(site: Path):
    return [p for p in site.rglob("*") if p.suffix in (".html", ".xml")]


def test_site_has_expected_pages(site):
    for rel in [
        "index.html",
        "es/index.html",
        "en/index.html",
        "es/proyectos/victory/index.html",
        "en/projects/victory/index.html",
        "es/estado/index.html",
        "en/status/index.html",
        "es/feed.xml",
        "en/feed.xml",
        "sitemap.xml",
        "robots.txt",
        "404.html",
        "assets/og.png",
        "assets/css/site.css",
        "assets/js/site.js",
    ]:
        assert (site / rel).exists(), rel


def test_no_spoiler_terms_in_generated_html(site):
    for f in _html_files(site):
        text = f.read_text(encoding="utf-8")
        hit = spoiler_filter.find_spoiler(text)
        assert hit is None, f"{f}: {hit}"


def test_no_external_url_outside_allowlist(site):
    origin = build_site.site()["origin"]
    allowed = [*legal_filter.allowlist(), "schema.org", "www.sitemaps.org", "sitemaps.org", "www.w3.org", "w3.org"]
    for f in _html_files(site):
        text = f.read_text(encoding="utf-8")
        for url in re.findall(r"https?://[^\s\"'<>;&]+", text):
            if url.startswith(origin):
                continue
            assert domain_allowed(url, allowed), f"{f}: {url}"


def test_no_blocked_content_in_html(site):
    for f in site.rglob("*.html"):
        for href in re.findall(r'href="([^"]+)"', f.read_text(encoding="utf-8")):
            if href.startswith("http") and not href.startswith(build_site.site()["origin"]):
                assert legal_filter.url_violation(href) is None, href


def test_external_links_have_safe_rel(site):
    text = (site / "es" / "index.html").read_text(encoding="utf-8")
    for tag in re.findall(r"<a [^>]*href=\"https?://[^>]+>", text):
        assert 'target="_blank"' in tag and 'rel="noopener noreferrer nofollow"' in tag, tag


def test_csp_and_referrer_present(site):
    text = (site / "en" / "index.html").read_text(encoding="utf-8")
    assert "Content-Security-Policy" in text and "default-src 'self'" in text
    assert '<meta name="referrer" content="no-referrer">' in text
    assert "<script>" not in text  # no inline executable script


def test_flagged_clone_is_never_linked(site):
    text = (site / "en" / "index.html").read_text(encoding="utf-8")
    assert "shady/GTA6-PC-Port" in text
    assert "github.com/shady/GTA6-PC-Port" not in text


def test_hoax_is_struck_and_never_linked(site):
    text = (site / "en" / "index.html").read_text(encoding="utf-8")
    assert 'class="strike"' in text
    assert "comments/a5" not in text


def test_third_party_titles_are_not_shown(site):
    text = (site / "en" / "index.html").read_text(encoding="utf-8")
    assert "world now loads" not in text
    assert "Progress video · VIctory · r/pcgaming" in text


def test_base_path_everywhere(site):
    base = build_site.site()["base"]
    text = (site / "es" / "index.html").read_text(encoding="utf-8")
    assert f'href="{base}assets/css/site.css"' in text
    for href in re.findall(r'(?:href|src)="(/[^"]*)"', text):
        assert href.startswith(base), href
    assert f"{base}sitemap.xml" in (site / "robots.txt").read_text(encoding="utf-8")


def test_html_escaping(tmp_path):
    from jinja2 import Template  # noqa: F401

    env = build_site.env()
    tpl = env.from_string("<p>{{ x }}</p>")
    assert tpl.render(x='<script>alert(1)</script>"') == "<p>&lt;script&gt;alert(1)&lt;/script&gt;&#34;</p>"
    from items import clean

    assert clean("<b>hi</b><script>x</script>") == "hix"
