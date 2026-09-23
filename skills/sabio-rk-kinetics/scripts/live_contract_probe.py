#!/usr/bin/env python3
"""Opt-in live contract probe for the SABIO-RK Export API.

**This script makes a real network call. It never runs as part of the
offline test suite.** It exists to cheaply re-verify (per the
`scientific-database-access` hub skill's step 2: "probe access before
planning any fallback") that the documented Export API route is still
reachable and still returns the expected JSON envelope/pagination shape,
without ever exceeding the documented 60-requests/60-second rate limit.

Guardrails:
  - Requires the caller to pass `--i-understand-this-makes-a-live-request`
    (or set `SABIO_RK_LIVE_PROBE=1`) -- refuses to run otherwise.
  - Issues at most `--max-requests` calls (default 1, hard-capped at 5) in
    one invocation, all through the same rate-limited `SabioClient` used
    elsewhere in this skill.
  - Reports route status using this repo's fixed vocabulary
    (`reachable_public`, `dead_superseded`, `blocked_bot_challenge`,
    `unknown_untested`, ...) -- see the `scientific-database-access` hub
    skill's `references/route-status-vocabulary.md` for the full list this
    reuses rather than duplicates.

Exit code 0 means "probe ran and produced a status", not "route is healthy" --
always read the printed status/evidence before relying on it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sabio_rk_client import RateLimiter, SabioClient

MAX_REQUESTS_HARD_CAP = 5


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--i-understand-this-makes-a-live-request",
        action="store_true",
        dest="consent",
        help="Required flag (or set SABIO_RK_LIVE_PROBE=1) to actually issue a live HTTP request.",
    )
    parser.add_argument("--ec", default="1.1.1.1", help="EC number to probe with (default: 1.1.1.1).")
    parser.add_argument(
        "--max-requests", type=int, default=1, help=f"Requests to issue, capped at {MAX_REQUESTS_HARD_CAP}."
    )
    return parser


def classify_route_status(http_status: Optional[int], content_type: Optional[str], parsed: Optional[dict]) -> str:
    """Map a probe response to the shared route-status vocabulary (see
    `scientific-database-access/references/route-status-vocabulary.md`)."""
    if http_status is None:
        return "unknown_untested"
    if http_status in (401, 403):
        return "gated_credentialed"
    if http_status == 429:
        return "reachable_public"  # reachable, just rate-limited right now.
    if http_status in (301, 302, 404):
        return "dead_superseded"
    if http_status == 200 and isinstance(parsed, dict) and "meta" in parsed and "data" in parsed:
        return "reachable_public"
    if http_status == 200:
        return "unknown_untested"  # 200 but not the expected JSON envelope shape.
    return "unknown_untested"


def run(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    consent = args.consent or os.environ.get("SABIO_RK_LIVE_PROBE") == "1"
    if not consent:
        print(
            json.dumps(
                {
                    "ran": False,
                    "reason": (
                        "Refusing to make a live request without explicit consent. Pass "
                        "--i-understand-this-makes-a-live-request or set SABIO_RK_LIVE_PROBE=1."
                    ),
                },
                indent=2,
            )
        )
        return 1

    max_requests = max(1, min(args.max_requests, MAX_REQUESTS_HARD_CAP))
    client = SabioClient(rate_limiter=RateLimiter())
    results = []
    for _ in range(max_requests):
        parsed, meta = client.get_json("/kinlaw-entry/json", {"q": f"ECNumber:{args.ec}", "page": 1, "pageSize": 1})
        status = classify_route_status(meta.get("http_status"), None, parsed)
        results.append({"meta": meta, "route_status": status})

    print(json.dumps({"ran": True, "requests_issued": len(results), "results": results}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
