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


# ------------------------------------------------------------------ v2: media, trackers, private projects, new pages


def test_readme_media_skips_badges_and_logos_and_resolves_relative_paths():
    import collect_media

    media = collect_media.media_from_readme(README, "Example/Repo", "main", "victory", "2026-11-24T10:00:00Z")
    thumbs = [m["thumb"] for m in media if m["thumb"]]
    assert "https://raw.githubusercontent.com/Example/Repo/main/docs/img/menus.png" in thumbs
    assert not any("shields.io" in t or "logo" in t for t in thumbs)
    kinds = {m["kind"] for m in media}
    assert "video" in kinds  # user-attachments clip and the YouTube link
    assert any(m.get("embed") == "AbCdEfGhIjK" for m in media)
    assert all(m["credibility"] == "confirmed" and m["origin"] == "project" for m in media)


def test_youtube_parser_reads_ids_and_relative_dates():
    import collect_media

    videos = collect_media.parse_youtube((FIX / "youtube.html").read_text(encoding="utf-8"))
    assert [v["video_id"] for v in videos] == ["Vid0000001A", "Vid0000002B", "Vid0000003C"]
    assert videos[0]["created"] == "2026-11-24T09:00:00Z"


def test_tracker_project_lists_and_merge():
    from bs4 import BeautifulSoup

    import collect_trackers
    from common import load_json

    race = collect_trackers.projects_from_race(
        BeautifulSoup((FIX / "vuyp.html").read_text(encoding="utf-8"), "html.parser")
    )
    by_name = {p["name"]: p for p in race}
    assert by_name["CGBJ"]["repo"] is None and by_name["CGBJ"]["stage"] == 6
    assert by_name["VIctory"]["repo"] == "marvisak/VIctory" and by_name["VIctory"]["stage"] == 5
    assert by_name["g6shim"]["watch"] is True
    pw = collect_trackers.projects_from_portwatch(load_json(FIX / "portwatch.json"))
    rows = collect_trackers.merge_external(
        [{"name": "Race", "projects": race}, {"name": "PW", "projects": pw}],
        run_all.config("projects")["projects"],
    )
    victory = next(r for r in rows if r["slug"] == "victory")
    assert victory["stages"] == {"Race": 5, "PW": 5}
    assert not any(r["name"] == "CGBJ" for r in rows)  # config/blocklist.yml → names
    assert any(r["slug"] is None and r["repo"] == "newdev/scarlett-runtime" for r in rows)


def test_private_project_has_no_repo_link_and_a_private_signal():
    victory = next(p for p in run_all.config("projects")["projects"] if p["slug"] == "victory")
    cfg = dict(
        victory,
        name="Private",
        slug="private-build",
        repo=None,
        kind="private",
        link="https://portwatch-vi.xyz/news/example.html",
    )
    res = analyze.analyse_project(cfg, {"slug": "private-build", "repo": None, "private": True}, None, None, [], {})
    assert res["status"] == "private" and res["private"]
    assert res["url"].startswith("https://portwatch-vi.xyz/")
    assert any(s["code"] == "private" for s in res["signals_bad"])
    assert res["estimates"] and not res["estimates"][0].get("stale")


def test_news_relevance_is_whole_word_and_needs_pc_context():
    import collect_news
    from items import relevant

    assert not relevant(["GTA 6 on PS Portal is great"])
    assert relevant(["GTA 6 PC port progress"])
    assert collect_news._relevant(["Unofficial GTA 6 PC port reaches the menus"])
    assert not collect_news._relevant(["GTA 6 runs at 60 fps on Xbox Series X", "PC owners wait"])
    assert not collect_news._relevant(["GTA 6 - How to make money fast in Port Gellhorn"])


def test_new_pages_exist_in_both_languages(site):
    for rel in [
        "es/como-funciona/index.html",
        "en/how-it-works/index.html",
        "es/descargas-falsas/index.html",
        "en/fake-downloads/index.html",
        "es/galeria/index.html",
        "en/gallery/index.html",
        "es/noticias/index.html",
        "en/news/index.html",
        "es/proyectos/victory/index.html",
        "en/projects/palmwave/index.html",
    ]:
        assert (site / rel).exists(), rel
    sitemap = (site / "sitemap.xml").read_text(encoding="utf-8")
    assert "como-funciona" in sitemap and "fake-downloads" in sitemap


def test_media_never_loads_external_previews_by_default(site):
    for rel in ["en/gallery/index.html", "es/index.html"]:
        text = (site / rel).read_text(encoding="utf-8")
        assert "<iframe" not in text
        for src in re.findall(r'<img[^>]+src="([^"]+)"', text):
            assert not src.startswith("http"), src
        assert "data-src=" in text
    gallery = (site / "en" / "gallery" / "index.html").read_text(encoding="utf-8")
    assert "From the project" in gallery and "cdn.example.com" not in gallery
    assert "Vid0000002B" not in gallery  # a "download" video is dropped by the legal filter
    assert "Vid0000003C" not in gallery  # a hoax ("fully playable") is never listed


def test_counters_and_structured_data(site):
    text = (site / "es" / "index.html").read_text(encoding="utf-8")
    assert 'data-since="2026-11-19T00:00:00Z"' in text and 'data-since="2026-11-23T14:36:00Z"' in text
    assert '"@type": "FAQPage"' in text and '"@type": "ItemList"' in text
    proj = (site / "en" / "projects" / "victory" / "index.html").read_text(encoding="utf-8")
    assert '"codeRepository": "https://github.com/Marvisak/VIctory"' in proj
    assert 'href="https://github.com/Marvisak/VIctory"' in proj
    assert not (site / "en" / "projects" / "cgbj").exists()


def test_press_headlines_shown_but_forum_titles_are_not(site):
    text = (site / "en" / "news" / "index.html").read_text(encoding="utf-8")
    assert "GTA 6 PC port progressing at breakneck speed" in text
    # a third-party headline always carries its outlet, a "third-party" tag and the external-link warning
    card = text[text.index("GTA 6 PC port progressing at breakneck speed") - 900 :]
    card = card[: card.index("</li>")]
    assert "Third-party" in card and "Original headline by" in card and "may contain spoilers" in card
    home = (site / "en" / "index.html").read_text(encoding="utf-8")
    assert "world now loads" not in home


def test_media_must_really_be_gta_vi():
    import collect_media

    generic = """# Some layer
## Tested games
### Minecraft Preview
![Minecraft in-world](docs/minecraft.png)
https://github.com/user-attachments/assets/11111111-2222-3333-4444-555555555555
### The Matrix Awakens
![Matrix chase](docs/matrix.png)
## A newer retail game
![Settings screen](docs/settings.png)
## GTA VI
![GTA VI start screen on PC](docs/gta.png)
![architecture](docs/architecture.png)
"""
    media = collect_media.media_from_readme(generic, "Some/Layer", "main", "xcl", None)
    assert [m["thumb"] for m in media] == ["https://raw.githubusercontent.com/Some/Layer/main/docs/gta.png"]
    # a GTA-only project keeps its screenshots without a caption, but never another game's
    only = "## Status\n![menus](a.png)\n### Minecraft test\n![x](b.png)\n"
    kept = collect_media.media_from_readme(only, "Marvisak/VIctory", "main", "victory", None)
    assert [m["thumb"].rsplit("/", 1)[1] for m in kept] == ["a.png"]


@pytest.mark.parametrize(
    "title,ok",
    [
        ("GTA 6 running on PC with VIctory: menus at 30 fps", True),
        ("Modders Got GTA 6 Booting on PC Overnight", True),
        ("GTA 6 PC Port: Private Build CGBJ Takes First Place", False),  # standings, not footage
        ("Is GTA 6 Really PORTED to PC? (fact checked)", False),
        ("GTA 6 EXE LEAKED, already being tested on PC", False),
        ("Minecraft running natively through XCL", False),
        ("GTA 6 gameplay trailer reaction", False),
        ("GTA 6 IS FREE ON PC!", False),
        ("How To Play GTA 6 on PC RIGHT NOW! (Full Guide)", False),
        ("Keanu in The Matrix Awakens on PC", False),
        ("GTA 5 vs GTA 6 on PC", False),
        ("GTA 6 money glitch", False),
    ],
)
def test_video_titles_are_filtered_strictly(title, ok):
    import collect_media

    assert collect_media.is_progress_footage([title]) is ok


def test_screenshots_from_project_posts():
    import collect_media

    posts = [
        {
            "url": "https://github.com/Marvisak/VIctory/pull/9",
            "title": "Fix mesh pipelines: world now renders",
            "body": "Before/after on an RX 6700 XT ![image](https://github.com/user-attachments/assets/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee)",
            "date": "2026-11-24T10:00:00Z",
            "trusted": True,
        },
        {
            "url": "https://github.com/Marvisak/VIctory/issues/3",
            "title": "Minecraft test crashes",
            "body": "![image](https://github.com/user-attachments/assets/11111111-bbbb-cccc-dddd-eeeeeeeeeeee)",
            "date": "2026-11-24T10:00:00Z",
            "trusted": False,
        },
        {
            "url": "https://github.com/Marvisak/VIctory/issues/4",
            "title": "Typo in docs",
            "body": "![image](https://github.com/user-attachments/assets/22222222-bbbb-cccc-dddd-eeeeeeeeeeee)",
            "date": "2026-11-24T10:00:00Z",
            "trusted": False,
        },
    ]
    media = collect_media.media_from_posts(posts, "victory", "Marvisak/VIctory")
    assert len(media) == 1 and media[0]["credibility"] == "confirmed"
    assert media[0]["thumb"].startswith("https://github.com/user-attachments/assets/aaaaaaaa")
    # a generic layer needs GTA named in the post
    assert collect_media.media_from_posts(posts[:1], "xcl", "ivall/xcl") == []


# ------------------------------------------------------------------ Reddit without the official API: four layers


class FakeHttp:
    """Answers by URL substring: a str body, a dict/list (JSON) or an int (HTTP error status). Records every call."""

    offline = True

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, headers=None, github=False, retries=None):
        import json

        from common import HttpError, Response

        self.calls.append(url)
        for key, answer in self.routes:
            if key in url:
                if isinstance(answer, int):
                    raise HttpError(answer, url)
                body = answer if isinstance(answer, str) else json.dumps(answer)
                return Response(200, url, body, {})
        raise HttpError(404, url)

    def get_json(self, url, **kw):
        return self.get(url, **kw).json()


def _fixture_json(name):
    import json

    return json.loads((FIX / name).read_text(encoding="utf-8"))


ARCTIC = _fixture_json("arcticshift.json")
RSS = (FIX / "reddit_search.rss").read_text(encoding="utf-8")
BING = (FIX / "bing.xml").read_text(encoding="utf-8")


@pytest.fixture()
def all_layers(monkeypatch):
    """Unit tests of every Reddit layer, including those switched off in config/sources.yml."""
    import copy

    import collect_reddit

    cfg = copy.deepcopy(collect_reddit._cfg())
    for layer in cfg["layers"].values():
        layer["enabled"] = True
    monkeypatch.setattr(collect_reddit, "_cfg", lambda: cfg)
    return cfg


def test_reddit_rss_layer_success_and_partial(all_layers):
    import collect_reddit

    res = collect_reddit.collect_rss(FakeHttp([("search.rss", RSS), ("new.rss", RSS)]))
    ids = {i["post_id"] for i in res["items"]}
    assert {"a9", "a2"} <= ids
    assert all(i["via"] == "rss" and i["completeness"] == "partial" and i["score"] is None for i in res["items"])
    assert next(i for i in res["items"] if i["post_id"] == "a2")["kind"] == "image"


def test_reddit_rss_stops_after_two_blocks_in_a_row(all_layers):
    import collect_reddit
    from common import HttpError

    http = FakeHttp([("reddit.com", 429)])
    with pytest.raises(HttpError):
        collect_reddit.collect_rss(http)
    assert len(http.calls) == 2  # two 403/429 in a row -> no more requests to Reddit this run
    http = FakeHttp([("search.rss?q=gta%206%20pc&", 403), ("search.rss", RSS), ("new.rss", 429)])
    res = collect_reddit.collect_rss(http)
    assert res["count"] >= 1 and "403" in res["seen"]


def test_arcticshift_layer_success_timeout_retry_and_bad_format():
    import collect_reddit
    from common import HttpError

    res = collect_reddit.collect_arcticshift(FakeHttp([("arctic-shift", ARCTIC)]))
    by_id = {i["post_id"]: i for i in res["items"]}
    assert by_id["a8"]["completeness"] == "full" and by_id["a8"]["score"] == 900  # 40 h old: final numbers
    assert by_id["a1"]["completeness"] == "partial"  # 0.5 h old: archived numbers are not settled yet
    assert by_id["a1"]["via"] == "arcticshift" and by_id["a1"]["kind"] == "video"
    assert res["lag_h"] is not None

    class Flaky(FakeHttp):
        def get(self, url, **kw):
            if not self.calls:
                self.calls.append(url)
                raise HttpError(422, url, "Timeout. Maybe slow down a bit")
            return super().get(url, **kw)

    flaky = Flaky([("arctic-shift", ARCTIC)])
    assert collect_reddit.collect_arcticshift(flaky)["count"] == res["count"]
    assert flaky.calls[0] == flaky.calls[1]  # the timed-out query was retried once

    with pytest.raises(HttpError):  # unexpected format on every query: the layer fails, nothing crashes
        collect_reddit.collect_arcticshift(FakeHttp([("arctic-shift", {"data": None, "error": "boom"})]))
    with pytest.raises(HttpError):
        collect_reddit.collect_arcticshift(FakeHttp([("arctic-shift", 429)]))


def test_pullpush_refusal_pauses_the_layer(all_layers):
    import collect_reddit
    from common import SourcePaused

    with pytest.raises(SourcePaused) as exc:
        collect_reddit.collect_pullpush(FakeHttp([("pullpush", 429)]))
    assert "HTTP 429" in exc.value.reason
    http = FakeHttp([("pullpush", ARCTIC)])
    with pytest.raises(SourcePaused):
        collect_reddit.collect_pullpush(http, {"paused_until": "2026-11-25T12:00:00Z"})
    assert http.calls == []  # while paused, PullPush is not called at all
    res = collect_reddit.collect_pullpush(FakeHttp([("pullpush", ARCTIC)]))
    assert res["count"] > 0 and all(i["via"] == "pullpush" for i in res["items"])


def test_bing_layer_keeps_only_allowed_reddit_threads(all_layers):
    import collect_reddit

    res = collect_reddit.collect_bing(FakeHttp([("bing.com", BING)]))
    assert sorted(i["post_id"] for i in res["items"]) == ["a1", "b1"]  # r/memes and example.com are dropped
    assert all(i["completeness"] == "partial" and i["via"] == "bing" for i in res["items"])


def test_merge_across_layers_keeps_the_most_complete_record(all_layers):
    import collect_reddit

    arctic = collect_reddit.collect_arcticshift(FakeHttp([("arctic-shift", ARCTIC)]))["items"]
    rss = collect_reddit.collect_rss(FakeHttp([("search.rss", RSS), ("new.rss", RSS)]))["items"]
    bing = collect_reddit.collect_bing(FakeHttp([("bing.com", BING)]))["items"]
    merged = {i["post_id"]: i for i in collect_reddit.merge([rss, arctic, bing])}
    assert len(merged) == len({i["post_id"] for i in arctic + rss + bing})  # one record per post id
    a1 = merged["a1"]
    assert a1["via"] == "arcticshift" and a1["vias"] == ["arcticshift", "bing"] and a1["score"] == 850
    a2 = merged["a2"]
    assert a2["vias"] == ["arcticshift", "rss"] and a2["kind"] == "image"
    assert merged["b1"]["completeness"] == "partial" and merged["b1"]["via"] == "bing"
    assert len({i["id"] for i in merged.values()}) == len(merged)  # same canonical URL -> same item id


def test_reddit_items_still_pass_legal_and_spoiler_filters():
    import collect_reddit

    posts = [dict(p) for p in ARCTIC["data"]]
    bad = next(p for p in posts if p["id"] == "a4")
    bad["url"] = "https://mega.nz/file/abc"  # a post linking a file host
    items = collect_reddit.collect_arcticshift(FakeHttp([("arctic-shift", {"data": posts})]))["items"]
    kept, dropped_legal = legal_filter.filter_items(items)
    kept, dropped_spoiler = spoiler_filter.filter_items(kept)
    ids = {i["post_id"] for i in kept}
    assert "a4" not in ids and dropped_legal >= 1  # prohibited link: dropped whole
    assert "a3" not in ids and "a6" not in ids and dropped_spoiler >= 2  # spoiler term / spoiler flag


def test_reddit_freshness_and_delayed_notice():
    import collect_reddit
    from common import parse_time

    now = parse_time("2026-11-24T12:00:00Z")
    fresh = collect_reddit.freshness([{"source": "reddit", "created": "2026-11-24T10:00:00Z"}], now)
    assert fresh == {"newest": "2026-11-24T10:00:00Z", "lag_h": 2.0, "stale": False}
    late = collect_reddit.freshness([{"source": "reddit", "created": "2026-11-24T04:00:00Z"}], now)
    assert late["stale"] and late["lag_h"] == 8.0
    assert collect_reddit.freshness([], now)["stale"]
    env = build_site.env()
    tpl = env.from_string('{% import "macros.html" as m %}{{ m.reddit_note(rs, T) }}')
    t = load_yaml(I18N_DIR / "es.yml")
    html = tpl.render(rs={"stale": True, "newest_rel": "hace 8 h"}, T=t, fmt=build_site.fmt)
    assert "Reddit: datos con retraso" in html
    html = tpl.render(rs={"stale": False, "newest_rel": "hace 2 h"}, T=t, fmt=build_site.fmt)
    assert "datos con retraso" not in html


def test_status_page_lists_each_reddit_layer(site):
    text = (site / "en" / "status" / "index.html").read_text(encoding="utf-8")
    for name in ["Reddit (RSS)", "Arctic Shift", "PullPush", "Bing (discovery)"]:
        assert name in text
    # layers switched off in config/sources.yml are shown as "off" with their reason, never as failing
    assert text.count(">off<") >= 3 and "Reddit blocks requests from GitHub Actions" in text
    assert "PullPush asks automated clients not to use it" in text
    assert "built-in method" not in text
    home = (site / "en" / "index.html").read_text(encoding="utf-8")
    assert "partial data" in home and "r/memes" not in home
