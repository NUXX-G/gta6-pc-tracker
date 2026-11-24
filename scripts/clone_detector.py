"""Clone / trap detection for discovered candidates and popular forks.

A repo is flagged "possible clone or malware" when:
  * its README is >= `similarity_threshold` similar to a tracked project's README and it is NOT a declared GitHub fork, or
  * it shows a strong download signal (binary releases, file-hosting links, a password), or
  * it has no real code and shows at least 2 weak signals (setup/launcher wording, links outside the allowlist), or
  * it is on the manual blocklist.
Flagged repos are never linked: the site shows the name as plain text with a warning.
"""

from __future__ import annotations

import difflib
import re
from typing import Any

from common import config, domain_allowed
from legal_filter import allowlist

SIMILARITY_THRESHOLD = 0.60
WEAK_SIGNALS_NEEDED = 2
BINARY_EXT = re.compile(r"\.(exe|zip|rar|7z|iso|msi|dll|bin|apk|scr)$", re.IGNORECASE)
HOSTING = re.compile(r"mega\.nz|mediafire|gofile|pixeldrain|1fichier|drive\.google|dropbox|magnet:|torrent", re.I)
PASSWORD = re.compile(r"\b(password|contraseña|passwd)\b", re.IGNORECASE)
SETUP = re.compile(r"\b(setup\.exe|installer|launcher|instalador)\b", re.IGNORECASE)
LINK = re.compile(r"https?://[^\s)<>\"']+", re.IGNORECASE)
# Links that are normal in a legit technical README even though we do not link them ourselves.
BENIGN_LINK_DOMAINS = [
    "discord.gg",
    "discord.com",
    "img.shields.io",
    "shields.io",
    "githubusercontent.com",
    "llvm.org",
    "vulkan.lunarg.com",
    "learn.microsoft.com",
    "microsoft.com",
    "khronos.org",
    "rust-lang.org",
    "python.org",
    "dotnet.microsoft.com",
    "visualstudio.microsoft.com",
    "gpuopen.com",
    "amd.com",
    "nvidia.com",
    "wikipedia.org",
    "freedesktop.org",
    "x.com",
    "twitter.com",
    "youtube.com",
    "youtu.be",
    "github.io",
]


def _shingles(text: str, k: int = 5) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(words[i : i + k]) for i in range(max(0, len(words) - k + 1))}


def similarity(a: str, b: str) -> float:
    """Max of shingle Jaccard and a difflib quick ratio on normalised text (0..1)."""
    if not a or not b:
        return 0.0
    sa, sb = _shingles(a), _shingles(b)
    jacc = len(sa & sb) / len(sa | sb) if sa and sb else 0.0
    # Word-level diff (fast): at most the first 1,500 words of each README.
    na = re.findall(r"[a-z0-9]+", a.lower())[:1500]
    nb = re.findall(r"[a-z0-9]+", b.lower())[:1500]
    ratio = difflib.SequenceMatcher(None, na, nb).ratio() if na and nb else 0.0
    return max(jacc, ratio)


def _blocked(full_name: str) -> bool:
    bl = config("blocklist") or {}
    name = full_name.lower()
    owner = name.split("/")[0]
    return name in {r.lower() for r in bl.get("repos") or []} or owner in {o.lower() for o in bl.get("owners") or []}


def analyse(candidate: dict[str, Any], tracked_readmes: dict[str, str]) -> dict[str, Any]:
    """candidate: {full_name, readme, fork (bool), parent (str|None), release_assets: [names]}."""
    reasons: list[dict[str, Any]] = []
    full_name = candidate["full_name"]
    readme = candidate.get("readme") or ""

    if _blocked(full_name):
        reasons.append({"code": "blocklist"})

    best_repo, best_sim = None, 0.0
    for repo, text in tracked_readmes.items():
        if repo.lower() == full_name.lower():
            continue
        s = similarity(readme, text)
        if s > best_sim:
            best_repo, best_sim = repo, s
    if best_sim >= SIMILARITY_THRESHOLD and not candidate.get("fork"):
        reasons.append({"code": "similar", "pct": round(best_sim * 100), "repo": best_repo})

    assets = candidate.get("release_assets") or []
    if any(BINARY_EXT.search(a) for a in assets):
        reasons.append({"code": "binaries"})
    if HOSTING.search(readme):
        reasons.append({"code": "hosting"})
    if PASSWORD.search(readme):
        reasons.append({"code": "password"})

    weak = []
    if SETUP.search(readme):
        weak.append({"code": "setup"})
    allowed = allowlist()
    external = [
        u for u in LINK.findall(readme) if not domain_allowed(u, allowed) and not domain_allowed(u, BENIGN_LINK_DOMAINS)
    ]
    if len(external) >= 2:
        weak.append({"code": "external"})
    # Weak signals only count for repos without real code (README-only lures).
    if len(weak) >= WEAK_SIGNALS_NEEDED and not candidate.get("has_code", False):
        reasons.extend(weak)

    return {
        "full_name": full_name,
        "flagged": bool(reasons),
        "reasons": reasons,
        "similarity": round(best_sim, 3),
        "similar_to": best_repo,
    }
