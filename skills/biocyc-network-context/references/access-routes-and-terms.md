# Current terms / access guidance (BioCyc / EcoCyc / CyanoCyc)

**This skill does not own access-route status.** The `scientific-database-access`
hub skill's `references/sources/biocyc.md` is the authoritative, continuously
maintained record of last-verified route status, licensing, and rate-limit
contract for the BioCyc family of databases. Before relying on any statement
below, re-check that file for a more recent verification.

This document summarizes, from already-committed evidence
(`docs/database-evidence/dx30-biocyc-network-context-methods.md`,
`docs/database-evidence/second-organism/dx53-biocyc-ecocyc-methods.md`), what
was true as of the last two verification passes. **No new live terms check
was performed to author this skill** -- see those methods docs for the full
citations.

## Two independently-gated access paths (do not conflate)

1. **Public `getxml` REST** (`https://websvc.biocyc.org/getxml?<orgid>:<frameid>`)
   — individual, namespace-qualified object lookups. Confirmed reachable with
   **no authentication** for:
   - Tier-1 `META` (generic MetaCyc) frames.
   - Organism-specific Tier-2/3 frames too (e.g. `MED4:...` in DX-30), for
     *individual, already-known-valid* frame IDs -- this is narrower than
     unrestricted systematic access to that organism's full PGDB.
2. **Interactive web UI** (`biocyc.org/<orgid>/...`) — gating differs *by
   PGDB tier*, confirmed differently in two separate passes:
   - **EcoCyc** (`ECOLI`) is a documented BioCyc "Open Database": DX-53 found
     its interactive gene page rendered full content with no login/
     subscription gate.
   - **CyanoCyc/MED4** (Tier-2/3, organism-specific): DX-30 found the
     organism-summary page gated behind explicit login/subscription banners,
     and a direct object page 404'd with an hCaptcha-bearing
     Content-Security-Policy header.

Bulk/systematic API download rights for a Tier-2/3 organism-specific PGDB
(e.g. full CyanoCyc/MED4) require a paid non-profit subscription per BioCyc's
published terms (not re-tested live in either pass; carried forward as
documented, not directly confirmed).

## Licensing text (as fetched live in DX-53, 2026-09-22)

Per `https://bioinformatics.ai.sri.com/ptools/licensing/all-reg.shtml`
(BioCyc Databases Limited Use License): *"Open Databases"* — explicitly
including the EcoCyc PGDB — may be used, modified, and redistributed
worldwide/royalty-free for any purpose, with attribution conditions applying
only on redistribution. Using the public `getxml` web-services API
constitutes the sole acceptance mechanism for these terms — no click-through,
account registration, or subscription action is required for Open Database
content. This is why this skill's fixtures (derived from EcoCyc `getxml`
responses) are freely redistributable, and why `scripts/live_contract_probe.py`
never needs to fetch or "accept" any terms page itself.

## Rate limit

1 query/sec guideline (BioCyc Limited Use License). No documented HTTP 429/
`Retry-After` contract (unlike SABIO-RK) — `scripts/biocyc_client.py`'s
`RateLimiter` therefore enforces a minimum inter-request spacing rather than
a rolling request-count window, and this skill's live probe refuses to run
with a spacing below 1.0 seconds.

## What this skill's traversal never does

- Never calls a bulk/systematic multi-object export endpoint.
- Never re-fetches a frame already retrieved once per traversal run
  (`Cache-Control: max-age=86400, public` was observed on every live
  response in both prior passes -- honor it in any caller-level caching you
  add on top of `BioCycClient`).
- Never assumes organism identity from an `orgid` string alone -- always
  reads the PGDB's own `NCBI-TAXONOMY-DB` dblink (see
  `traversal-strategy.md`).
