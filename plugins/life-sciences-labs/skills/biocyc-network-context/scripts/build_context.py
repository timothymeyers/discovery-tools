#!/usr/bin/env python3
"""CLI: build a canonical network-context JSON document by running
`traversal.traverse_reaction` over one or more reaction frames.

Two modes:

- **Offline/deterministic demo** (default, no network calls): replays this
  skill's own bundled fixtures (`../fixtures/`) via `CachedFixtureClient`
  for the three validated non-Calvin EC examples (EC 5.3.1.9 / 2.7.1.11 /
  1.1.1.49, *E. coli* K-12 MG1655). This is what the offline pytest suite
  exercises and what `--example` reproduces for anyone evaluating this
  skill without making a live request.
- **Live mode** (`--live`, requires `--i-understand-this-makes-a-live-request`):
  wires `BioCycClient` (real `default_http_get`) instead of the fixture
  replay double, for a caller-supplied orgid/frameid/EC/taxid. Subject to
  the same >=1-req/sec spacing as the live contract probe. This mode still
  only fetches individually-named frames discovered by the traversal --
  never a bulk/systematic query.

Run: `python3 scripts/build_context.py --example` (from this skill's
directory) or `python3 scripts/build_context.py --live --orgid ECOLI
--reaction-frameid <frameid> --ec <ec> --taxid <taxid>
--i-understand-this-makes-a-live-request`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from biocyc_client import BioCycClient, CachedFixtureClient, RateLimiter
from traversal import traverse_reaction

SKILL_ROOT = Path(__file__).resolve().parent.parent
FIXTURES_DIR = SKILL_ROOT / "fixtures"

# The three validated non-Calvin EC examples (DX-53, E. coli K-12 MG1655,
# EcoCyc orgid ECOLI, NCBI taxid 511145) -- illustrative example values for
# the offline demo/tests only, never hardcoded into traversal.py itself.
EXAMPLE_ORGID = "ECOLI"
EXAMPLE_TAXID = "511145"
EXAMPLE_REACTIONS = [
    {"entry_id": "pgi", "reaction_frameid": "PGLUCISOM-RXN", "expected_ec": "5.3.1.9"},
    {"entry_id": "pfk", "reaction_frameid": "6PFRUCTPHOS-RXN", "expected_ec": "2.7.1.11"},
    {"entry_id": "zwf", "reaction_frameid": "GLU6PDEHYDROG-RXN", "expected_ec": "1.1.1.49"},
]

EXAMPLE_FRAME_TO_FIXTURE = {
    "ECOLI:PGLUCISOM-RXN": "reaction_ec_5.3.1.9_pgi.xml",
    "ECOLI:CPLX0-7877": "protein_CPLX0-7877_pgi_complex.xml",
    "ECOLI:CPLX-8401": "protein_CPLX-8401_pgi_complex.xml",
    "ECOLI:PGLUCISOM": "protein_PGLUCISOM_pgi_monomer.xml",
    "ECOLI:G7463-MONOMER": "protein_G7463-MONOMER_pgi_monomer.xml",
    "ECOLI:EG10702": "gene_EG10702_pgi.xml",
    "ECOLI:G7463": "gene_G7463_kdui.xml",
    "ECOLI:6PFRUCTPHOS-RXN": "reaction_ec_2.7.1.11_pfk.xml",
    "ECOLI:6PFK-1-CPX": "protein_6PFK-1-CPX_pfk_complex.xml",
    "ECOLI:6PFK-2-CPX": "protein_6PFK-2-CPX_pfk_complex.xml",
    "ECOLI:6PFK-1-MONOMER": "protein_6PFK-1-MONOMER_pfk_monomer.xml",
    "ECOLI:6PFK-2-MONOMER": "protein_6PFK-2-MONOMER_pfk_monomer.xml",
    "ECOLI:EG10699": "gene_EG10699_pfkA.xml",
    "ECOLI:EG10700": "gene_EG10700_pfkB.xml",
    "ECOLI:GLU6PDEHYDROG-RXN": "reaction_ec_1.1.1.49_zwf.xml",
    "ECOLI:GLU6PDEHYDROG-MONOMER": "protein_GLU6PDEHYDROG-MONOMER_zwf_monomer.xml",
    "ECOLI:EG11221": "gene_EG11221_zwf.xml",
    "ECOLI:GLYCOLYSIS": "pathway_GLYCOLYSIS.xml",
    "ECOLI:PWY-5484": "pathway_GLYCOLYSIS.xml",  # bounded: only 1st pathway fetched by default; unused unless --pathway-detail-limit raised.
    "ECOLI:OXIDATIVEPENT-PWY": "pathway_OXIDATIVEPENT-PWY.xml",
    "ECOLI:UDPNAGSYN-PWY": "pathway_GLYCOLYSIS.xml",  # bounded: unused unless --pathway-detail-limit raised.
    "ECOLI:GLYCOLYSIS-E-D": "pathway_GLYCOLYSIS.xml",  # bounded: unused unless --pathway-detail-limit raised.
}


def build_example_document(pathway_detail_limit: int = 1) -> dict:
    client = CachedFixtureClient(FIXTURES_DIR, EXAMPLE_FRAME_TO_FIXTURE)
    entries = [
        traverse_reaction(
            client, EXAMPLE_ORGID, spec["reaction_frameid"], spec["entry_id"],
            expected_ec=spec["expected_ec"], expected_taxid=EXAMPLE_TAXID,
            pathway_detail_limit=pathway_detail_limit,
        )
        for spec in EXAMPLE_REACTIONS
    ]
    return {"orgid": EXAMPLE_ORGID, "ncbi_taxonomy_id": EXAMPLE_TAXID, "entries": entries}


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--example", action="store_true", help="Build the bundled 3-EC offline demo document (default if no --live).")
    parser.add_argument("--live", action="store_true", help="Use a real BioCycClient instead of the fixture replay double.")
    parser.add_argument("--i-understand-this-makes-a-live-request", action="store_true", dest="consent")
    parser.add_argument("--orgid")
    parser.add_argument("--reaction-frameid")
    parser.add_argument("--entry-id", default="entry")
    parser.add_argument("--ec", dest="expected_ec")
    parser.add_argument("--taxid", dest="expected_taxid")
    parser.add_argument("--pathway-detail-limit", type=int, default=1)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv=None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.live:
        if not args.consent:
            print(json.dumps({"ran": False, "reason": "Refusing --live without --i-understand-this-makes-a-live-request."}, indent=2))
            return 1
        if not (args.orgid and args.reaction_frameid):
            print(json.dumps({"ran": False, "reason": "--live requires --orgid and --reaction-frameid."}, indent=2))
            return 1
        client = BioCycClient(rate_limiter=RateLimiter())
        entry = traverse_reaction(
            client, args.orgid, args.reaction_frameid, args.entry_id,
            expected_ec=args.expected_ec, expected_taxid=args.expected_taxid,
            pathway_detail_limit=args.pathway_detail_limit,
        )
        doc = {"orgid": args.orgid, "ncbi_taxonomy_id": args.expected_taxid, "entries": [entry]}
    else:
        doc = build_example_document(pathway_detail_limit=args.pathway_detail_limit)

    text = json.dumps(doc, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(text + "\n")
        print(f"Wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
