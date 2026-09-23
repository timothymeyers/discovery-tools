#!/usr/bin/env python3
"""Validate a BRENDA bulk-JSON payload (or subset) against BRENDA's own
published JSON Schema, while surfacing a genuine publisher-side defect
rather than hiding it behind a misleadingly clean "0 errors" corpus result.

BRENDA's `brenda.schema.json` declares `data` as an object with
`patternProperties` keyed by an EC-number-shaped regex, but no
`additionalProperties: false` on that nested object -- so any EC key that
fails to match the pattern is silently skipped from `enzyme.schema.json`
validation entirely, rather than flagged as invalid. The pattern itself
restricts the EC number's 4th (serial) digit group to `B?[1-7][0-9]{0,2}`,
which wrongly excludes any real EC number whose serial number starts with
8 or 9 (e.g. `5.3.1.9`, `1.1.1.49` does NOT trigger this -- its 4th group is
`49`, which does match `[1-7][0-9]{0,2}`; but `5.3.1.9`'s 4th group `9`
does not match `[1-7]...` at all since it starts with 9).

This script always validates every requested entry DIRECTLY against
`enzyme.schema.json`, bypassing the corpus-level patternProperties gate, so
this silent-skip behavior can never mask a real validation error.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re

try:
    import jsonschema
    from jsonschema import Draft202012Validator
except ImportError:  # pragma: no cover - exercised only when jsonschema is absent
    jsonschema = None
    Draft202012Validator = None

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent.parent / "fixtures" / "brenda_schema"
BRENDA_SCHEMA_PATH = FIXTURES_DIR / "brenda.schema.json"
ENZYME_SCHEMA_PATH = FIXTURES_DIR / "enzyme.schema.json"

# The exact publisher pattern this script checks against (mirrored from the
# real, officially published schema; see references/schema-generation-vs-version.md).
_EC_KEY_PATTERN = re.compile(
    r"^(?:[1-7]\.[1-9][0-9]{0,2}\.[1-9][0-9]{0,2}\.B?[1-7][0-9]{0,2})|spontaneous$"
)


def matches_publisher_ec_key_pattern(ec: str) -> bool:
    """Whether `ec` would be picked up by brenda.schema.json's own
    `patternProperties` regex (and therefore actually validated against
    enzyme.schema.json in a naive corpus-level pass)."""
    return bool(_EC_KEY_PATTERN.match(ec))


def load_schemas() -> tuple[dict, dict]:
    brenda_schema = json.loads(BRENDA_SCHEMA_PATH.read_text(encoding="utf-8"))
    enzyme_schema = json.loads(ENZYME_SCHEMA_PATH.read_text(encoding="utf-8"))
    return brenda_schema, enzyme_schema


def validate_entries(data: dict) -> dict:
    """Validate every EC entry in `data` directly against enzyme.schema.json,
    and separately report which EC keys the publisher's own corpus-level
    patternProperties regex would have silently skipped."""
    if jsonschema is None:
        raise RuntimeError("the 'jsonschema' package is required to run schema validation")

    _brenda_schema, enzyme_schema = load_schemas()
    validator = Draft202012Validator(enzyme_schema)

    results = {}
    silently_skipped_by_publisher_pattern = []
    for ec, entry in data.items():
        if not matches_publisher_ec_key_pattern(ec):
            silently_skipped_by_publisher_pattern.append(ec)
        errors = sorted(validator.iter_errors(entry), key=lambda e: e.path)
        results[ec] = {
            "valid": len(errors) == 0,
            "errors": [str(e.message) for e in errors],
            "would_be_silently_skipped_by_publisher_pattern_property_gate": not matches_publisher_ec_key_pattern(ec),
        }

    return {
        "per_ec_result": results,
        "silently_skipped_by_publisher_pattern_property_gate": silently_skipped_by_publisher_pattern,
        "note": (
            "Every entry above was validated DIRECTLY against enzyme.schema.json, "
            "so the publisher's patternProperties silent-skip defect (see module "
            "docstring) cannot mask a genuine error here. 'would_be_silently_"
            "skipped_by_publisher_pattern_property_gate'=true means a naive "
            "corpus-level validation pass (relying only on brenda.schema.json's "
            "patternProperties) would never have checked this entry at all."
        ),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subset", required=True, type=pathlib.Path, help="Subset or full-dump JSON to validate.")
    args = ap.parse_args()

    payload = json.loads(args.subset.read_text(encoding="utf-8"))
    result = validate_entries(payload["data"])
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
