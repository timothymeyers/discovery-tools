---
name: biocyc-network-context
description: |
  Life sciences / Systems biology — Parameterized bounded BioCyc, EcoCyc, and
  CyanoCyc network-context traversal: reaction -> enzyme/complex -> gene ->
  pathway. Preserves every catalyst for a reaction rather than collapsing
  isoenzymes, expands multi-subunit complexes to each subunit's own gene, and
  verifies organism identity from the PGDB's own NCBI-TAXONOMY-DB
  cross-reference rather than assuming it from the orgid string. Distinguishes
  missing_frame (the frame genuinely does not exist) from gated (a bot or
  subscription challenge blocked the page) instead of conflating both as
  "access failed". Rate-limited getxml client, never a bulk endpoint. Every
  identifier is a parameter. WHEN: "query BioCyc", "query EcoCyc", "query
  CyanoCyc", "getxml traversal", "BioCyc network context", "reaction to gene
  to pathway", "BioCyc isoenzyme", "protein complex subunits", "missing frame
  vs gated", "BioCyc rate limit", "BioCyc Limited Use License".
metadata:
  version: "1"
  category: "Life sciences"
  subfield: "Systems biology / metabolic networks"
  secondary: "Genomics (gene-reaction links)"
---

# biocyc-network-context — Parameterized BioCyc/EcoCyc/CyanoCyc Network-Context Traversal

Reusable, project-agnostic adapter for walking a BioCyc-family PGDB's
`getxml` REST API from a single reaction frame out to its catalyzing
enzyme(s)/complex(es), their gene(s), and pathway membership -- generalized
from two prior, independently validated ingestion passes: DX-30 (3
Calvin-Benson-cycle reactions against *Prochlorococcus marinus* MED4 /
CyanoCyc) and DX-53 (3 central-metabolism reactions -- pgi/EC 5.3.1.9, pfk/EC
2.7.1.11, zwf/EC 1.1.1.49 -- against *E. coli* K-12 MG1655 / EcoCyc). Every
organism, orgid, frame ID, EC number, and taxid used in this skill's
scripts/fixtures is illustrative; nothing is hardcoded as a default inside
`scripts/traversal.py` itself.

**Access-route status and general database-access procedure are owned by
the `scientific-database-access` hub skill**
(`references/sources/biocyc.md` there records the last-verified route
status and rate-limit contract) -- this skill does not duplicate that; see
`references/access-routes-and-terms.md` for a summary from already-
committed evidence and focuses on the traversal/classification/outcome
logic for actually walking a PGDB's object graph.

## When to use

- Walk a BioCyc/EcoCyc/CyanoCyc reaction frame out to its catalyzing
  enzyme(s), the gene(s) encoding them, and pathway membership, with every
  step individually rate-limited and every outcome (success/missing/gated/
  failure) explicit (`scripts/traversal.py`).
- Preserve multiple catalysts for one EC number (isoenzymes, or a
  cross-reactive secondary enzyme) instead of picking just one -- see
  `references/traversal-strategy.md`.
- Expand a multi-subunit protein complex to each distinct subunit's own
  gene, correctly handling both homomeric complexes (one subunit type, one
  gene) and heteromeric complexes (multiple subunit types, multiple genes)
  -- also in `references/traversal-strategy.md`.
- Confirm organism identity from a PGDB's own `NCBI-TAXONOMY-DB` dblink
  cross-reference rather than assuming it from the `orgid` string.
- Distinguish a genuinely nonexistent frame (`missing_frame`) from a
  bot/subscription-gated page (`gated`) -- these must never be conflated
  (`references/missing-frame-vs-gated.md`).
- Respect BioCyc's 1-query/sec rate-limit guideline with a minimum
  inter-request spacing (not a rolling-window counter, since BioCyc has no
  documented 429/Retry-After contract like SABIO-RK) --
  `scripts/biocyc_client.py::RateLimiter`.
- Cheaply re-verify the public `getxml` route is still reachable before
  relying on it, without risking the rate limit or attempting any bulk
  download (`scripts/live_contract_probe.py`, opt-in).

## Bounded traversal (parameterized, never a bare organism-wide query)

`scripts/traversal.py::traverse_reaction(client, orgid, reaction_frameid,
entry_id, expected_ec=None, expected_taxid=None, pathway_detail_limit=1)`
runs the full reaction -> enzyme/complex -> gene -> pathway walk for one
reaction frame and returns a plain dict (JSON-serializable) reporting:

- `outcome` (`"ok"` / `"missing_frame"` / `"gated"` / `"failure"`) for the
  root reaction fetch.
- `ec_match` / `organism_verified` (booleans, or `None` if not checked) --
  never silently assumed true.
- `genes_and_enzymes`: one entry **per catalyst**, each with its own
  `role` (`"monomer"` / `"complex"` / `"complex_subunit"`), `protein`,
  `gene`, `gene_symbol`, `legacy_locus_tag`, and `genome_coordinates`.
- `pathways`: every `<in-pathway>` frame ID, namespace-qualified, with
  detail fetched for only the first `pathway_detail_limit` (bounded).
- `compounds`: left/right reaction participants, namespace-qualified.

See `references/traversal-strategy.md` for the full step-by-step rationale
and the isoenzyme/complex-expansion worked examples.

## Rate-limited client + missing-frame/gated classification

`scripts/biocyc_client.py::BioCycClient` fetches exactly one namespace-
qualified frame per call (`getxml?<orgid>:<frameid>`) through a `RateLimiter`
enforcing a minimum inter-request spacing (default 1.0s, matching BioCyc's
Limited Use License guideline), classifying every response via
`scripts/response_classifier.py::classify_response` into `missing_frame` /
`gated` / `ok` (see `references/missing-frame-vs-gated.md`) before the
traversal ever branches on it. `CachedFixtureClient` (same module) is a
drop-in test/demo double that replays this skill's own bundled fixtures
instead of making network calls -- used by the offline test suite and
`scripts/build_context.py --example`.

## Opt-in live contract probe

`scripts/live_contract_probe.py` makes **one to three real, individually
rate-limited** `getxml` requests against a public Tier-1 MetaCyc frame (by
default `META:WATER` -- deliberately not any organism-specific Tier-2/3
PGDB) to confirm the route is still reachable. It:

- Refuses to run without explicit consent
  (`--i-understand-this-makes-a-live-request` or `BIOCYC_LIVE_PROBE=1`).
- Refuses to run with a request spacing below 1.0 second.
- Hard-caps itself at `MAX_REQUESTS_HARD_CAP = 3` requests per invocation.
- Never fetches, reads, or "accepts" any license/terms-of-use page --
  current-terms guidance is read from already-committed evidence in
  `references/access-routes-and-terms.md`, not re-checked live by this
  probe.
- Never calls a bulk/systematic multi-object endpoint -- every request is
  one individually-named frame lookup.

**Never run this from an offline/CI test.** `tests/test_live_contract_probe.py`
exercises its consent-gating, spacing guardrail, hard cap, and route-status
classification entirely with an injected fake HTTP layer.

## Files in this skill

- `scripts/biocyc_client.py` — `RateLimiter` (minimum-spacing enforcement),
  `HttpResponse`, `default_http_get`, `BioCycClient` (real, rate-limited
  single-frame `getxml` client with cache + manifest),
  `CachedFixtureClient` (offline fixture-replay double).
- `scripts/response_classifier.py` — `classify_response` (missing_frame /
  gated / ok).
- `scripts/traversal.py` — `traverse_reaction` (the bounded
  reaction -> enzyme/complex -> gene -> pathway walk).
- `scripts/build_context.py` — CLI: `--example` (offline, bundled 3-EC
  demo) or `--live --orgid --reaction-frameid --ec --taxid
  --i-understand-this-makes-a-live-request` (real HTTP).
- `scripts/live_contract_probe.py` — opt-in, rate-limit-safe live probe.
- `fixtures/` — small (each well under 2.5 KB), hand-trimmed EcoCyc
  `getxml` response fixtures for the three validated non-Calvin EC
  examples (5.3.1.9/pgi, 2.7.1.11/pfk, 1.1.1.49/zwf: reaction, enzyme
  complex/monomer, gene, and pathway frames for each), plus
  `missing_frame_response.xml` (real 404 body shape) and
  `synthetic_gated_response.html` (clearly-labeled synthetic, never
  captured live). EcoCyc is a BioCyc "Open Database" -- see
  `references/access-routes-and-terms.md` for why these are freely
  redistributable.
- `references/access-routes-and-terms.md` — current terms/access
  guidance, summarized from already-committed evidence (points to the
  `scientific-database-access` hub skill as the authoritative,
  continuously-maintained source).
- `references/traversal-strategy.md` — full traversal rationale,
  isoenzyme/complex-expansion worked examples.
- `references/missing-frame-vs-gated.md` — classification semantics and
  why they must never be conflated.
- `tests/` — offline pytest suite, no live network calls anywhere:
  classifier semantics (`test_response_classifier.py`), traversal
  including isoenzyme/complex/organism-verification/bounded-pathway/
  missing-frame/gated coverage (`test_traversal.py`), CLI + deterministic
  replay (`test_build_context.py`), and live-probe consent-gating/spacing/
  cap/route-status classification (`test_live_contract_probe.py`). Run
  with `python3 -m pytest tests/ -q` from this skill's directory.

## Generalization note

No orgid, frame ID, EC number, or taxid is hardcoded inside
`scripts/traversal.py`, `scripts/biocyc_client.py`, or
`scripts/response_classifier.py` -- every one is a parameter. The example
values in `scripts/build_context.py` (`EXAMPLE_ORGID = "ECOLI"`, the three
pgi/pfk/zwf EC numbers, taxid `511145`) and the bundled fixtures are
illustrative only, reused from the two prior validated ingestion passes
strictly as worked examples. When adapting this skill to a new organism,
reaction, or EC number, pass your own values via `traverse_reaction`'s
parameters or `build_context.py`'s `--live` CLI flags -- never import
another project's crosswalk values (MED4-specific or otherwise) as if they
were general defaults.
