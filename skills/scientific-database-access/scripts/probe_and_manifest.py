#!/usr/bin/env python3
"""Probe a single URL and record the fields the access-matrix template needs.

This is a small, dependency-light helper (stdlib only) intended to make
"probe a route and record evidence" a single, repeatable action instead of a
manually-reconstructed one. It does NOT decide route status for you -- read
the response, apply references/route-status-vocabulary.md yourself, and fill
in references/access-matrix-template.md using this script's output.

It never sends or logs credentials. Pass headers explicitly if a documented,
non-secret step (e.g. a public API key) is required; never pass passwords or
session tokens on the command line.

Usage:
    python3 probe_and_manifest.py <url> [--method GET] [--header "Key: Value" ...] \
        [--manifest manifest.json] [--label my-source-route] [--timeout 15] \
        [--body-sample-bytes 512]

Every invocation appends one record to the manifest (default: manifest.json
in the current directory) rather than overwriting it, so a manifest can
accumulate the full probe history for a source.
"""
import argparse
import hashlib
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone

REDACT_HEADER_NAMES = {"authorization", "cookie", "set-cookie", "x-api-key"}


def redact_headers(headers):
    """Keep header names for evidence; redact values that commonly carry secrets."""
    out = {}
    for k, v in headers.items():
        if k.lower() in REDACT_HEADER_NAMES:
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


def probe(url, method, extra_headers, timeout, body_sample_bytes):
    req = urllib.request.Request(url, method=method)
    for h in extra_headers:
        if ":" not in h:
            raise SystemExit(f"--header must be 'Key: Value', got: {h!r}")
        key, _, value = h.partition(":")
        req.add_header(key.strip(), value.strip())

    record = {
        "url": url,
        "method": method,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(body_sample_bytes + 1)
            truncated = len(body) > body_sample_bytes
            body_sample = body[:body_sample_bytes]
            record.update(
                {
                    "http_status": resp.status,
                    "content_type": resp.headers.get("Content-Type", ""),
                    "headers": redact_headers(dict(resp.headers.items())),
                    "body_sample_truncated": truncated,
                    "body_sample_text": body_sample.decode("utf-8", errors="replace"),
                    "body_sample_sha256": hashlib.sha256(body_sample).hexdigest(),
                    "transport_error": None,
                }
            )
    except urllib.error.HTTPError as e:
        body = e.read(body_sample_bytes + 1)
        record.update(
            {
                "http_status": e.code,
                "content_type": e.headers.get("Content-Type", "") if e.headers else "",
                "headers": redact_headers(dict(e.headers.items())) if e.headers else {},
                "body_sample_truncated": len(body) > body_sample_bytes,
                "body_sample_text": body[:body_sample_bytes].decode(
                    "utf-8", errors="replace"
                ),
                "body_sample_sha256": hashlib.sha256(body[:body_sample_bytes]).hexdigest(),
                "transport_error": None,
            }
        )
    except (urllib.error.URLError, TimeoutError) as e:
        record.update(
            {
                "http_status": None,
                "content_type": None,
                "headers": {},
                "body_sample_truncated": False,
                "body_sample_text": "",
                "body_sample_sha256": None,
                "transport_error": str(e),
            }
        )
    return record


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url")
    ap.add_argument("--method", default="GET")
    ap.add_argument("--header", action="append", default=[], dest="headers")
    ap.add_argument("--manifest", default="manifest.json")
    ap.add_argument("--label", default=None, help="short route label, e.g. source:route")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--body-sample-bytes", type=int, default=512)
    args = ap.parse_args()

    record = probe(
        args.url, args.method, args.headers, args.timeout, args.body_sample_bytes
    )
    if args.label:
        record["label"] = args.label

    try:
        with open(args.manifest, "r", encoding="utf-8") as f:
            manifest = json.load(f)
            if not isinstance(manifest, list):
                raise ValueError("existing manifest is not a JSON array")
    except FileNotFoundError:
        manifest = []

    manifest.append(record)
    with open(args.manifest, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
        f.write("\n")

    print(json.dumps(record, indent=2, sort_keys=True))
    print(f"\nAppended to {args.manifest}", file=sys.stderr)
    print(
        "Reminder: read the body/content-type yourself and assign a status from "
        "references/route-status-vocabulary.md -- this script records evidence, "
        "it does not classify it.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
