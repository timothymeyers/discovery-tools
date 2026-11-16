#!/usr/bin/env python3
"""Config-driven validator for identifier-crosswalk.schema.json data.

Generalizes the project's `scripts/validate_crosswalk.py` (which hardcoded
paths to one specific 19-entry data file) into a reusable check runnable
against any crosswalk JSON + any independent ChEBI properties file, so the
same validator covers fixtures, new reaction sets, and the real project
artifact without editing code.

Layers checked:

  1. JSON Schema conformance (Draft 2020-12) against the crosswalk schema.
  2. Elemental mass balance and net-charge balance per reaction, using
     `formula_parser.parse_formula` (a real recursive-descent parser with
     bracket/hydrate support) against an INDEPENDENTLY retrieved ChEBI
     formula/charge table -- never the reaction equation's own bookkeeping.
     A formula this parser rejects (R-groups, wildcards, unbalanced
     brackets, unknown element symbols) is reported as an error requiring
     curator attention, never silently skipped.
  3. EC-number consistency: a reaction/enzyme's top-level `ec_number` must
     match a `present`-state `identifiers.ec` mapping (or be explicitly
     absent when unresolved, e.g. a generic `1.2.1.-` isoenzyme class).
  4. ChEBI-identity collision check: two DISTINCT non-generic compound
     entries must not silently claim the same ChEBI ID.
  5. Namespace-coverage report across all 12 required namespaces.

Run:
    python3 validate_crosswalk.py <data.json> --schema <schema.json> \\
        --chebi-props <chebi_properties.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator

sys.path.insert(0, str(Path(__file__).resolve().parent))
from formula_parser import FormulaParseError, parse_formula  # noqa: E402

REQUIRED_NAMESPACES = [
    "model_native", "rhea", "chebi", "ec", "uniprot", "ncbi_taxonomy",
    "biocyc", "cyanocyc", "sabio_rk", "brenda_ligand", "kegg", "metanetx",
]


def load_json(path):
    with open(path) as f:
        return json.load(f)


def check_schema(schema, data):
    errors = []
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    for e in sorted(validator.iter_errors(data), key=lambda e: list(e.path)):
        errors.append(f"[schema] {'/'.join(map(str, e.path)) or '<root>'}: {e.message}")
    return errors


def check_mass_and_charge_balance(entries, chebi_props):
    errors, warnings = [], []
    for entry in entries:
        if entry.get("entity_type") != "reaction":
            continue
        eid = entry["entry_id"]
        stoich = entry.get("stoichiometry") or []
        if not stoich:
            warnings.append(f"[balance] {eid}: reaction entry has no stoichiometry block to check")
            continue
        element_totals = {"substrate": {}, "product": {}}
        charge_totals = {"substrate": 0, "product": 0}
        missing_props, unparseable = [], []
        for p in stoich:
            role, cid, coeff = p["role"], p["chebi_id"], p["coefficient"]
            props = chebi_props.get(cid)
            if props is None:
                missing_props.append(cid)
                continue
            try:
                counts = parse_formula(props["formula"])
            except FormulaParseError as exc:
                unparseable.append(f"{cid} ({props['formula']!r}): {exc}")
                continue
            for el, n in counts.items():
                element_totals[role][el] = element_totals[role].get(el, 0) + n * coeff
            charge_totals[role] += props["charge"] * coeff
        if missing_props:
            errors.append(
                f"[balance] {eid}: no independent ChEBI formula/charge on file for {missing_props}; "
                "cannot verify mass/charge balance for this reaction."
            )
            continue
        if unparseable:
            errors.append(
                f"[balance] {eid}: formula(s) rejected by the parser -- {unparseable}. "
                "Widen the parser grammar deliberately or curate the formula; do not "
                "silently skip the balance check for this reaction."
            )
            continue
        all_elements = set(element_totals["substrate"]) | set(element_totals["product"])
        for el in sorted(all_elements):
            lhs = element_totals["substrate"].get(el, 0)
            rhs = element_totals["product"].get(el, 0)
            if lhs != rhs:
                errors.append(
                    f"[balance] {eid}: element {el} unbalanced -- substrates={lhs} vs products={rhs}"
                )
        if charge_totals["substrate"] != charge_totals["product"]:
            errors.append(
                f"[balance] {eid}: net charge unbalanced -- substrates={charge_totals['substrate']:+d} "
                f"vs products={charge_totals['product']:+d}"
            )
    return errors, warnings


def check_ec_consistency(entries):
    errors = []
    for entry in entries:
        ec_number = entry.get("ec_number")
        if not ec_number:
            continue
        ec_mappings = entry.get("identifiers", {}).get("ec", [])
        present_ids = [m["id"] for m in ec_mappings if m.get("state") == "present" and m.get("id")]
        if not present_ids:
            continue
        if ec_number not in present_ids:
            errors.append(
                f"[ec_consistency] {entry['entry_id']}: top-level ec_number '{ec_number}' does not "
                f"match any present identifiers.ec mapping {present_ids}"
            )
    return errors


def check_chebi_identity_collisions(entries):
    errors = []
    seen = {}
    for entry in entries:
        if entry.get("entity_type") != "compound":
            continue
        chebi_mappings = entry.get("identifiers", {}).get("chebi", [])
        is_generic = entry.get("ambiguity", {}).get("generic_compound", {}).get("applies", False)
        for m in chebi_mappings:
            if m.get("state") != "present":
                continue
            cid = m["id"]
            if cid in seen and not is_generic and not seen[cid][1]:
                errors.append(
                    f"[chebi_identity] duplicate non-generic ChEBI ID {cid} claimed by both "
                    f"{seen[cid][0]} and {entry['entry_id']}"
                )
            seen[cid] = (entry["entry_id"], is_generic)
    return errors


def check_namespace_coverage(entries):
    report = []
    for entry in entries:
        ident = entry.get("identifiers", {})
        row = {"entry_id": entry["entry_id"], "entity_type": entry["entity_type"]}
        for ns in REQUIRED_NAMESPACES:
            states = [m.get("state") for m in ident.get(ns, [])]
            row[ns] = states[0] if len(states) == 1 else states
        report.append(row)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("data", help="Path to a crosswalk data JSON file")
    ap.add_argument("--schema", required=True, help="Path to identifier-crosswalk.schema.json")
    ap.add_argument("--chebi-props", required=True, help="Path to an independent ChEBI properties JSON file")
    args = ap.parse_args(argv)

    schema = load_json(args.schema)
    data = load_json(args.data)
    chebi_props = load_json(args.chebi_props)["compounds"]

    entries = data.get("entries", [])
    all_errors, all_warnings = [], []

    schema_errors = check_schema(schema, data)
    all_errors.extend(schema_errors)

    balance_errors, balance_warnings = check_mass_and_charge_balance(entries, chebi_props)
    all_errors.extend(balance_errors)
    all_warnings.extend(balance_warnings)

    all_errors.extend(check_ec_consistency(entries))
    all_errors.extend(check_chebi_identity_collisions(entries))

    coverage = check_namespace_coverage(entries)

    print(f"Loaded {len(entries)} crosswalk entries from {args.data}")
    print(f"Schema errors: {len(schema_errors)}")
    print(f"Mass/charge balance errors: {len(balance_errors)} (warnings: {len(balance_warnings)})")
    print()

    if all_warnings:
        print("WARNINGS:")
        for w in all_warnings:
            print(f"  - {w}")
        print()

    if all_errors:
        print("ERRORS:")
        for e in all_errors:
            print(f"  - {e}")
        print()
        print(f"FAIL: {len(all_errors)} error(s) found.")
    else:
        print("PASS: no schema, mass/charge-balance, EC-consistency, or ChEBI-identity errors found.")

    print()
    print("Namespace coverage (state per source, per entry):")
    for row in coverage:
        print(f"  {row['entry_id']} ({row['entity_type']}):")
        for ns in REQUIRED_NAMESPACES:
            print(f"    {ns}: {row[ns]}")

    return 1 if all_errors else 0


if __name__ == "__main__":
    sys.exit(main())
