"""Shared helpers: paths, config loading, time, logging and a polite HTTP client."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
I18N_DIR = ROOT / "i18n"
FIXTURES_DIR = ROOT / "tests" / "fixtures"
SITE_DIR = ROOT / "_site"

log = logging.getLogger("tracker")


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def load_yaml(path: Path) -> Any:
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


_config_cache: dict[str, Any] = {}


def config(name: str) -> Any:
    """Load config/<name>.yml once."""
    if name not in _config_cache:
        _config_cache[name] = load_yaml(CONFIG_DIR / f"{name}.yml")
    return _config_cache[name]


def load_json(path: Path, default: Any = None) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=False)
        fh.write("\n")
    tmp.replace(path)


# ---------------------------------------------------------------- time


def now_utc() -> datetime:
    fixed = os.environ.get("TRACKER_NOW")  # tests / offline runs can pin the clock
    if fixed:
        return parse_time(fixed) or datetime.now(UTC)
    return datetime.now(UTC).replace(microsecond=0)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def parse_time(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), UTC)
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def hours_since(value: Any, now: datetime | None = None) -> float | None:
    dt = parse_time(value)
    if not dt:
        return None
    return ((now or now_utc()) - dt).total_seconds() / 3600


def within(value: Any, delta: timedelta, now: datetime | None = None) -> bool:
    dt = parse_time(value)
    return bool(dt and (now or now_utc()) - dt <= delta)


# ---------------------------------------------------------------- misc


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().removeprefix("www.")


def domain_allowed(url: str, allowed: list[str]) -> bool:
    host = domain_of(url)
    if not host or urlparse(url).scheme not in ("https", "http"):
        return False
    return any(host == d or host.endswith("." + d) for d in allowed)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------- HTTP


class HttpError(Exception):
    def __init__(self, status: int, url: str, message: str = "") -> None:
        super().__init__(f"HTTP {status} for {url} {message}".strip())
        self.status = status
        self.url = url


class OfflineMiss(HttpError):
    def __init__(self, url: str) -> None:
        super().__init__(0, url, "(no offline fixture)")


@dataclass
class Response:
    status: int
    url: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.text)


class Http:
    """requests wrapper: timeout, size cap, own UA, retries with backoff, per-host pacing.

    In offline mode every request is answered from tests/fixtures/index.yml (regex → file).
    """

    def __init__(self, offline: bool = False, token: str | None = None) -> None:
        cfg = config("settings")["http"]
        self.offline = offline
        self.token = token
        self.ua = cfg["user_agent"]
        self.timeout = cfg["timeout_s"]
        self.max_bytes = cfg["max_bytes"]
        self.retries = cfg["retries"]
        self.backoff = cfg["backoff_s"]
        self.intervals: dict[str, float] = cfg["min_interval_s"]
        self._last: dict[str, float] = {}
        self.calls = 0
        self._session = None
        self._fixtures: list[tuple[re.Pattern[str], dict[str, Any]]] = []
        if offline:
            index = load_yaml(FIXTURES_DIR / "index.yml") or []
            self._fixtures = [(re.compile(e["match"]), e) for e in index]

    @property
    def session(self):  # lazy import keeps offline tests fast
        if self._session is None:
            import requests

            self._session = requests.Session()
            self._session.headers["User-Agent"] = self.ua
        return self._session

    def _pace(self, host: str) -> None:
        interval = self.intervals.get(host, self.intervals.get("default", 0.5))
        wait = self._last.get(host, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last[host] = time.monotonic()

    def _offline(self, url: str) -> Response:
        for pattern, entry in self._fixtures:
            if pattern.search(url):
                status = int(entry.get("status", 200))
                text = ""
                if entry.get("file"):
                    text = (FIXTURES_DIR / entry["file"]).read_text(encoding="utf-8")
                if status >= 400:
                    raise HttpError(status, url)
                return Response(status, url, text, dict(entry.get("headers", {})))
        raise OfflineMiss(url)

    def get(self, url: str, headers: dict[str, str] | None = None, github: bool = False) -> Response:
        self.calls += 1
        if self.offline:
            return self._offline(url)
        import requests

        hdrs = dict(headers or {})
        if github:
            hdrs.setdefault("Accept", "application/vnd.github+json")
            hdrs.setdefault("X-GitHub-Api-Version", "2022-11-28")
            if self.token:
                hdrs["Authorization"] = f"Bearer {self.token}"
        host = urlparse(url).hostname or ""
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._pace(host)
            try:
                with self.session.get(url, headers=hdrs, timeout=self.timeout, stream=True) as r:
                    body = bytearray()
                    for chunk in r.iter_content(65536):
                        body.extend(chunk)
                        if len(body) > self.max_bytes:
                            raise HttpError(r.status_code, url, "(response too large)")
                    text = body.decode(r.encoding or "utf-8", "replace")
                    resp_headers = {k.lower(): v for k, v in r.headers.items()}
                    if r.status_code == 429 or (r.status_code >= 500 and attempt < self.retries):
                        retry_after = resp_headers.get("retry-after", "")
                        delay = float(retry_after) if retry_after.isdigit() else self.backoff * 2**attempt
                        if r.status_code == 429 and attempt >= self.retries:
                            raise HttpError(429, url, "(rate limited)")
                        time.sleep(min(delay, 30))
                        continue
                    if r.status_code == 403 and github and resp_headers.get("x-ratelimit-remaining") == "0":
                        raise HttpError(403, url, "(GitHub rate limit)")
                    if r.status_code >= 400:
                        raise HttpError(r.status_code, url)
                    return Response(r.status_code, r.url, text, resp_headers)
            except requests.RequestException as exc:
                last_exc = exc
                time.sleep(self.backoff * 2**attempt)
        raise HttpError(0, url, f"({type(last_exc).__name__ if last_exc else 'retries exhausted'})")

    def get_json(self, url: str, **kw: Any) -> Any:
        return self.get(url, **kw).json()
