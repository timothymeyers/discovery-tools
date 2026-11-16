#!/usr/bin/env python3
"""Separated evidence-coverage counts for reaction-evidence-schema records.

Reusable, source-agnostic helper for `scientific-evidence-qc`. See
`references/coverage-counting.md` for why a single blended "record count" is
insufficient and what each of these four categories means.

Dependency-light (stdlib only). Run directly for a self-test against the
bundled synthetic fixture:

    python3 scripts/coverage_counts.py

No project-specific organism/taxid/identifier assumptions live here -- pass
in whatever corpus you're evaluating.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_FIXTURE = SCRIPT_DIR.parent / "fixtures" / "synthetic_corpus_sample.json"


def separate_evidence_counts(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Compute law / parameter-row / numeric-measurement / publication counts.

    - "laws": distinct provenance.source_record_id values (one law/entry may
      fan out into several parameter rows -- see coverage-counting.md).
    - "parameter_rows": total record count (one row per observation).
    - "numeric_measurements": records whose kinetics.parameter_value.state is
      "present" and whose normalized_value is a real number (explicit gaps
      such as "unknown"/"not_applicable"/"unavailable_source"/
      "deferred_resolution" are reported separately, never folded in).
    - "gap_states": count of parameter rows by non-"present" missingness
      state (the explicit-gap breakdown).
    - "distinct_publications": distinct publication.pubmed.id values with
      state == "present".
    - "multi_record_publications": publication ids cited by more than one
      record, as a linked group (record ids), never silently deduplicated.
    """
    records = list(records)

    laws: set[str] = set()
    numeric_measurements = 0
    gap_states: Counter[str] = Counter()
    pub_to_records: dict[str, list[str]] = {}

    for r in records:
        source_record_id = r.get("provenance", {}).get("source_record_id")
        if source_record_id:
            laws.add(source_record_id)

        pv = r.get("kinetics", {}).get("parameter_value", {})
        state = pv.get("state")
        if state == "present" and isinstance(pv.get("normalized_value"), (int, float)) \
                and not isinstance(pv.get("normalized_value"), bool):
            numeric_measurements += 1
        elif state:
            gap_states[state] += 1

        pubmed = r.get("publication", {}).get("pubmed", {})
        if pubmed.get("state") == "present" and pubmed.get("id"):
            pub_to_records.setdefault(pubmed["id"], []).append(r.get("record_id", "<unknown>"))

    multi_record_publications = {
        pmid: rec_ids for pmid, rec_ids in pub_to_records.items() if len(rec_ids) > 1
    }

    return {
        "laws": len(laws),
        "parameter_rows": len(records),
        "numeric_measurements": numeric_measurements,
        "gap_states": dict(gap_states),
        "distinct_publications": len(pub_to_records),
        "multi_record_publications": multi_record_publications,
    }


def _self_test() -> None:
    doc = json.loads(DEFAULT_FIXTURE.read_text())
    counts = separate_evidence_counts(doc["records"])

    assert counts["laws"] == 3, counts
    assert counts["parameter_rows"] == 4, counts
    assert counts["numeric_measurements"] == 3, counts
    assert counts["gap_states"] == {"unknown": 1}, counts
    assert counts["distinct_publications"] == 2, counts
    assert set(counts["multi_record_publications"]) == {"SYN0001"}, counts
    assert len(counts["multi_record_publications"]["SYN0001"]) == 3, counts

    print("self-test OK against", DEFAULT_FIXTURE.name)
    print(json.dumps(counts, indent=2))


if __name__ == "__main__":
    _self_test()
    sys.exit(0)
