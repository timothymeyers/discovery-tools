# Release, Payload Version, and Schema-Document Version: Three Distinct Numbers

A BRENDA bulk-JSON download carries (at least) three separately-versioned
numbers. Confusing any two of them is a real mistake this skill's tests
guard against.

| Field | Example | What it answers | Where it lives |
|---|---|---|---|
| `release` | `"2026.1"` | Which BRENDA content **release** (a periodic content snapshot, e.g. "March 2026") did this download come from? | Top-level `release` key in the downloaded JSON |
| `version` | `"1"` | Which **payload-format generation** is this JSON download itself (BRENDA's own bulk-download format revision counter)? | Top-level `version` key in the downloaded JSON |
| schema document semver | `"2.0.0"` | Which version of the **schema *document*** (`brenda.schema.json`/`enzyme.schema.json`) validates this payload's shape? | The `$id` URL path segment of the schema documents themselves, e.g. `.../schemas/2.0.0/brenda.schema.json` |

None of these is authoritative over the others, and none should silently
overwrite another in your own records. Record all three verbatim.

## The publisher's EC-key regex defect

`brenda.schema.json`'s `data` object declares:

```json
"patternProperties": {
    "^(?:[1-7]\\.[1-9][0-9]{0,2}\\.[1-9][0-9]{0,2}\\.B?[1-7][0-9]{0,2})|spontaneous$": {
        "$ref": "https://www.brenda-enzymes.org/schemas/2.0.0/enzyme.schema.json"
    }
}
```

with **no `additionalProperties: false`** on that object. This means: any
EC-number key that doesn't match the pattern above is **silently excluded**
from `enzyme.schema.json` validation during a naive corpus-level pass — not
flagged as invalid, just never checked at all.

The pattern's 4th (serial) EC digit group, `B?[1-7][0-9]{0,2}`, only allows
values starting with digits `1`–`7`. Any real EC number whose serial number
starts with `8` or `9` — e.g. `5.3.1.9` — **fails this pattern** and is
silently skipped. Directly quantified against BRENDA release 2026.1: **729
of 8,129 EC keys (~9.0%)** fail this pattern, including at least one real,
in-use EC number.

Validating such an entry **directly** against `enzyme.schema.json`
(bypassing the broken `patternProperties` gate) surfaces the truth
honestly: the entry's own `id` field (e.g. `"5.3.1.9"`, copied verbatim
from real data) fails the publisher's own buggy pattern, while every other
field on that entry validates cleanly. This is a genuine defect in BRENDA's
published schema — not a defect in extracted data, and not something this
skill patches, hides, or works around silently.

`scripts/validate_schema.py::validate_entries` always validates every
requested entry directly against `enzyme.schema.json` for this reason, and
reports `would_be_silently_skipped_by_publisher_pattern_property_gate` per
entry so callers can see exactly which entries a naive corpus-level pass
would have missed.

## What to do if you find this

Report it upstream to BRENDA/DSMZ as a schema bug. This skill does not fix,
patch, or silently work around the published schema — it mirrors it
verbatim (see `fixtures/brenda_schema/`) and reports the defect's real
scope instead.
