#!/usr/bin/env python3
"""Opt-in live contract probe for the BioCyc/EcoCyc/CyanoCyc `getxml` REST
API.

**This script makes a real network call. It never runs as part of the
offline test suite.** It exists to cheaply re-verify (per the
`scientific-database-access` hub skill's step 2: "probe access before
planning any fallback") that the documented public `getxml` route is still
reachable for individual-frame lookups, without ever exceeding BioCyc's
documented 1-query/sec rate-limit guideline and without ever attempting a
bulk/systematic download.

Guardrails (all enforced in code, not just documented):
  - Requires the caller to pass `--i-understand-this-makes-a-live-request`
    (or set `BIOCYC_LIVE_PROBE=1`) -- refuses to run otherwise.
  - Issues at most `--max-requests` individually-named frame lookups
    (default 1, hard-capped at `MAX_REQUESTS_HARD_CAP`) in one invocation.
  - Every request goes through `BioCycClient`'s `RateLimiter`, whose
    `min_spacing_seconds` this script asserts is >= 1.0 before issuing any
    request -- refuses to run if misconfigured to go faster.
  - Fetches only single, caller-named frames (default: the public Tier-1
    MetaCyc frame `META:WATER`, deliberately not any organism-specific
    Tier-2/3 PGDB) -- never a pathway/organism-wide listing or export
    endpoint. This is a reachability check, not an ingestion run.
  - Does not read, submit, or act on any license/terms-of-use acceptance
    page -- current terms guidance is owned by
    `../references/access-routes-and-terms.md` (and the
    `scientific-database-access` hub skill), checked from already-committed
    evidence; this probe never fetches or "accepts" a new terms page.

Exit code 0 means "probe ran and produced a status", not "route is
healthy" -- always read the printed status/evidence before relying on it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from biocyc_client import BioCycClient, RateLimiter

MAX_REQUESTS_HARD_CAP = 3
MIN_ALLOWED_SPACING_SECONDS = 1.0
DEFAULT_PROBE_ORGID = "META"
DEFAULT_PROBE_FRAMEID = "WATER"  # Public Tier-1 MetaCyc frame -- not organism-specific.


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--i-understand-this-makes-a-live-request",
        action="store_true",
        dest="consent",
        help="Required flag (or set BIOCYC_LIVE_PROBE=1) to actually issue a live HTTP request.",
    )
    parser.add_argument("--orgid", default=DEFAULT_PROBE_ORGID)
    parser.add_argument("--frameid", default=DEFAULT_PROBE_FRAMEID)
    parser.add_argument(
        "--max-requests", type=int, default=1, help=f"Frame lookups to issue, capped at {MAX_REQUESTS_HARD_CAP}."
    )
    parser.add_argument(
        "--min-spacing-seconds", type=float, default=MIN_ALLOWED_SPACING_SECONDS,
        help=f"Minimum seconds between requests; refuses to run below {MIN_ALLOWED_SPACING_SECONDS}.",
    )
    return parser


def classify_route_status(outcome: Optional[str], http_status: Optional[int]) -> str:
    """Map a probe's `FrameResult.outcome` to the shared route-status
    vocabulary used by the `scientific-database-access` hub skill (see
    `references/route-status-vocabulary.md` there -- reused, not
    duplicated)."""
    if outcome == "ok":
        return "reachable_public"
    if outcome == "gated":
        return "gated_paid_subscription"
    if outcome == "missing_frame":
        return "dead_superseded"  # confirmed reachable, but this specific frame ID no longer/never resolves.
    return "unknown_untested"


def run(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    consent = args.consent or os.environ.get("BIOCYC_LIVE_PROBE") == "1"
    if not consent:
        print(json.dumps({
            "ran": False,
            "reason": (
                "Refusing to make a live request without explicit consent. Pass "
                "--i-understand-this-makes-a-live-request or set BIOCYC_LIVE_PROBE=1."
            ),
        }, indent=2))
        return 1

    if args.min_spacing_seconds < MIN_ALLOWED_SPACING_SECONDS:
        print(json.dumps({
            "ran": False,
            "reason": (
                f"Refusing to run with min_spacing_seconds={args.min_spacing_seconds} < "
                f"{MIN_ALLOWED_SPACING_SECONDS} (BioCyc Limited Use License 1 query/sec guideline)."
            ),
        }, indent=2))
        return 1

    max_requests = max(1, min(args.max_requests, MAX_REQUESTS_HARD_CAP))
    client = BioCycClient(rate_limiter=RateLimiter(min_spacing_seconds=args.min_spacing_seconds))
    results = []
    for _ in range(max_requests):
        frame_result = client.get_frame(args.orgid, args.frameid)
        results.append({
            "meta": frame_result.meta,
            "route_status": classify_route_status(frame_result.outcome, frame_result.http_status),
        })

    print(json.dumps({
        "ran": True,
        "requests_issued": len(results),
        "min_spacing_seconds": args.min_spacing_seconds,
        "bulk_download_attempted": False,
        "results": results,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
