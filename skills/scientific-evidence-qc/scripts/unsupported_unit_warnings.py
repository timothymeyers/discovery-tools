#!/usr/bin/env python3
"""Warn (never silently skip) on unit-conversion pairs missing a known factor.

Reusable, source-agnostic helper for `scientific-evidence-qc`. See
`references/unit-conversion-and-traceability.md` for why an unsupported
`(unit_raw, unit_normalized)` pair must surface as an explicit warning
finding rather than vanish silently from a QC report.

Dependency-light (stdlib only). Run directly for a self-test against the
bundled synthetic fixture:

    python3 scripts/unsupported_unit_warnings.py
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE = SCRIPT_DIR.parent / "fixtures" / "synthetic_corpus_sample.json"

# Small illustrative allow-list. A real project maintains its own table
# (project-specific values belong in that project's fixtures, not hardcoded
# here) -- this is deliberately tiny so the self-test below exercises both
# the "checked" and "warned" paths.
EXAMPLE_UNIT_FACTORS: dict[tuple[str, str], float] = {
    ("mM", "M"): 1e-3,
    ("uM", "M"): 1e-6,
}


def check_unit_conversions(
    records: Iterable[dict[str, Any]],
    unit_factors: dict[tuple[str, str], float],
    rel_tolerance: float = 1e-3,
) -> dict[str, Any]:
    """Recompute raw*factor vs normalized for known pairs; warn on unknown pairs.

    Returns a dict with:
      - "checked": count of records whose unit pair was in the factor table
      - "mismatches": list of record_ids where raw*factor != normalized
      - "unsupported_pairs": Counter of (unit_raw, unit_normalized) pairs NOT
        in the factor table, with how many records each affects -- this is
        the required warning output, never a silent skip.
    """
    checked = 0
    mismatches: list[str] = []
    unsupported_pairs: Counter[tuple[str, str]] = Counter()

    for r in records:
        pv = r.get("kinetics", {}).get("parameter_value", {})
        if pv.get("state") != "present":
            continue
        raw = pv.get("raw_value")
        norm = pv.get("normalized_value")
        unit_pair = (pv.get("unit_raw"), pv.get("unit_normalized"))
        if raw is None or norm is None or unit_pair == (None, None):
            continue

        factor = unit_factors.get(unit_pair)
        if factor is None:
            unsupported_pairs[unit_pair] += 1
            continue

        checked += 1
        if isinstance(raw, (int, float)) and isinstance(norm, (int, float)):
            expected = raw * factor
            tol = max(1e-12, abs(norm) * rel_tolerance)
            if abs(expected - norm) > tol:
                mismatches.append(r.get("record_id", "<unknown>"))

    return {
        "checked": checked,
        "mismatches": mismatches,
        # Convert tuple keys to "raw->normalized" strings for JSON-friendliness.
        "unsupported_pairs": {f"{a}->{b}": n for (a, b), n in unsupported_pairs.items()},
    }


def _self_test() -> None:
    doc = json.loads(DEFAULT_FIXTURE.read_text())
    result = check_unit_conversions(doc["records"], EXAMPLE_UNIT_FACTORS)

    # Km (mM->M) is in the table and converts correctly.
    assert result["checked"] == 1, result
    assert result["mismatches"] == [], result
    # kcat has no unit change (s-1->s-1, not in the mM/uM table) and the
    # IC50 record uses a deliberately fabricated unit pair not in the table
    # -- both must surface as warnings, never disappear silently.
    assert result["unsupported_pairs"] == {
        "s-1->s-1": 1,
        "furlongs_per_fortnight->furlongs_per_fortnight_si": 1,
    }, result

    print("self-test OK against", DEFAULT_FIXTURE.name)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    _self_test()
    sys.exit(0)
