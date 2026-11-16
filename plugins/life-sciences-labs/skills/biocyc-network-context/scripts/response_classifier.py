#!/usr/bin/env python3
"""Classify a raw BioCyc/EcoCyc/CyanoCyc HTTP response as missing_frame vs
gated vs ok.

Generalized, project-agnostic extraction of the classifier validated in
DX-53 (`scripts/biocyc_response_classifier.py` in the repo root, itself
following the DX-30 precedent of a MED4 object page 404 with an
hCaptcha-bearing CSP header). Distinguishes two responses that could
otherwise be conflated as "access failed":

- ``missing_frame``: the queried frame ID genuinely does not exist in the
  target PGDB. Signature: HTTP 404 *and* an explicit
  "was not found in database <ORGID>" message in the body -- a specific,
  object-scoped not-found statement, not generic site chrome.
- ``gated``: the object page is blocked by a bot/subscription challenge
  rather than a genuine "no such frame" result. Signature: HTTP 404 *and*
  an hCaptcha-bearing Content-Security-Policy marker *and* the absence of
  the specific not-found message above.
- ``ok``: a normal response -- not a 404, regardless of whether generic
  subscription/login marketing chrome (which appears on essentially every
  BioCyc page, including fully-open ones) is present in the body.

This module makes no network calls; it only classifies already-retrieved
response text. Never used to make an access decision on its own -- see
`../references/missing-frame-vs-gated.md` for how callers should act on
each outcome.
"""
import re

NOT_FOUND_RE = re.compile(r"was not found in database\s+\w+", re.IGNORECASE)
HCAPTCHA_RE = re.compile(r"hcaptcha", re.IGNORECASE)


def classify_response(http_status, body_text):
    """Return 'missing_frame', 'gated', or 'ok' for a raw BioCyc response."""
    has_not_found = bool(NOT_FOUND_RE.search(body_text))
    has_hcaptcha = bool(HCAPTCHA_RE.search(body_text))

    if http_status == 404 and has_not_found:
        return "missing_frame"
    if http_status == 404 and has_hcaptcha and not has_not_found:
        return "gated"
    return "ok"
