#!/usr/bin/env python3
"""Parameterized extraction of a small, redistributable subset from a
user-supplied BRENDA bulk JSON release.

This script never reads a bundled or repo-local full dump. The caller must
supply the path to their own licensed BRENDA bulk JSON download (obtained by
accepting BRENDA's license on `download.php` themselves -- see
`references/access-route.md`). No EC number, organism, or file path is
hardcoded here; every one of them is a CLI parameter, so this skill is not
scoped to any single project's target enzymes/organisms.

Extraction scope (mirrors the pattern validated in this workspace's BRENDA
ingestion passes): for each requested EC number, keep only `protein` entries
whose `organism` field contains the caller-supplied organism substring
(case-sensitive, matched exactly as BRENDA wrote it -- never promoted to a
strain), every dataset-field entry (across BRENDA's ~40 field types) that
references one of those matched protein ids, and every `reference` entry
cited by a matched field. This keeps the output small while preserving every
value/ligand/literature link needed to reconstruct canonical records.

Usage:
    python3 extract_subset.py \
        --full-dump /path/to/your/licensed/brenda_2026_1.json \
        --ec 1.1.1.49 --ec 2.7.1.11 \
        --organism-substring "Escherichia coli" \
        --out subset.json

The full dump is read only from the caller-supplied `--full-dump` path; this
script does not know about, and will not search for, any bundled or
repo-local bulk file.
"""
from __future__ import annotations

import argparse
import json
import pathlib

# Every BRENDA dataset field type that can carry a `proteins: [...]`
# cross-reference (numeric_dataset and text_dataset types alike), so nothing
# linked to a matched protein id is silently dropped.
DATASET_FIELDS = [
    "source_tissue",
    "localization",
    "natural_substrates_products",
    "substrates_products",
    "turnover_number",
    "km_value",
    "ph_optimum",
    "ph_range",
    "specific_activity",
    "temperature_optimum",
    "temperature_range",
    "activating_compound",
    "inhibitor",
    "metals_ions",
    "molecular_weight",
    "posttranslational_modification",
    "subunits",
    "pi_value",
    "application",
    "protein_variants",
    "cloned",
    "crystallization",
    "purification",
    "renatured",
    "general_stability",
    "oxidation_stability",
    "ph_stability",
    "storage_stability",
    "temperature_stability",
    "ki_value",
    "ic50_value",
    "kcat_km_value",
    "expression",
    "general_information",
]

ENTRY_SCALAR_FIELDS = [
    "id",
    "recommended_name",
    "systematic_name",
    "synonyms",
    "reaction",
    "reaction_type",
]


def extract(full_dump_path: pathlib.Path, target_ecs: list[str], organism_substring: str) -> dict:
    with open(full_dump_path, encoding="utf-8") as f:
        full = json.load(f)

    out_data = {}
    missing_ecs = []
    for ec in target_ecs:
        entry = full["data"].get(ec)
        if entry is None:
            missing_ecs.append(ec)
            continue
        protein = entry.get("protein", {})

        matched_pids = {
            pid: p
            for pid, p in protein.items()
            if organism_substring in (p.get("organism") or "")
        }

        trimmed_entry = {f: entry[f] for f in ENTRY_SCALAR_FIELDS if f in entry}
        trimmed_entry["protein"] = matched_pids

        referenced_ids = set()
        for p in matched_pids.values():
            referenced_ids.update(p.get("references", []))

        for field in DATASET_FIELDS:
            matched = [
                v
                for v in entry.get(field, [])
                if any(p in v.get("proteins", []) for p in matched_pids)
            ]
            if matched:
                trimmed_entry[field] = matched
                for v in matched:
                    referenced_ids.update(v.get("references", []))

        all_refs = entry.get("reference", {})
        trimmed_entry["reference"] = {
            rid: all_refs[rid] for rid in sorted(referenced_ids, key=int) if rid in all_refs
        }

        out_data[ec] = trimmed_entry

    return {
        "release": full.get("release"),
        "version": full.get("version"),
        "extraction_scope_note": (
            f"Trimmed subset: {len(target_ecs)} caller-requested EC number(s), "
            f"restricted to `protein` entries whose organism field contains the "
            f"exact substring {organism_substring!r}, plus every dataset-field "
            "entry and reference linked to one of those protein ids."
        ),
        "target_ecs_not_found_in_dump": missing_ecs,
        "data": out_data,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--full-dump",
        required=True,
        type=pathlib.Path,
        help="Path to YOUR OWN licensed BRENDA bulk JSON download. Never a bundled/repo file.",
    )
    ap.add_argument(
        "--ec",
        action="append",
        required=True,
        dest="ecs",
        help="EC number to extract (repeatable, e.g. --ec 1.1.1.49 --ec 2.7.1.11).",
    )
    ap.add_argument(
        "--organism-substring",
        required=True,
        help='Exact (case-sensitive) organism substring to match, e.g. "Escherichia coli".',
    )
    ap.add_argument("--out", required=True, type=pathlib.Path)
    args = ap.parse_args()

    result = extract(args.full_dump, args.ecs, args.organism_substring)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, sort_keys=True)
        f.write("\n")

    total_protein = sum(len(v["protein"]) for v in result["data"].values())
    print(
        f"wrote {args.out}: {len(result['data'])} EC entries, "
        f"{total_protein} matched protein records"
        + (f"; NOT FOUND in dump: {result['target_ecs_not_found_in_dump']}" if result["target_ecs_not_found_in_dump"] else "")
    )


if __name__ == "__main__":
    main()
