"""Regenerates tests/fixtures/ (offline mode). Run: python tests/make_fixtures.py"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 24, 12, tzinfo=UTC)


def w(name: str, obj) -> None:
    text = obj if isinstance(obj, str) else json.dumps(obj, indent=1)
    (HERE / name).write_text(text, encoding="utf-8", newline="\n")


def ts(hours: float) -> str:
    return (NOW - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%SZ")


def main() -> None:
    HERE.mkdir(exist_ok=True)
    w(
        "repo.json",
        {
            "full_name": "Example/Repo",
            "stargazers_count": 5000,
            "forks_count": 700,
            "open_issues_count": 40,
            "language": "Rust",
            "license": {"spdx_id": "GPL-3.0"},
            "pushed_at": ts(0.5),
            "created_at": "2026-11-10T10:00:00Z",
            "fork": False,
            "default_branch": "main",
            "archived": False,
        },
    )
    w("contributors.json", [{"login": "a"}])
    msgs = [
        (0.5, "d3d12: fix descriptor heap lifetimes"),
        (2, "shaders: translate wave64 compute ops"),
        (5, "audio: xma voice decode"),
        (20, "docs: update README status"),
        (40, "loader: tls callbacks"),
        (70, "perf: parallel shader cache"),
        (100, "input: xinput rumble"),
    ]
    commits = []
    for i, (h, msg) in enumerate(msgs):
        commits.append(
            {
                "sha": f"{i:02d}" + "ab" * 19,
                "html_url": f"https://github.com/Example/Repo/commit/{i:02d}",
                "commit": {"message": msg, "committer": {"date": ts(h)}, "author": {"date": ts(h)}},
            }
        )
    w("commits.json", commits)
    w("commit_detail.json", {"files": [{"filename": "src/gpu/d3d12/heap.rs"}, {"filename": "src/shaders/rdna.rs"}]})
    w("pulls_open.json", [{"number": 1}])
    w("pulls_closed.json", [{"number": 2, "merged_at": ts(4)}, {"number": 3, "merged_at": None}])
    w(
        "issues.json",
        [
            {
                "number": 10,
                "html_url": "https://github.com/Example/Repo/issues/10",
                "title": "TDR crash on NVIDIA RTX 3080 when entering world",
                "body": "black screen then device removed",
            },
            {
                "number": 11,
                "html_url": "https://github.com/Example/Repo/issues/11",
                "title": "Hang on RX 6800",
                "body": "hangs",
            },
            {
                "number": 12,
                "html_url": "https://github.com/Example/Repo/issues/12",
                "title": "Crash at boot",
                "body": "crash",
            },
            {
                "number": 13,
                "html_url": "https://github.com/Example/Repo/pull/13",
                "title": "PR",
                "body": "",
                "pull_request": {},
            },
        ],
    )
    w(
        "releases.json",
        [{"html_url": "https://github.com/Example/Repo/releases/tag/v0.1", "published_at": ts(96), "assets": []}],
    )
    readme = """# Example port

## Status (2026-11-23)

**Not playable yet.** The title boots and runs its menus at 30 fps.

| Stage | State |
|-------|-------|
| Boot, first-boot screens | works, 30 fps |
| Intro video with sound | works |
| Start screen, profile and saves | works |
| Loading into the world | works |
| The world | reached, the scene is still dark |

Tested on one machine: Windows 11, AMD RX 6700 XT. Also measured on an NVIDIA RTX 4090.

## License
GPL
"""
    w("readme.md", readme)
    w("forks.json", [{"full_name": "someone/Repo", "stargazers_count": 12}])
    w(
        "dmca_list.json",
        [
            {
                "name": "2026-11-23-take-two.md",
                "path": "2026/11/2026-11-23-take-two.md",
                "type": "file",
                "html_url": "https://github.com/github/dmca/blob/master/2026/11/2026-11-23-take-two.md",
            },
            {
                "name": "2026-11-22-other.md",
                "path": "2026/11/2026-11-22-other.md",
                "type": "file",
                "html_url": "https://github.com/github/dmca/blob/master/2026/11/2026-11-22-other.md",
            },
        ],
    )
    w(
        "dmca_notice.md",
        "Notice on behalf of Take-Two Interactive regarding Grand Theft Auto. "
        "Infringing: https://github.com/notreal/gta5-mobile\n",
    )
    w(
        "search.json",
        {
            "items": [
                {
                    "full_name": "newdev/scarlett-runtime",
                    "html_url": "https://github.com/newdev/scarlett-runtime",
                    "stargazers_count": 40,
                    "created_at": "2026-11-20T00:00:00Z",
                    "pushed_at": ts(2),
                    "language": "C++",
                    "size": 900,
                    "fork": False,
                },
                {
                    "full_name": "shady/GTA6-PC-Port",
                    "html_url": "https://github.com/shady/GTA6-PC-Port",
                    "stargazers_count": 15,
                    "created_at": "2026-11-23T00:00:00Z",
                    "pushed_at": ts(2),
                    "language": None,
                    "size": 2,
                    "fork": False,
                },
                {
                    "full_name": "bad/gta6-free-download",
                    "html_url": "https://github.com/bad/gta6-free-download",
                    "stargazers_count": 3,
                    "created_at": "2026-11-23T00:00:00Z",
                    "pushed_at": ts(2),
                    "language": None,
                    "size": 1,
                    "fork": False,
                },
            ]
        },
    )
    w("candidate_readme.md", "# Scarlett runtime\nA small experiment loading GDK executables.\n")
    w("clone_readme.md", readme + "\nGet the setup launcher here, password: 1234 https://example.org/x\n")
    w(
        "clone_releases.json",
        [
            {
                "html_url": "https://github.com/shady/GTA6-PC-Port/releases/tag/v1",
                "published_at": ts(20),
                "assets": [{"name": "GTA6_Setup.exe"}],
            }
        ],
    )

    def post(pid, title, sub, hours, score, comments, **kw):
        d = {
            "id": pid,
            "title": title,
            "selftext": "",
            "subreddit": sub,
            "permalink": f"/r/{sub}/comments/{pid}/x/",
            "created_utc": (NOW - timedelta(hours=hours)).timestamp(),
            "score": score,
            "num_comments": comments,
            "author": "someone",
            "domain": "self." + sub,
            "url": f"https://www.reddit.com/r/{sub}/comments/{pid}/x/",
            "over_18": False,
            "spoiler": False,
        }
        d.update(kw)
        return {"data": d}

    w(
        "reddit.json",
        {
            "data": {
                "children": [
                    post(
                        "a1",
                        "VIctory port progress: GTA 6 PC world now loads (github.com/Marvisak/VIctory)",
                        "pcgaming",
                        0.5,
                        850,
                        120,
                        is_video=True,
                        domain="v.redd.it",
                        url="https://v.redd.it/abc",
                    ),
                    post(
                        "a2",
                        "GTA 6 PC port screenshot from the XCL dev",
                        "GTA6",
                        3,
                        300,
                        45,
                        post_hint="image",
                        domain="i.redd.it",
                        url="https://i.redd.it/x.png",
                    ),
                    post("a3", "GTA 6 PC port: my thoughts on the ending", "GTA6", 2, 50, 10),
                    post("a4", "GTA 6 PC port full magnet link inside", "pcgaming", 1, 5, 2),
                    post(
                        "a5",
                        "Insider says GTA 6 PC port is fully playable at 60 fps already",
                        "GamingLeaksAndRumours",
                        4,
                        30,
                        25,
                    ),
                    post("a6", "GTA 6 PC port thread", "GTA6", 1, 20, 3, spoiler=True),
                    post("a7", "Which GPU for the GTA 6 PC recomp? NVIDIA wave64 discussion", "emulation", 10, 140, 60),
                ]
            }
        },
    )
    w(
        "hn.json",
        {
            "hits": [
                {
                    "objectID": "50612686",
                    "title": "GTA 6 PC recomp projects race after executable surfaces",
                    "created_at": ts(3),
                    "url": "https://www.xda-developers.com/gta-6-xbox-executable-leaked-opening-door-for-pc-recompilation-projects-begin-testing/",
                    "points": 293,
                    "num_comments": 180,
                },
                {
                    "objectID": "50600000",
                    "title": "Unrelated GTA 6 trailer talk",
                    "created_at": ts(4),
                    "url": "https://example.com",
                    "points": 10,
                    "num_comments": 2,
                },
            ]
        },
    )
    w(
        "mastodon.json",
        [
            {
                "id": "1",
                "content": "<p>GTA 6 PC port VIctory reaches menus</p>",
                "account": {"acct": "dev@example.social"},
                "created_at": ts(1),
                "sensitive": False,
                "spoiler_text": "",
                "media_attachments": [],
                "card": None,
                "favourites_count": 12,
                "replies_count": 3,
            }
        ],
    )
    w(
        "lemmy.json",
        {
            "posts": [
                {
                    "post": {
                        "id": 99,
                        "name": "GTA 6 PC port status thread (XCL, VIctory)",
                        "body": "",
                        "url": None,
                        "published": ts(5),
                        "nsfw": False,
                    },
                    "counts": {"score": 40, "comments": 12},
                }
            ]
        },
    )
    w(
        "gnews.xml",
        """<?xml version="1.0"?><rss version="2.0"><channel><title>GN</title>
<item><title>GTA 6 PC port VIctory reaches menus - VGTimes</title><link>https://news.google.com/rss/articles/CBMiAU_yqLOpaque?oc=5</link><pubDate>Tue, 24 Nov 2026 09:00:00 GMT</pubDate><source url="https://vgtimes.com">VGTimes</source></item>
</channel></rss>""",
    )
    w(
        "publisher.xml",
        """<?xml version="1.0"?><rss version="2.0"><channel><title>NBC</title>
<item><title>GTA 6 PC port progressing at breakneck speed</title><link>https://www.notebookcheck.net/GTA-6-PC-port-progressing-at-breakneck-speed.1458028.0.html</link><description>Projects such as VIctory and XCL keep moving on PC.</description><pubDate>Tue, 24 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>New laptop review</title><link>https://www.notebookcheck.net/laptop.html</link><pubDate>Tue, 24 Nov 2026 08:00:00 GMT</pubDate></item>
</channel></rss>""",
    )
    w(
        "vuyp.html",
        "<html><body><p>VIctory is furthest, at stage&nbsp;5&nbsp;of&nbsp;7.</p><p>XCL: stage 4 of 7</p>"
        "<time datetime='2026-11-24T11:49:00Z'>x</time></body></html>",
    )
    w(
        "portwatch.json",
        {
            "updated": "2026-11-24T11:41:43.000Z",
            "efforts": [
                {
                    "name": "VIctory",
                    "repo": "Marvisak/VIctory",
                    "gta": {"loads": 1, "boots": 1, "menu": 1, "ingame": 1, "fps": 1},
                },
                {"name": "XCL", "repo": "ivall/xcl", "gta": {"loads": 1, "boots": 1, "menu": 1, "ingame": 0}},
            ],
        },
    )
    index = [
        ("api\\.github\\.com/repos/github/dmca/contents/\\d{4}/\\d{2}$", {"file": "dmca_list.json"}),
        ("api\\.github\\.com/repos/github/dmca/contents/.+\\.md$", {"file": "dmca_notice.md"}),
        ("api\\.github\\.com/search/repositories", {"file": "search.json"}),
        ("api\\.github\\.com/repos/shady/GTA6-PC-Port/readme", {"file": "clone_readme.md"}),
        ("api\\.github\\.com/repos/shady/GTA6-PC-Port/releases", {"file": "clone_releases.json"}),
        ("api\\.github\\.com/repos/newdev/scarlett-runtime/readme", {"file": "candidate_readme.md"}),
        ("api\\.github\\.com/repos/LeonidaReforge/Leonida$", {"status": 404}),
        ("api\\.github\\.com/repos/[^/]+/[^/?]+$", {"file": "repo.json"}),
        (
            "/contributors\\?",
            {
                "file": "contributors.json",
                "headers": {"link": '<https://api.github.com/x?per_page=1&page=37>; rel="last"'},
            },
        ),
        ("/commits/[0-9a-f]{10,}$", {"file": "commit_detail.json"}),
        ("/commits\\?", {"file": "commits.json"}),
        (
            "/pulls\\?state=open",
            {
                "file": "pulls_open.json",
                "headers": {"link": '<https://api.github.com/x?per_page=1&page=14>; rel="last"'},
            },
        ),
        ("/pulls\\?state=closed", {"file": "pulls_closed.json"}),
        ("/issues\\?", {"file": "issues.json"}),
        ("/releases\\?", {"file": "releases.json"}),
        ("/readme$", {"file": "readme.md"}),
        ("/forks\\?", {"file": "forks.json"}),
        ("reddit\\.com/r/[^/]+/search\\.json", {"file": "reddit.json"}),
        ("reddit\\.com/.+\\.rss", {"status": 403}),
        ("hn\\.algolia\\.com", {"file": "hn.json"}),
        ("bsky\\.app", {"status": 403}),
        ("mastodon\\.social/api", {"file": "mastodon.json"}),
        ("lemmy\\.world/api", {"file": "lemmy.json"}),
        ("news\\.google\\.com", {"file": "gnews.xml"}),
        ("notebookcheck\\.net", {"file": "publisher.xml"}),
        ("videocardz\\.com|dsogaming\\.com", {"status": 403}),
        ("portwatch-vi\\.xyz/data/tracker\\.json", {"file": "portwatch.json"}),
        ("vuyp\\.github\\.io", {"file": "vuyp.html"}),
        ("mtwildwood", {"status": 403}),
    ]
    w("index.yml", [{"match": m, **e} for m, e in index])


if __name__ == "__main__":
    main()
