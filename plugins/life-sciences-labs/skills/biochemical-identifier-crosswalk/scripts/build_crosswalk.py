#!/usr/bin/env python3
"""Config-driven builder for identifier-crosswalk.schema.json data.

Replaces the pattern in the project's `scripts/build_identifier_crosswalk.py`,
where every reaction/compound/enzyme was a hand-assembled Python literal
(three reactions, twelve compounds, four enzymes, all baked into the script
itself). Adding a new entity there means editing Python; reviewing it means
reading code. Here, entities are declarative YAML records (see
`fixtures/configs/*.yaml` for worked examples) and this script only fills in
structural defaults, enforces the hard identity/ambiguity rules, and emits
schema-conformant JSON. Adding a new reaction/compound/enzyme means adding a
YAML record; no code change.

Hard rules enforced here (not just documented):

  1. Every REACTION record MUST carry `direction_evidence.resolved_from:
     equation_text` plus the actual retrieved `equation_text`. A config that
     tries to justify direction from a Rhea LR/RL label alone (or omits
     `direction_evidence` entirely) is rejected -- Rhea's LR/RL suffix is an
     internal ChEBI-ID-ordering convention, not a biology-direction claim.
  2. A reaction/compound whose ONLY 'present'-state cross-source identifiers
     use `smiles_structure_match` and/or `ec_number_match` (i.e. no
     `direct_xref_file`/`rest_api_lookup`/`bulk_export_lookup` hit anywhere)
     is flagged in the build report as identity-unconfirmed. Structure/EC
     matches are corroborating signals, never sole proof of identity.
  3. Every stoichiometry participant must declare `role_class`
     (`currency` | `generic` | `specific`). `currency` (ATP, ADP, Pi, CO2,
     H2O, H+, NAD(P)(H), ...) means "widely shared, chemically well-defined,
     but reaction-identity-weak" -- structurally NOT the same concept as
     `generic` (chemically underspecified: R-groups, unresolved
     stereochemistry, wildcard positions). Conflating the two is a rejected
     config.
  4. A compound record participating as `role_class: generic` in any
     reaction MUST set `ambiguity.generic_compound.applies: true`; a
     `currency` participant must NOT (currency is not an ambiguity --
     it is a reaction-identity-strength caveat, tracked in the build report,
     not in the ambiguity block).
  5. Every namespace in
     model_native/rhea/chebi/ec/uniprot/ncbi_taxonomy/biocyc/cyanocyc/
     sabio_rk/brenda_ligand/kegg/metanetx is filled with an explicit
     unknown/not_applicable placeholder if the config does not populate it,
     so coverage gaps stay visible instead of silently absent keys.

Run:
    python3 build_crosswalk.py <config.yaml> [<config2.yaml> ...] -o out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REQUIRED_NAMESPACES = [
    "model_native", "rhea", "chebi", "ec", "uniprot", "ncbi_taxonomy",
    "biocyc", "cyanocyc", "sabio_rk", "brenda_ligand", "kegg", "metanetx",
]

WEAK_IDENTITY_METHODS = {"smiles_structure_match", "ec_number_match"}
STRONG_IDENTITY_METHODS = {"direct_xref_file", "rest_api_lookup", "bulk_export_lookup"}

DEFAULT_COMPARTMENT_NOTE = (
    "Rhea/ChEBI reaction and compound definitions are compartment-agnostic "
    "(aqueous-phase only). Compartment is 'unknown' unless a project model "
    "artifact explicitly asserts a localization for this entity."
)


class ConfigError(ValueError):
    """A config record violates a hard rule; refuse to build rather than
    silently emit a weaker/ambiguity-destroying record."""


def _placeholder(namespace: str) -> dict:
    return {
        "source": namespace,
        "state": "unknown",
        "method": "not_applicable",
        "confidence": "not_applicable",
        "note": "Not populated in the source config; no in-scope query targeted "
                 "this namespace/entity combination.",
    }


def _fill_identifiers(entry_id: str, identifiers_cfg: dict) -> dict:
    identifiers_cfg = identifiers_cfg or {}
    unknown_ns = [ns for ns in identifiers_cfg if ns not in REQUIRED_NAMESPACES]
    if unknown_ns:
        raise ConfigError(f"{entry_id}: unknown namespace(s) in identifiers: {unknown_ns}")
    out = {}
    for ns in REQUIRED_NAMESPACES:
        vals = identifiers_cfg.get(ns)
        out[ns] = vals if vals else [_placeholder(ns)]
    return out


def _default_flag() -> dict:
    return {"applies": False, "state": "not_applicable"}


def _fill_ambiguity(entry_id: str, ambiguity_cfg: dict) -> dict:
    ambiguity_cfg = ambiguity_cfg or {}
    required = ["direction", "protonation", "compartment", "generic_compound", "isoenzyme"]
    unknown = [k for k in ambiguity_cfg if k not in required]
    if unknown:
        raise ConfigError(f"{entry_id}: unknown ambiguity dimension(s): {unknown}")
    out = {}
    for dim in required:
        if dim in ambiguity_cfg:
            out[dim] = ambiguity_cfg[dim]
        elif dim == "compartment":
            out[dim] = {"applies": True, "state": "unknown", "description": DEFAULT_COMPARTMENT_NOTE}
        else:
            out[dim] = _default_flag()
    return out


def _check_direction_evidence(entry_id: str, entity_type: str, cfg: dict, warnings: list):
    if entity_type != "reaction":
        return
    de = cfg.get("direction_evidence")
    if not de:
        raise ConfigError(
            f"{entry_id}: reaction records MUST declare direction_evidence "
            "(resolved_from + equation_text); the Rhea LR/RL suffix alone is "
            "not a biology-direction claim."
        )
    if de.get("resolved_from") != "equation_text":
        raise ConfigError(
            f"{entry_id}: direction_evidence.resolved_from must be 'equation_text' "
            f"(got {de.get('resolved_from')!r}); direction must be read from the "
            "actual retrieved equation, never assumed from an LR/RL label."
        )
    if not de.get("equation_text"):
        raise ConfigError(f"{entry_id}: direction_evidence.equation_text is required and empty.")


def _check_identity_strength(entry_id: str, identifiers: dict, warnings: list):
    present_methods = set()
    for ns, vals in identifiers.items():
        if ns == "model_native":
            continue
        for v in vals:
            if v.get("state") == "present" and v.get("method"):
                present_methods.add(v["method"])
    if not present_methods:
        return
    if present_methods and present_methods <= WEAK_IDENTITY_METHODS:
        warnings.append(
            f"[identity_strength] {entry_id}: every cross-source 'present' mapping uses only "
            f"{sorted(present_methods)} -- no direct_xref_file/rest_api_lookup/bulk_export_lookup "
            "hit anywhere. Name/structure similarity or a shared EC number is NOT sufficient proof "
            "of reaction/compound identity on its own; treat this entry as identity-unconfirmed "
            "until a stronger cross-reference is found."
        )


def _check_role_class(entry_id: str, stoich: list, ambiguity: dict, warnings: list):
    if not stoich:
        return
    currency = []
    generic = []
    for p in stoich:
        rc = p.get("role_class")
        if rc not in ("currency", "generic", "specific"):
            raise ConfigError(
                f"{entry_id}: stoichiometry participant {p.get('chebi_id')} must declare "
                "role_class as one of currency/generic/specific."
            )
        if rc == "currency":
            currency.append(p["chebi_id"])
        elif rc == "generic":
            generic.append(p["chebi_id"])
    if generic and not ambiguity.get("generic_compound", {}).get("applies"):
        raise ConfigError(
            f"{entry_id}: has role_class=generic participant(s) {generic} but "
            "ambiguity.generic_compound.applies is not true."
        )
    if currency:
        warnings.append(
            f"[currency_metabolite] {entry_id}: participant(s) {currency} are currency "
            "metabolites (widely shared, chemically well-defined) -- distinct from "
            "generic_compound ambiguity; do not use them alone as reaction-identity evidence."
        )


def build_entry(cfg: dict, warnings: list) -> dict:
    entry_id = cfg.get("entry_id")
    if not entry_id:
        raise ConfigError("entry missing entry_id")
    entity_type = cfg.get("entity_type")
    if entity_type not in ("reaction", "compound", "gene", "enzyme"):
        raise ConfigError(f"{entry_id}: entity_type must be reaction/compound/gene/enzyme")

    identifiers = _fill_identifiers(entry_id, cfg.get("identifiers"))
    ambiguity = _fill_ambiguity(entry_id, cfg.get("ambiguity"))
    _check_direction_evidence(entry_id, entity_type, cfg, warnings)
    _check_identity_strength(entry_id, identifiers, warnings)

    stoich = cfg.get("stoichiometry") or []
    _check_role_class(entry_id, stoich, ambiguity, warnings)
    # role_class is build-time-only metadata (drives the checks above); the
    # schema's StoichiometryParticipant does not carry it, so strip it from
    # the emitted record rather than fail additionalProperties validation.
    clean_stoich = [
        {k: v for k, v in p.items() if k != "role_class"} for p in stoich
    ]

    out = {
        "entry_id": entry_id,
        "entity_type": entity_type,
        "canonical_name": cfg["canonical_name"],
        "identifiers": identifiers,
        "ambiguity": ambiguity,
        "provenance": cfg["provenance"],
        "transformation_history": cfg.get("transformation_history", []),
    }
    if cfg.get("ec_number") is not None:
        out["ec_number"] = cfg["ec_number"]
    if cfg.get("compartment") is not None:
        out["compartment"] = cfg["compartment"]
    if clean_stoich:
        out["stoichiometry"] = clean_stoich
    if cfg.get("organism"):
        out["organism"] = cfg["organism"]
    if cfg.get("notes"):
        out["notes"] = cfg["notes"]
    return out


def build(configs: list, generated_at: str, source_schema_dependency: str):
    warnings = []
    entries = []
    seen_ids = set()
    for cfg_path in configs:
        doc = yaml.safe_load(Path(cfg_path).read_text())
        for entry_cfg in doc.get("entries", []):
            entry = build_entry(entry_cfg, warnings)
            if entry["entry_id"] in seen_ids:
                raise ConfigError(f"duplicate entry_id across configs: {entry['entry_id']}")
            seen_ids.add(entry["entry_id"])
            entries.append(entry)
    data = {
        "schema_version": "1.0.0",
        "generated_at": generated_at,
        "source_schema_dependency": source_schema_dependency,
        "entries": entries,
    }
    return data, warnings


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("configs", nargs="+", help="One or more YAML config files")
    ap.add_argument("-o", "--output", required=True, help="Output JSON path")
    ap.add_argument("--generated-at", required=True, help="ISO-8601 UTC timestamp for generated_at")
    ap.add_argument(
        "--source-schema-dependency",
        default="https://discovery.local/schemas/reaction-evidence/1.0.0/reaction-evidence.schema.json",
    )
    args = ap.parse_args(argv)

    try:
        data, warnings = build(args.configs, args.generated_at, args.source_schema_dependency)
    except ConfigError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1

    Path(args.output).write_text(json.dumps(data, indent=2, sort_keys=False) + "\n")
    print(f"Wrote {len(data['entries'])} entries to {args.output}")
    if warnings:
        print(f"{len(warnings)} build-time warning(s):")
        for w in warnings:
            print(f"  - {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
