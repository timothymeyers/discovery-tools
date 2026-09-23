#!/usr/bin/env python3
"""Deterministic, offline canonical-record builder over a committed BRENDA
subset (the output of `extract_subset.py`).

Reads ONLY the caller-supplied subset file (no network call, no full-dump
read) and reconstructs one record per (EC, matched protein, kinetic
parameter type) combination, preserving BRENDA's own value grammar (ranges,
`-999`/`-999 {more}` sentinel) via `brenda_value_parser.py`. Running this
script twice against the same subset, with no other changes, must produce
byte-identical JSON output -- this is what this skill's replay tests verify.

No EC number or organism is hardcoded; both come from whatever the subset
file itself contains.

Usage:
    python3 build_canonical_records.py --subset subset.json > records.json
    python3 build_canonical_records.py --subset subset.json > pass2.json
    diff records.json pass2.json   # expect empty
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from brenda_value_parser import parse_brenda_numeric_value  # noqa: E402

KINETIC_FIELDS = ["km_value", "turnover_number", "kcat_km_value", "ki_value", "specific_activity"]

# Free-text tokens that flag a kinetics comment as carrying an incidental
# strain hint. This is a heuristic over BRENDA's own free text, never a
# canonical strain assignment -- see build_records()'s strain_note.
STRAIN_HINT_TOKENS = ("strain",)


def build_records(subset: dict) -> dict:
    records = []
    for ec, entry in sorted(subset["data"].items()):
        protein = entry.get("protein", {})
        references = entry.get("reference", {})

        for pid, p in sorted(protein.items(), key=lambda kv: int(kv[0])):
            organism = p.get("organism", "")
            strain_note = (
                "BRENDA organism field is species-level only "
                f"('{organism}'); this record does not assert or infer any "
                "strain beyond what BRENDA's own free-text comment/reference "
                "fields state (see strain_hints_from_comments, never silently "
                "promoted to a canonical strain identifier)."
            )
            strain_hints = []
            for field in KINETIC_FIELDS:
                for v in entry.get(field, []):
                    if pid in v.get("proteins", []):
                        c = v.get("comment", "")
                        if any(tok in c.lower() for tok in STRAIN_HINT_TOKENS):
                            strain_hints.append(c)

            uniprot_accessions = p.get("accessions", [])

            kinetic_observations = []
            for field in KINETIC_FIELDS:
                for v in entry.get(field, []):
                    if pid not in v.get("proteins", []):
                        continue
                    parsed = parse_brenda_numeric_value(v["value"])
                    ref_ids = v.get("references", [])
                    kinetic_observations.append(
                        {
                            "parameter_type": field,
                            "raw_value": parsed.raw,
                            "ligand": parsed.substrate_or_inhibitor_name,
                            "is_sentinel_more": parsed.is_sentinel_more,
                            "is_range": parsed.is_range,
                            "point_value": parsed.point_value,
                            "range_low": parsed.range_low,
                            "range_high": parsed.range_high,
                            "assay_comment": v.get("comment", ""),
                            "literature": [
                                {
                                    "brenda_reference_id": rid,
                                    "pmid": references.get(rid, {}).get("pmid"),
                                    "title": references.get(rid, {}).get("title"),
                                    "year": references.get(rid, {}).get("year"),
                                }
                                for rid in ref_ids
                            ],
                            "manual_vs_text_mined": (
                                "unavailable_source: BRENDA bulk-JSON reference_dataset "
                                "schema has no textmining flag; only the credential-gated "
                                "SOAP getReference-family operations expose this flag."
                            ),
                        }
                    )

            records.append(
                {
                    "ec_number": ec,
                    "enzyme_name": entry.get("recommended_name"),
                    "brenda_protein_id": pid,
                    "organism_species": organism,
                    "strain_note": strain_note,
                    "strain_hints_from_comments": sorted(set(strain_hints)),
                    "uniprot_accessions_if_present": uniprot_accessions,
                    "kinetic_observations": kinetic_observations,
                    "provenance": {
                        "source_release": subset.get("release"),
                        "source_schema_version": subset.get("version"),
                    },
                }
            )

    return {"records": records}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subset", required=True, type=pathlib.Path)
    args = ap.parse_args()

    subset = json.loads(args.subset.read_text(encoding="utf-8"))
    result = build_records(subset)
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
