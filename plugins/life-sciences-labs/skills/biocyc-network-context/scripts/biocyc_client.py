#!/usr/bin/env python3
"""Rate-limited HTTP client for the BioCyc/EcoCyc/CyanoCyc `getxml` REST API
(`https://websvc.biocyc.org/getxml?<orgid>:<frameid>`).

Generalized, project-agnostic extraction of the client/rate-limiting logic
validated in two prior ingestion passes (DX-30 against MED4/CyanoCyc,
DX-53 against ECOLI/EcoCyc -- see
`../references/access-routes-and-terms.md`). No orgid, frame ID, or
project identifier is hardcoded here -- callers supply every identifier.

BioCyc's Limited Use License documents a 1 query/sec guideline (no
documented HTTP 429/Retry-After contract, unlike SABIO-RK) -- this client
therefore enforces a minimum inter-request spacing rather than a sliding
request-count window, and never performs a bulk/systematic multi-object
query: every call fetches exactly one namespace-qualified frame.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

USER_AGENT = "discovery-biocyc-network-context-skill/1.0 (+https://github.com; academic use)"

# BioCyc Limited Use License guideline (confirmed via live headers in DX-30/
# DX-53): 1 query/sec. This is a minimum-spacing contract, not a rolling
# request-count window.
DEFAULT_MIN_REQUEST_SPACING_SECONDS = 1.0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RateLimiter:
    """Enforces a minimum spacing between successive `acquire()` calls.

    `time_fn`/`sleep_fn` are injectable so tests never perform a real
    wall-clock sleep.
    """

    def __init__(
        self,
        min_spacing_seconds: float = DEFAULT_MIN_REQUEST_SPACING_SECONDS,
        time_fn: Callable[[], float] = time.monotonic,
        sleep_fn: Callable[[float], None] = time.sleep,
    ):
        self.min_spacing_seconds = min_spacing_seconds
        self._time_fn = time_fn
        self._sleep_fn = sleep_fn
        self._last_call_at: Optional[float] = None

    def acquire(self) -> None:
        now = self._time_fn()
        if self._last_call_at is not None:
            elapsed = now - self._last_call_at
            remaining = self.min_spacing_seconds - elapsed
            if remaining > 0:
                self._sleep_fn(remaining)
        self._last_call_at = self._time_fn()


class HttpResponse:
    def __init__(self, status: int, body: bytes, headers: Optional[Dict[str, str]] = None):
        self.status = status
        self.body = body
        self.headers = headers or {}

    def text(self) -> str:
        return self.body.decode("iso-8859-1", errors="replace")


def default_http_get(url: str) -> HttpResponse:
    """Real network GET via urllib (stdlib only). Used only by the opt-in
    live contract probe -- all offline tests inject a fake instead."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return HttpResponse(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as exc:
        body = exc.read() if exc.fp else b""
        return HttpResponse(exc.code, body, dict(exc.headers or {}))


class BioCycClient:
    """Thin, rate-limited client over BioCyc's `getxml` REST endpoint.

    Fetches exactly one namespace-qualified frame (`<orgid>:<frameid>`) per
    call -- never a bulk/systematic multi-object query. Caches raw
    responses + a manifest for provenance auditability, mirroring the
    DX-30/DX-53 raw-evidence convention.
    """

    DEFAULT_BASE_URL = "https://websvc.biocyc.org/getxml"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        http_get: Callable[[str], HttpResponse] = default_http_get,
        rate_limiter: Optional[RateLimiter] = None,
        cache_dir: Optional[Path] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._http_get = http_get
        self.rate_limiter = rate_limiter or RateLimiter()
        self.cache_dir = cache_dir
        self.manifest: List[Dict[str, Any]] = []

    def get_frame(self, orgid: str, frameid: str) -> "FrameResult":
        """GET `<base_url>?<orgid>:<frameid>`. Returns a `FrameResult` with
        the raw response body, retrieval metadata, and an explicit
        `outcome` (`"ok"` / `"missing_frame"` / `"gated"` / `"failure"`) --
        see `response_classifier.classify_response` for the missing_frame/
        gated/ok distinction. Never conflates a confirmed 404 (frame does
        not exist) with a subscription/bot-challenge gate.
        """
        qualified_id = f"{orgid}:{frameid}"
        url = f"{self.base_url}?{qualified_id}"

        self.rate_limiter.acquire()
        response = self._http_get(url)
        body_text = response.text()

        from response_classifier import classify_response  # local import: keeps this module import-order-independent

        classification = classify_response(response.status, body_text)
        if classification == "ok":
            outcome = "ok"
        elif classification in ("missing_frame", "gated"):
            outcome = classification
        else:
            outcome = "failure"

        meta = {
            "url": url,
            "qualified_id": qualified_id,
            "http_status": response.status,
            "outcome": outcome,
            "retrieved_at": _utc_now_iso(),
        }
        if self.cache_dir is not None:
            self._write_cache(qualified_id, body_text, meta)
        self.manifest.append(meta)
        return FrameResult(orgid=orgid, frameid=frameid, outcome=outcome, http_status=response.status, body_text=body_text, meta=meta)

    def _write_cache(self, qualified_id: str, body_text: str, meta: Dict[str, Any]) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        safe_name = qualified_id.replace(":", "_").replace("/", "_")
        (self.cache_dir / f"{safe_name}.xml").write_text(body_text, encoding="utf-8")
        (self.cache_dir / f"{safe_name}.meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8"
        )

    def write_manifest(self) -> None:
        if self.cache_dir is None:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        (self.cache_dir / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True), encoding="utf-8"
        )


class FrameResult:
    """Result of a single `BioCycClient.get_frame` call."""

    def __init__(self, orgid: str, frameid: str, outcome: str, http_status: int, body_text: str, meta: Dict[str, Any]):
        self.orgid = orgid
        self.frameid = frameid
        self.outcome = outcome
        self.http_status = http_status
        self.body_text = body_text
        self.meta = meta

    @property
    def qualified_id(self) -> str:
        return f"{self.orgid}:{self.frameid}"


class CachedFixtureClient:
    """Test/demo double for `BioCycClient` that replays pre-fetched fixture
    files from a directory instead of making network calls. Used by
    `scripts/build_context.py`'s deterministic offline demo and by the
    offline pytest suite -- never used for the opt-in live probe.

    `frame_to_fixture` maps `"<orgid>:<frameid>"` -> either a fixture file
    path (relative to `fixtures_dir`, implying HTTP 200) or a
    `(fixture_path, http_status)` tuple, so a caller can replay a synthetic
    gated (404 + hCaptcha marker) or other non-200 fixture. A qualified_id
    absent from the map is reported as `missing_frame` (404-equivalent),
    matching the real client's behavior for an unknown frame -- it is never
    silently skipped.
    """

    def __init__(self, fixtures_dir: Path, frame_to_fixture: Dict[str, str], rate_limiter: Optional[RateLimiter] = None):
        self.fixtures_dir = fixtures_dir
        self.frame_to_fixture = frame_to_fixture
        self.rate_limiter = rate_limiter or RateLimiter(min_spacing_seconds=0.0)
        self.manifest: List[Dict[str, Any]] = []
        self.cache_dir = None

    def get_frame(self, orgid: str, frameid: str) -> FrameResult:
        qualified_id = f"{orgid}:{frameid}"
        self.rate_limiter.acquire()

        from response_classifier import classify_response

        if qualified_id not in self.frame_to_fixture:
            body_text = (
                f"<ptools-xml><error>The object {frameid} was not found in "
                f"database {orgid}.</error></ptools-xml>"
            )
            outcome = classify_response(404, body_text)
            meta = {"qualified_id": qualified_id, "http_status": 404, "outcome": outcome, "retrieved_at": _utc_now_iso(), "source": "synthetic_not_in_map"}
            self.manifest.append(meta)
            return FrameResult(orgid, frameid, outcome, 404, body_text, meta)

        mapped = self.frame_to_fixture[qualified_id]
        if isinstance(mapped, tuple):
            fixture_name, http_status = mapped
        else:
            fixture_name, http_status = mapped, 200
        fixture_path = self.fixtures_dir / fixture_name
        body_text = fixture_path.read_text(encoding="iso-8859-1", errors="replace")
        outcome = classify_response(http_status, body_text)
        meta = {"qualified_id": qualified_id, "http_status": http_status, "outcome": outcome, "retrieved_at": _utc_now_iso(), "source": str(fixture_path)}
        self.manifest.append(meta)
        return FrameResult(orgid, frameid, outcome, http_status, body_text, meta)

    def write_manifest(self) -> None:
        return None
