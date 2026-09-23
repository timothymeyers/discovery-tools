---
name: brenda-enzyme-data
description: |
  Life sciences / Enzymology — Parameterized BRENDA ingestion from a
  caller-supplied licensed bulk JSON release. Subset extractor and
  deterministic canonical-record builder where organism substring, EC number,
  and file path are all parameters. Includes a BRENDA numeric-value-string
  parser covering ranges, the -999 {more} sentinel, and ligand-in-braces
  labels, plus a schema validator that surfaces a genuine publisher-side
  EC-key regex defect instead of hiding it behind a false "0 errors". You must
  supply your own licensed BRENDA download: this skill never bundles, caches,
  downloads, or accepts the license for any BRENDA dump. WHEN: "BRENDA",
  "brenda-enzymes.org", "BRENDA bulk JSON", "km_value", "turnover_number",
  "kcat_km_value", "specific_activity", "BRENDA -999 sentinel", "BRENDA schema
  validation", "BRENDA license", "ingest enzyme kinetic parameters from
  BRENDA".
metadata:
  version: "1"
  category: "Life sciences"
  subfield: "Enzymology"
---

# brenda-enzyme-data — Parameterized BRENDA Bulk-JSON Ingestion

Reusable, project-agnostic adapter for extracting and canonicalizing
enzyme/kinetic-parameter data from BRENDA's licensed bulk JSON release,
generalized from two independent extraction passes (disjoint EC numbers and
organisms) over release `2026.1`. Every EC number, organism substring, and
file path here is a caller-supplied parameter — this skill is not scoped to
any single project's target enzymes.

## Dependencies

The extractor and canonical-record builder are standard-library only.
Only `scripts/validate_schema.py` needs a third-party package, and
`gh skill install` does not install it:

```bash
python3 -m pip install -r requirements.txt   # jsonschema
```

Without it the validator raises a clear error; everything else still runs.

**Access-route status is owned by the `scientific-database-access` hub
skill** (`references/sources/brenda.md` there records the last-verified
route status: the bulk JSON route is `reachable_documented_auth` — a
click-through license acceptance, no account needed; SOAP is
`gated_credentialed`; SPARQL is `reachable_public` but not a fully validated
recipe). This skill does not duplicate that; it assumes the bulk-JSON route
has already been confirmed and focuses on extraction/parsing/validation
logic over a file **you** have already licensed and downloaded.

## Hard requirement: bring your own licensed bulk file

This skill **never** bundles, caches, auto-downloads, or reads any full
BRENDA dump (which runs to hundreds of megabytes uncompressed). Every
script that touches BRENDA's real data takes an explicit `--full-dump` or
`--subset` file-path argument supplied by the caller. If you don't have
one yet: go to `https://www.brenda-enzymes.org/download.php`, review and
accept BRENDA's own license (CC BY 4.0, with the DSI/Cali-Fund
benefit-sharing note — read it yourself), and download the JSON release
yourself. Never store that full file inside a shared/version-controlled
directory this skill or its caller commits — gitignore it (see
`references/access-route.md`).

## When to use

- Extract a small, redistributable, organism/EC-scoped subset from your own
  licensed BRENDA bulk JSON file (`scripts/extract_subset.py`).
- Deterministically rebuild canonical kinetic/organism/ligand/literature
  records from a committed subset, with a byte-identical replay guarantee
  (`scripts/build_canonical_records.py`).
- Parse BRENDA's `<number>[-<number>] {label}` / `-999 {more}` sentinel
  value-string grammar without collapsing ranges to a fake midpoint or
  coercing the sentinel to a fake negative reading
  (`scripts/brenda_value_parser.py`).
- Validate extracted entries against BRENDA's own published JSON Schema
  *and* get an honest report of which entries a naive corpus-level pass
  would have silently skipped due to a real publisher-side regex defect
  (`scripts/validate_schema.py`; see
  `references/schema-generation-vs-version.md`).
- Cheaply re-verify BRENDA's SOAP/download/SPARQL routes are still
  reachable without acquiring any licensed data or accepting any license
  (`scripts/live_contract_probe.py`, opt-in).

## Extraction: user-supplied file only

`scripts/extract_subset.py --full-dump <your-file> --ec <EC> [--ec <EC> ...]
--organism-substring "<exact species string>" --out <subset.json>` reads
**only** the file you point it at. It keeps, per requested EC number: every
`protein` entry whose `organism` field contains your exact (case-sensitive)
substring — never promoted to a strain — plus every dataset-field entry
(across BRENDA's ~40 field types) and `reference` entry linked to one of
those matched protein ids. Organism matching is species-level, exactly as
BRENDA wrote it; if BRENDA doesn't record a strain, this skill never invents
one (any incidental strain name only ever appears in a free-text
`comment`, surfaced separately, never promoted to a canonical field — see
`references/strain-vs-species.md`).

## Deterministic canonical-record replay

`scripts/build_canonical_records.py --subset <subset.json>` reads only that
committed subset (no network call, no full-dump read) and emits one record
per (EC, matched protein, kinetic-parameter-type) combination: parsed
value/range/sentinel, ligand name, literature (PMID/title/year when BRENDA
links a reference; `null` — never fabricated — when it doesn't), and an
explicit `manual_vs_text_mined: "unavailable_source: ..."` note (BRENDA's
bulk-JSON `reference_dataset` schema has no `textmining` flag; only the
credential-gated SOAP `getReference`-family calls expose it). Running this
script twice against the same subset produces byte-identical output —
verified in `tests/test_build_canonical_records.py::test_deterministic_replay_is_byte_identical`.

## Value-string grammar

`scripts/brenda_value_parser.py::parse_brenda_numeric_value` turns
`"0.147 {D-fructose 6-phosphate}"`, `"0.05-0.09 {ligand}"`, and
`"-999 {more}"` into a structured, lossless `ParsedBrendaValue`: ranges keep
both bounds, the sentinel keeps `point_value=None` (never coerced to a fake
`-999` reading), and ligand names in `{...}` — including charged-ion names
like `{Zn2+}` — are never mis-split on `+`. Raises `BrendaValueParseError`
on unrecognized input rather than silently guessing.

## Schema validation: surfacing the publisher's own defect

BRENDA's `brenda.schema.json` keys its `data` object with `patternProperties`
but no `additionalProperties: false`, so any EC key that fails the
pattern — `^(?:[1-7]\.[1-9][0-9]{0,2}\.[1-9][0-9]{0,2}\.B?[1-7][0-9]{0,2})|spontaneous$` —
is **silently skipped** from validation, not flagged. That pattern's 4th
(serial) digit group only allows `1-7`-leading values, wrongly excluding
any real EC number whose serial starts with `8`/`9` (confirmed: 729/8,129
EC keys in release 2026.1, including a real target EC `5.3.1.9`).
`scripts/validate_schema.py::validate_entries` always validates every entry
**directly** against `enzyme.schema.json`, bypassing that gate, and reports
`would_be_silently_skipped_by_publisher_pattern_property_gate` per entry so
the defect is visible rather than hidden behind a false "0 errors". See
`references/schema-generation-vs-version.md` for why the payload's own
`version` field (a bulk-download-format generation counter) must never be
conflated with the externally-hosted schema *document* semver, or with the
`release` field (e.g. `"2026.1"`) — three genuinely distinct numbers.

## Opt-in live contract probe

`scripts/live_contract_probe.py` makes **plain GET requests only** (SOAP
WSDL contract shape, the download *page*, the public SPARQL endpoint) to
confirm BRENDA's routes are still reachable. It refuses to run without
explicit consent (`--i-understand-this-makes-a-live-request` or
`BRENDA_LIVE_PROBE=1`), asserts none of its URLs carry `accept-license` or
`dlfile` before issuing any request, and **never** POSTs the download form,
accepts the license, or writes a downloaded file — accepting BRENDA's
license is a separate, deliberate, human action outside this skill. Never
run this from an offline/CI test.

## Files in this skill

- `scripts/brenda_value_parser.py` — value-string grammar parser (ranges,
  sentinel, ligand labels).
- `scripts/extract_subset.py` — user-supplied-file-only subset extractor
  (`--full-dump`, `--ec` (repeatable), `--organism-substring`, `--out`).
- `scripts/build_canonical_records.py` — deterministic canonical-record
  builder over a committed subset (`--subset`).
- `scripts/validate_schema.py` — direct per-entry schema validation +
  publisher-regex-defect reporting (`--subset`).
- `scripts/live_contract_probe.py` — opt-in, license-safe live route probe.
- `fixtures/synthetic_subset.json` — a small, fabricated, clearly-labeled
  (`_synthetic_do_not_use_as_evidence: true`) BRENDA-shaped subset covering
  positive values, a range, both sentinel forms, two disjoint
  species-only organisms with an incidental strain-hint comment, ligand +
  PMID linkage, a missing-PMID reference, the publisher EC-key regex
  defect (`5.3.1.9`), and the release/version/schema-document-version
  distinction. **Never present any value in it as real evidence.**
- `fixtures/brenda_schema/{brenda,enzyme}.schema.json` — BRENDA's own
  officially published JSON Schema (v2.0.0), used for validation tests.
- `tests/` — offline pytest suite, zero network calls: value-parser
  known-answer tests, subset-extraction organism-scoping tests,
  deterministic-replay + known-answer canonical-record tests, schema-defect
  regression tests, and live-probe consent/license-safety guardrail tests.
  Run with `python3 -m pytest tests/ -q` from this skill's directory.
- `references/access-route.md` — license/route summary and gitignore
  guidance for your own downloaded full dump.
- `references/strain-vs-species.md` — why BRENDA's organism field is
  species-level only and how incidental strain hints are handled.
- `references/schema-generation-vs-version.md` — the release / payload
  `version` / schema-document-semver distinction, and the EC-key regex
  defect in full.

## Generalization note

No EC number, organism substring, or file path is hardcoded anywhere in
`scripts/`. When adapting this skill to a new organism or enzyme set, pass
your own values via the CLI flags — never reuse another project's
EC-number/organism constants as if they were general defaults. The fixture
enzymes/organisms used in tests (glucose-6-phosphate dehydrogenase,
glucose-6-phosphate isomerase, RuBisCO; *Escherichia coli*,
*Prochlorococcus marinus*) are fabricated, illustrative placeholders only —
see `fixtures/synthetic_subset.json`'s own `_synthetic_do_not_use_as_evidence`
flag.
