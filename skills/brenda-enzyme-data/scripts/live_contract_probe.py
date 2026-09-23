#!/usr/bin/env python3
"""Opt-in live contract probe for BRENDA's official access routes.

**This script makes real network calls when consent is given. It never runs
as part of the offline test suite, never accepts BRENDA's license, and
never downloads the bulk release.** It exists to cheaply re-verify that the
routes documented in the `scientific-database-access` hub skill's
`references/sources/brenda.md` card are still reachable, without acquiring
any licensed data:

  - `GET soap.php?wsdl` — confirms the SOAP WSDL is still publicly served
    (contract-shape check only; this probe never sends a `login` operation,
    so no credentials are needed or read).
  - `GET download.php` — confirms the license/download PAGE itself is
    reachable. **This probe issues a plain GET only. It never sends
    `accept-license=1`, never POSTs the download form, and never writes any
    downloaded file** — accepting the license and fetching the bulk release
    is a deliberate, separate, human-authorized action outside this skill.
  - `GET sparql.dsmz.de/api/brenda` (a tiny, registration-free `SELECT`
    query) — confirms the public SPARQL endpoint is still live.

Guardrails:
  - Requires `--i-understand-this-makes-a-live-request` (or
    `BRENDA_LIVE_PROBE=1`) — refuses to run otherwise.
  - Never sets `accept-license=1`, never POSTs to `download.php`, never
    writes a downloaded file, never reads `BRENDA_EMAIL`/
    `BRENDA_PASSWORD_SHA256` from the environment for this probe (those
    exist only for a genuinely credentialed SOAP call elsewhere, and this
    probe does not make one).
  - Reports route status using the `scientific-database-access` hub skill's
    shared vocabulary (`reachable_public`, `gated_credentialed`,
    `dead_superseded`, `unknown_untested`, ...) rather than a bespoke one.

Exit code 0 means "probe ran and produced a status", not "route is
healthy" -- always read the printed status before relying on it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Optional

SOAP_WSDL_URL = "https://www.brenda-enzymes.org/soap.php?wsdl"
DOWNLOAD_PAGE_URL = "https://www.brenda-enzymes.org/download.php"
SPARQL_PROBE_URL = (
    "https://sparql.dsmz.de/api/brenda?query="
    "SELECT+DISTINCT+%3Fp+WHERE+%7B+%3Fs+%3Fp+%3Fo+%7D+LIMIT+1"
    "&format=json"
)

FORBIDDEN_PARAMS = ("accept-license", "dlfile")


def _http_get(url: str, timeout: float = 15.0) -> tuple[Optional[int], Optional[str]]:
    """Plain, unauthenticated GET. Never sends a request body, never sends
    the license-acceptance/download form fields."""
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.status, resp.headers.get("Content-Type")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type") if e.headers else None
    except (urllib.error.URLError, TimeoutError, OSError):
        return None, None


def classify_route_status(http_status: Optional[int]) -> str:
    if http_status is None:
        return "unknown_untested"
    if http_status in (401, 403):
        return "gated_credentialed"
    if http_status in (301, 302, 404):
        return "dead_superseded"
    if http_status == 200:
        return "reachable_public"
    return "unknown_untested"


def probe(consent: bool) -> dict:
    if not consent:
        return {
            "ran": False,
            "reason": (
                "Refusing to make a live request without explicit consent. Pass "
                "--i-understand-this-makes-a-live-request or set BRENDA_LIVE_PROBE=1."
            ),
        }

    targets = (
        ("soap_wsdl", SOAP_WSDL_URL),
        ("download_page", DOWNLOAD_PAGE_URL),
        ("sparql_endpoint", SPARQL_PROBE_URL),
    )
    # Validate every URL up front, before issuing any HTTP request, so a
    # tampered/forbidden URL is rejected without ever touching the network.
    for _name, url in targets:
        assert not any(p in url for p in FORBIDDEN_PARAMS), (
            f"refusing to probe a URL containing a license-acceptance/download param: {url}"
        )

    results = {}
    for name, url in targets:
        status, content_type = _http_get(url)
        results[name] = {
            "url": url,
            "http_status": status,
            "content_type": content_type,
            "route_status": classify_route_status(status),
        }

    return {
        "ran": True,
        "license_accepted": False,
        "bulk_file_downloaded": False,
        "note": (
            "This probe never sent accept-license=1, never POSTed the download "
            "form, and never wrote any downloaded file. Accepting BRENDA's "
            "license and fetching the bulk release is a separate, deliberate, "
            "human-authorized action outside this skill."
        ),
        "results": results,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--i-understand-this-makes-a-live-request",
        action="store_true",
        dest="consent",
        help="Required flag (or set BRENDA_LIVE_PROBE=1) to actually issue live HTTP requests.",
    )
    args = ap.parse_args()
    consent = args.consent or os.environ.get("BRENDA_LIVE_PROBE") == "1"

    result = probe(consent)
    print(json.dumps(result, indent=2, sort_keys=True))
    raise SystemExit(0 if result["ran"] else 1)


if __name__ == "__main__":
    main()
