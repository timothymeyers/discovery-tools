#!/usr/bin/env python3
"""Rate-limited HTTP client for the SABIO-RK Export API (`/export-api/sabio/...`).

Generalized, project-agnostic extraction of the client/rate-limiter/retry
logic validated in a prior ingestion pass (see
`../references/rate-limiting-and-retry-after.md` for the full access-route
history). No organism, EC number, or project identifier is hardcoded here --
callers supply a query string and cache key.

Distinguishes three outcomes on every call (never conflated):
  - "success"        -- HTTP 200, body parsed as JSON.
  - "confirmed_empty" -- caller-level concept: a well-formed 200 response
                         with zero matching entries. This module reports
                         "success" for any 200; callers decide "empty" from
                         the parsed body's own entry count.
  - "failure"        -- transport (network/5xx/other), authorization
                         (401/403), or parser (non-JSON / malformed body)
                         failure, always tagged with `failure_kind`.

Honors a server-supplied `Retry-After` header (RFC 9110: integer seconds or
an HTTP-date) on HTTP 429 before falling back to a computed backoff.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

USER_AGENT = "discovery-sabio-rk-kinetics-skill/1.0 (+https://github.com; academic use)"

# Documented, live-confirmed SABIO-RK Export API rate limit: 60 requests per
# 60-second window per client IP, shared across all `/export-api/` routes.
DEFAULT_RATE_LIMIT_REQUESTS = 60
DEFAULT_RATE_LIMIT_WINDOW_SECONDS = 60.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------
# Rate limiting
# --------------------------------------------------------------------------
class RateLimiter:
    """Sliding-window limiter: at most `max_requests` calls per `window` seconds.

    `time_fn`/`sleep_fn` are injectable so tests can run deterministically
    and instantly, with no real wall-clock sleeps.
    """

    def __init__(
        self,
        max_requests: int = DEFAULT_RATE_LIMIT_REQUESTS,
        window: float = DEFAULT_RATE_LIMIT_WINDOW_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.max_requests = max_requests
        self.window = window
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._calls: deque = deque()

    def acquire(self) -> None:
        now = self._time_fn()
        while self._calls and now - self._calls[0] >= self.window:
            self._calls.popleft()
        if len(self._calls) >= self.max_requests:
            wait_for = self.window - (now - self._calls[0])
            if wait_for > 0:
                self._sleep_fn(wait_for)
            now = self._time_fn()
            while self._calls and now - self._calls[0] >= self.window:
                self._calls.popleft()
        self._calls.append(self._time_fn())


# --------------------------------------------------------------------------
# HTTP layer (injectable for offline tests)
# --------------------------------------------------------------------------
class HttpResponse:
    def __init__(self, status: int, body: bytes, headers: Optional[Dict[str, str]] = None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


def default_http_get(url: str) -> HttpResponse:
    """Real network GET via urllib (stdlib only). Used only by the opt-in
    live contract probe -- all offline tests inject a fake instead."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return HttpResponse(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return HttpResponse(exc.code, body, dict(exc.headers or {}))


def parse_retry_after_seconds(headers: Dict[str, str]) -> Optional[float]:
    """Parse a `Retry-After` response header (case-insensitive lookup) per
    RFC 9110: either an integer/float number of seconds, or an HTTP-date.
    Returns None if absent/unparseable -- callers must fall back to their
    own default backoff, never crash."""
    value = None
    for key, val in (headers or {}).items():
        if key.lower() == "retry-after":
            value = val
            break
    if value is None:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        retry_dt = parsedate_to_datetime(value)
        if retry_dt.tzinfo is None:
            retry_dt = retry_dt.replace(tzinfo=timezone.utc)
        return max(0.0, (retry_dt - datetime.now(timezone.utc)).total_seconds())
    except (TypeError, ValueError):
        return None


class SabioClient:
    """Thin, rate-limited client over the SABIO-RK Export API with retry on
    HTTP 429 (honoring `Retry-After`) and raw-response caching + retrieval
    metadata for provenance. No base URL path segment, EC number, or
    organism is hardcoded -- every query is caller-supplied.
    """

    DEFAULT_BASE_URL = "https://sabiork.h-its.org/export-api/sabio"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        http_get: Callable[[str], HttpResponse] = default_http_get,
        rate_limiter: Optional[RateLimiter] = None,
        cache_dir: Optional[Path] = None,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_retries: int = 3,
    ):
        self.base_url = base_url.rstrip("/")
        self._http_get = http_get
        self.rate_limiter = rate_limiter or RateLimiter()
        self.cache_dir = cache_dir
        self._sleep_fn = sleep_fn
        self.max_retries = max_retries
        self.manifest: List[Dict[str, Any]] = []

    def get_json(
        self, path: str, params: Dict[str, Any], cache_key: Optional[str] = None
    ) -> Tuple[Optional[dict], Dict[str, Any]]:
        """GET `path` with query `params`. Returns (parsed_json_or_None, meta).

        `meta` always records url/http_status/attempts/retrieved_at plus an
        `outcome` ("success"/"failure") and, on failure, a `failure_kind`
        ("transport"/"authorization"/"parser") -- so callers can always tell
        a genuinely reachable-but-empty response apart from one that could
        not be confirmed at all.
        """
        query = urllib.parse.urlencode(params)
        url = f"{self.base_url}{path}?{query}" if query else f"{self.base_url}{path}"
        attempts = 0
        last_status = None
        response: Optional[HttpResponse] = None
        while attempts < self.max_retries:
            attempts += 1
            self.rate_limiter.acquire()
            response = self._http_get(url)
            last_status = response.status
            if response.status == 429:
                retry_after = parse_retry_after_seconds(response.headers)
                fallback_backoff = DEFAULT_RATE_LIMIT_WINDOW_SECONDS / DEFAULT_RATE_LIMIT_REQUESTS + 0.05
                self._sleep_fn(retry_after if retry_after is not None else fallback_backoff)
                continue
            break

        meta: Dict[str, Any] = {
            "url": url,
            "http_status": last_status,
            "attempts": attempts,
            "retrieved_at": _utc_now_iso(),
        }
        parsed = None
        if response is not None and response.status == 200:
            try:
                parsed = response.json()
                meta["outcome"] = "success"
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                meta["parse_error"] = str(exc)
                meta["body_snippet"] = response.body[:500].decode("utf-8", errors="replace")
                meta["outcome"] = "failure"
                meta["failure_kind"] = "parser"
        elif response is not None:
            meta["body_snippet"] = response.body[:500].decode("utf-8", errors="replace")
            meta["outcome"] = "failure"
            meta["failure_kind"] = "authorization" if response.status in (401, 403) else "transport"
        else:
            meta["outcome"] = "failure"
            meta["failure_kind"] = "transport"

        if self.cache_dir is not None and cache_key:
            self._write_cache(cache_key, parsed, meta)
        self.manifest.append({**meta, "cache_key": cache_key})
        return parsed, meta

    def _write_cache(self, cache_key: str, parsed: Optional[dict], meta: Dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {"meta": meta, "response": parsed}
        (self.cache_dir / f"{cache_key}.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )

    def write_manifest(self) -> None:
        if self.cache_dir is None:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
