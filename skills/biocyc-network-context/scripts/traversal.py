#!/usr/bin/env python3
"""Parameterized bounded BioCyc/EcoCyc/CyanoCyc network-context traversal:
reaction -> enzyme/complex -> gene -> pathway.

Generalizes the traversal validated twice against real BioCyc-family PGDBs
(DX-30 against MED4/CyanoCyc, DX-53 against ECOLI/EcoCyc) into a single,
organism-agnostic function. Every organism, orgid, frame ID, EC number, and
taxid is a caller-supplied parameter -- nothing here is scoped to any one
organism or project.

Design goals (beyond what either source ingestion pass did on its own):

1. **Preserve every catalyst, never collapse isoenzymes.** A reaction frame
   can carry more than one `<Enzymatic-Reaction>` (e.g. EcoCyc's
   `PGLUCISOM-RXN`, catalyzed by both the canonical Pgi homodimer and a
   cross-reactive KduI homodimer). Every one is walked and returned as its
   own `GeneEnzymeLink`, each correctly attributed to its own gene identity
   -- never merged into a single "the" enzyme for that EC number.
2. **True complex -> subunit -> gene expansion**, not a hardcoded
   protein-to-gene shortcut. A `Protein` frame with `<component>` children
   is a multi-subunit complex; each component subunit is itself fetched and
   its *own* `<gene>` link resolved. A `Protein` frame with a direct
   `<gene>` child is a monomer. This generalizes correctly to
   heteromultimers (distinct subunit genes), not just the homodimers/
   homotetramers seen in the validated fixtures.
3. **Organism identity confirmed from the PGDB's own cross-reference
   field** (`<dblink><dblink-db>NCBI-TAXONOMY-DB</dblink-db>
   <dblink-oid>...</dblink-oid></dblink>` inside `<metadata><PGDB>`), never
   assumed from the orgid string alone.
4. **Bounded at every step.** No step ever issues more than one request per
   discovered frame; a failed/missing/gated fetch stops that one branch
   (recorded, not silently dropped) without aborting the whole traversal;
   pathway detail-fetching is capped by `pathway_detail_limit` -- remaining
   `<in-pathway>` frame IDs are still recorded, namespace-qualified, just
   not independently fetched.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree as ET


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _qualified(orgid: str, frameid: str) -> str:
    return f"{orgid}:{frameid}"


def _biocyc_object(state: str, orgid: Optional[str] = None, frameid: Optional[str] = None,
                    common_name: Optional[str] = None, note: Optional[str] = None) -> Dict[str, Any]:
    obj: Dict[str, Any] = {"state": state}
    if orgid and frameid:
        obj.update({
            "orgid": orgid,
            "frameid": frameid,
            "qualified_id": _qualified(orgid, frameid),
            "common_name": common_name,
        })
    if note:
        obj["note"] = note
    return obj


def _outcome_object(frame_result) -> Dict[str, Any]:
    """A BioCycObject-shaped stand-in for a frame that was not reached in
    the 'ok' state -- distinguishes missing_frame/gated/failure explicitly
    rather than reporting all three as a generic error."""
    return _biocyc_object(
        state="unavailable_source" if frame_result.outcome in ("missing_frame", "gated", "failure") else "unknown",
        orgid=frame_result.orgid,
        frameid=frame_result.frameid,
        note=f"getxml outcome: {frame_result.outcome} (http_status={frame_result.http_status}).",
    )


def _parse_gene_frame(client, orgid: str, gene_frameid: str) -> Dict[str, Any]:
    result = client.get_frame(orgid, gene_frameid)
    if result.outcome != "ok":
        return {"gene": _outcome_object(result), "gene_symbol": None, "legacy_locus_tag": None, "genome_coordinates": None}

    root = ET.fromstring(result.body_text)
    gene_el = root.find(".//Gene")
    if gene_el is None:
        return {
            "gene": _biocyc_object("unknown", orgid, gene_frameid, note="No <Gene> element in retrieved frame."),
            "gene_symbol": None, "legacy_locus_tag": None, "genome_coordinates": None,
        }

    common_name = gene_el.findtext("common-name")
    return {
        "gene": _biocyc_object("present", orgid, gene_frameid, common_name=common_name),
        "gene_symbol": common_name,
        "legacy_locus_tag": gene_el.findtext("accession-1"),
        "genome_coordinates": {
            "left_end": _maybe_int(gene_el.findtext("left-end-position")),
            "right_end": _maybe_int(gene_el.findtext("right-end-position")),
            "strand": gene_el.findtext("transcription-direction"),
        },
    }


def _maybe_int(value: Optional[str]) -> Optional[int]:
    return int(value) if value else None


def _resolve_enzyme(client, orgid: str, protein_frameid: str) -> List[Dict[str, Any]]:
    """Resolve one `<enzyme><Protein resource=.../></enzyme>` reference into
    one or more `GeneEnzymeLink`-shaped dicts. Returns >1 entry only for a
    genuine multi-subunit complex whose subunits carry distinct genes
    (heteromultimer); a homodimer/homotetramer with one subunit type still
    yields exactly one link, correctly labeled `role: "complex"`.
    """
    protein_result = client.get_frame(orgid, protein_frameid)
    if protein_result.outcome != "ok":
        return [{
            "role": "unknown",
            "protein": _outcome_object(protein_result),
            "gene": _outcome_object(protein_result),
            "gene_symbol": None,
            "legacy_locus_tag": None,
            "genome_coordinates": None,
        }]

    root = ET.fromstring(protein_result.body_text)
    protein_el = root.find(".//Protein")
    if protein_el is None:
        return [{
            "role": "unknown",
            "protein": _biocyc_object("unknown", orgid, protein_frameid, note="No <Protein> element in retrieved frame."),
            "gene": _biocyc_object("unknown", orgid, protein_frameid),
            "gene_symbol": None,
            "legacy_locus_tag": None,
            "genome_coordinates": None,
        }]

    protein_common_name = protein_el.findtext("common-name")
    components = protein_el.findall("component/Protein")
    direct_gene = protein_el.find("gene/Gene")

    if components:
        # Multi-subunit complex: expand every distinct component subunit.
        links = []
        seen_subunit_frameids = set()
        for comp in components:
            subunit_frameid = comp.get("frameid")
            if subunit_frameid in seen_subunit_frameids:
                continue
            seen_subunit_frameids.add(subunit_frameid)
            subunit_result = client.get_frame(orgid, subunit_frameid)
            if subunit_result.outcome != "ok":
                links.append({
                    "role": "complex_subunit",
                    "protein": _biocyc_object("present", orgid, protein_frameid, common_name=protein_common_name),
                    "gene": _outcome_object(subunit_result),
                    "gene_symbol": None,
                    "legacy_locus_tag": None,
                    "genome_coordinates": None,
                })
                continue
            subunit_root = ET.fromstring(subunit_result.body_text)
            subunit_el = subunit_root.find(".//Protein")
            subunit_gene_el = subunit_el.find("gene/Gene") if subunit_el is not None else None
            if subunit_gene_el is None:
                links.append({
                    "role": "complex_subunit",
                    "protein": _biocyc_object("present", orgid, protein_frameid, common_name=protein_common_name),
                    "gene": _biocyc_object("unknown", orgid, subunit_frameid, note="Subunit frame has no <gene> link."),
                    "gene_symbol": None,
                    "legacy_locus_tag": None,
                    "genome_coordinates": None,
                })
                continue
            gene_frameid = subunit_gene_el.get("frameid")
            gene_info = _parse_gene_frame(client, orgid, gene_frameid)
            role = "complex_subunit" if len(components) > 1 else "complex"
            links.append({
                "role": role,
                "protein": _biocyc_object("present", orgid, protein_frameid, common_name=protein_common_name),
                **gene_info,
            })
        return links

    if direct_gene is not None:
        gene_frameid = direct_gene.get("frameid")
        gene_info = _parse_gene_frame(client, orgid, gene_frameid)
        return [{
            "role": "monomer",
            "protein": _biocyc_object("present", orgid, protein_frameid, common_name=protein_common_name),
            **gene_info,
        }]

    return [{
        "role": "unknown",
        "protein": _biocyc_object("present", orgid, protein_frameid, common_name=protein_common_name),
        "gene": _biocyc_object("unknown", orgid, protein_frameid, note="Protein frame has neither <gene> nor <component> -- gene identity not resolvable from this frame alone."),
        "gene_symbol": None,
        "legacy_locus_tag": None,
        "genome_coordinates": None,
    }]


def traverse_reaction(
    client,
    orgid: str,
    reaction_frameid: str,
    entry_id: str,
    expected_ec: Optional[str] = None,
    expected_taxid: Optional[str] = None,
    pathway_detail_limit: int = 1,
) -> Dict[str, Any]:
    """Run the bounded reaction -> enzyme/complex -> gene -> pathway
    traversal for one reaction frame.

    Parameters are entirely caller-supplied -- no organism/EC/frame ID
    defaults are baked in. `expected_ec`/`expected_taxid`, if given, are
    checked (never assumed) against the live record and reported as
    `ec_match`/`organism_verified` booleans rather than raised as
    exceptions, so a mismatch is surfaced to the caller instead of crashing
    a multi-reaction batch.

    Returns a dict; see `../references/traversal-strategy.md` for the full
    field-by-field description.
    """
    rxn_result = client.get_frame(orgid, reaction_frameid)
    generated_at = _utc_now_iso()

    if rxn_result.outcome != "ok":
        return {
            "entry_id": entry_id,
            "outcome": rxn_result.outcome,
            "biocyc_reaction": _outcome_object(rxn_result),
            "ec_number": expected_ec,
            "ec_match": None,
            "organism_verified": None,
            "genes_and_enzymes": [],
            "pathways": [],
            "compounds": [],
            "generated_at": generated_at,
        }

    root = ET.fromstring(rxn_result.body_text)
    rxn_el = root.find(f".//Reaction[@orgid='{orgid}']")
    actual_frameid = rxn_el.get("frameid") if rxn_el is not None else reaction_frameid

    ec_el = rxn_el.find("ec-number") if rxn_el is not None else None
    ec_number = None
    if ec_el is not None and ec_el.text:
        raw = ec_el.text.strip()
        ec_number = raw[3:] if raw.startswith("EC-") else raw
    ec_match = (ec_number == expected_ec) if expected_ec is not None else None

    pgdb_el = root.find(f".//PGDB[@orgid='{orgid}']")
    taxid = None
    if pgdb_el is not None:
        for dblink in pgdb_el.findall("dblink"):
            if dblink.findtext("dblink-db") == "NCBI-TAXONOMY-DB":
                taxid = dblink.findtext("dblink-oid")
    organism_verified = (taxid == expected_taxid) if expected_taxid is not None else None

    reaction_direction = rxn_el.findtext("reaction-direction") if rxn_el is not None else None
    phys_raw = rxn_el.findtext("physiologically-relevant") if rxn_el is not None else None

    # Enzyme(s)/complex(es) -> gene(s): every <Enzymatic-Reaction> is walked,
    # preserving multiple catalysts/isoenzymes rather than picking "the" one.
    genes_and_enzymes: List[Dict[str, Any]] = []
    if rxn_el is not None:
        for enz_rxn_el in rxn_el.findall("enzymatic-reaction/Enzymatic-Reaction"):
            protein_ref = enz_rxn_el.find("enzyme/Protein")
            if protein_ref is None:
                continue
            protein_frameid = protein_ref.get("frameid")
            genes_and_enzymes.extend(_resolve_enzyme(client, orgid, protein_frameid))

    # Pathway membership: fetch detail for the first `pathway_detail_limit`
    # pathways only; remaining <in-pathway> frame IDs are still recorded,
    # namespace-qualified, but not independently fetched (bounded traversal).
    pathways: List[Dict[str, Any]] = []
    if rxn_el is not None:
        in_pathway_el = rxn_el.find("in-pathway")
        pwy_frameids = [p.get("frameid") for p in in_pathway_el.findall("Pathway")] if in_pathway_el is not None else []
        for i, pwy_frameid in enumerate(pwy_frameids):
            if i < pathway_detail_limit:
                pwy_result = client.get_frame(orgid, pwy_frameid)
                if pwy_result.outcome == "ok":
                    pwy_root = ET.fromstring(pwy_result.body_text)
                    pwy_el = pwy_root.find(".//Pathway")
                    common_name = pwy_el.findtext("common-name") if pwy_el is not None else None
                    pathways.append({"pathway": _biocyc_object("present", orgid, pwy_frameid, common_name=common_name)})
                else:
                    pathways.append({"pathway": _outcome_object(pwy_result)})
            else:
                pathways.append({
                    "pathway": _biocyc_object(
                        "present", orgid, pwy_frameid,
                        note="Recorded namespace-qualified only; detail fetch skipped to keep this traversal bounded (pathway_detail_limit reached).",
                    )
                })

    compounds: List[Dict[str, Any]] = []
    if rxn_el is not None:
        for role, tag in (("substrate", "left"), ("product", "right")):
            for el in rxn_el.findall(tag):
                cpd_el = el.find("Compound")
                if cpd_el is None:
                    continue
                compounds.append({
                    "role": role,
                    "biocyc_compound": _biocyc_object("present", orgid, cpd_el.get("frameid")),
                })

    return {
        "entry_id": entry_id,
        "outcome": "ok",
        "biocyc_reaction": _biocyc_object("present", orgid, actual_frameid),
        "ec_number": ec_number,
        "ec_match": ec_match,
        "organism_verified": organism_verified,
        "ncbi_taxonomy_id": taxid,
        "reaction_direction": reaction_direction,
        "physiologically_relevant": (phys_raw == "true") if phys_raw is not None else None,
        "genes_and_enzymes": genes_and_enzymes,
        "pathways": pathways,
        "compounds": compounds,
        "generated_at": generated_at,
    }
