---
name: scientific-database-access
description: |
  Cross-domain / Research data access and provenance — Establish a working,
  authorized, versioned route to a scientific database or data source before
  declaring it inaccessible. WHEN a user says "validate database access",
  "check dataset availability", "API returns HTML instead of JSON", "database
  needs credentials", "find an official bulk mirror", "is this source down or
  gated?", "the legacy API is dead", "404 vs paywall", "record access
  evidence", or asks whether a scientific database (SABIO-RK, BRENDA,
  BioCyc/CyanoCyc, Rhea, ChEBI, MetaNetX, UniProt, AlphaFold DB, or a similar
  source) is reachable/usable, use this skill before writing an adapter,
  before declaring a source "unavailable", and before designing any
  access-failure fallback.
metadata:
  version: "1"
  category: "Cross-domain"
  subfield: "Research data access and provenance"
  secondary: "Life sciences (current source cards are bio databases)"
---

# Scientific Database Access

## Purpose

Scientific data sources expose *several independent access routes* (public
website, documented API, official bulk export, third-party mirror,
single-object lookup). One route being gated, dead, or credential-walled is
**not** evidence that the others are. This skill is the general procedure for
finding the correct authorized route, recording the evidence for that
finding, and refusing to plan a fallback until access has actually been
tested.

**Anti-pattern this skill exists to prevent:** pre-scripting an
`unavailable_source` fallback (or any other failure-branch plan) *before*
probing the source at all. Treating an assumed or historical failure as
equivalent to a fresh, dated probe is the single most common and most
expensive mistake in this space — see
`references/anti-pattern-premature-fallback.md`.

## Procedure

1. **Enumerate routes before probing any of them.** For the target source,
   list separately: interactive website, documented/official API, bulk
   export or dump, third-party mirror, and single-object/individual-record
   lookup. Do not let one route's status stand in for another's.
2. **Probe access before planning any fallback.** Never write or reuse a
   "this source is unavailable" branch until you have made a live request (or
   read a current, dated contract) for the specific route in question. If a
   memory, task doc, or teammate claims a source is down/gated, treat that as
   a *hypothesis to re-check*, not a fact — cheap re-probes are almost always
   worth it (see step 6).
3. **Read the response, not just the status code.** A `200 OK` that returns
   an HTML single-page-app shell is not a successful JSON API response. A
   `404` on a *guessed* identifier is not proof that the endpoint requires a
   subscription — it may just be a wrong ID. Inspect `Content-Type`, body
   shape, and any login/CAPTCHA/paywall markers before concluding gated vs.
   dead vs. wrong-input.
4. **Check official documentation and machine-readable contracts** (OpenAPI/
   WSDL/schema files, changelogs, developer docs, the site's own JS bundle if
   nothing else exists) before concluding a legacy service is discontinued.
   Prefer the officially documented successor route over undocumented
   guesses. Never bypass authentication, CAPTCHA, or license gates to "prove"
   access.
5. **Record every access claim** using the fields in
   `references/access-matrix-template.md`: route, request made, response
   evidence (status, content-type, body/checksum, headers), retrieval date,
   scope of the claim (which route/endpoint/organism/query — not "the whole
   database"), and what earlier claim (if any) this supersedes.
6. **Separate technical reachability from license/use permission.** A route
   returning data with no login prompt is not automatically a license to
   redistribute, bulk-harvest, or use commercially. Confirm current terms
   (license, rate limits, redistribution rights) as a distinct fact from "did
   the request succeed."
7. **Date every finding and supersede, don't overwrite.** When a later probe
   contradicts an earlier one, record the new finding with its own date and
   an explicit `supersedes` pointer to the old claim. Keep the old claim
   visible as history — do not silently rewrite it. Re-probe cheaply before a
   downstream task relies on an old finding.

## Route-status vocabulary

Use the fixed vocabulary in `references/route-status-vocabulary.md`
(`reachable_public`, `reachable_documented_auth`, `gated_credentialed`,
`gated_licensed_bulk`, `gated_paid_subscription`, `dead_superseded`,
`dead_unconfirmed`, `blocked_bot_challenge`, `unknown_untested`) instead of
ad-hoc words like "broken" or "inaccessible" — those words hide which route,
and whether it was actually tested.

## Reusable assets

- `references/access-matrix-template.md` — the per-source, per-route record
  to fill in for every access claim.
- `references/route-status-vocabulary.md` — the fixed status vocabulary.
- `references/anti-pattern-premature-fallback.md` — worked example of
  planning a failure branch before testing access, and how to avoid it.
- `references/sources/*.md` — source cards (SABIO-RK, BRENDA, BioCyc, Rhea,
  ChEBI/OLS4, MetaNetX, UniProt, AlphaFold DB) recording last-verified access
  routes and evidence pointers for each. These are illustrative starting
  points, not durable guarantees — re-verify before relying on them (see
  each card's "last verified" date).
- `scripts/probe_and_manifest.py` — a small, dependency-light HTTP probe
  helper that records the fields the access matrix needs (status,
  content-type, body sample/hash, headers, timestamp) to a JSON manifest, so
  probing and evidence-recording happen together instead of being
  reconstructed from memory afterward.

## Scope boundaries

- This skill is about *finding and recording authorized access*, not about
  parsing or normalizing the data once retrieved (see a source-specific
  ingestion skill/adapter for that).
- Do not generalize a single project's or organism's finding into a
  database-wide rule. "This EC number had no hits for organism X" is not "this
  database has no data for X."
- Never record secrets, credentials, session tokens, absolute local paths, or
  internal project/task identifiers in access evidence. Redact before saving.
