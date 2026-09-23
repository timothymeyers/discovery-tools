#!/usr/bin/env python3
"""CLI entry point for a parameterized SABIO-RK kinetic-law ingestion run.

Every project-specific value (EC number, target organism/genus, higher-taxon
fallback taxid/name, output paths) is a command-line argument -- nothing here
is scoped to a single organism or project. Normalization to a specific
project's evidence schema is intentionally *not* included in this skill: it
is a one-tier-down concern, and duplicating it here would conflict with this
repo's `scientific-evidence-qc` hub skill. This script stops at "resolved raw
entries + tier/pagination/outcome metadata", which is the reusable part.

Example:
    python3 ingest_kinetics.py \\
        --ec 1.1.1.1 --organism "Escherichia coli" --genus Escherichia \\
        --fallback-taxid 1224 --fallback-taxon-name Pseudomonadota \\
        --pagination-mode sample --output /tmp/ec_1_1_1_1.json

Run tests instead of live queries with `python -m pytest tests/ -q` from the
skill root -- this script itself always makes real HTTP calls when invoked
directly (subject to the injected rate limiter).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ncbi_taxonomy_client import NcbiTaxonomyClient
from query_tiers import DEFAULT_PAGINATION_MODE, PAGINATION_MODES, resolve_reaction_entries
from sabio_rk_client import SabioClient


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--ec", required=True, help="EC number to query, e.g. 1.1.1.1")
    parser.add_argument("--organism", required=True, help='Exact target species name, e.g. "Escherichia coli"')
    parser.add_argument("--genus", required=True, help="Genus for the tier-2 wildcard fallback, e.g. Escherichia")
    parser.add_argument(
        "--fallback-taxid",
        type=int,
        required=True,
        help="NCBI taxid of the higher-taxon fallback used for tier-3 lineage post-filtering.",
    )
    parser.add_argument(
        "--fallback-taxon-name",
        required=True,
        help="Human-readable name of the higher-taxon fallback, used only in notes/logging.",
    )
    parser.add_argument(
        "--verified-synonym",
        action="append",
        default=[],
        help="Verified synonym for the fallback taxon (repeatable) used only when taxid resolution fails.",
    )
    parser.add_argument("--pagination-mode", choices=PAGINATION_MODES, default=DEFAULT_PAGINATION_MODE)
    parser.add_argument("--cache-dir", type=Path, default=None, help="Directory for raw response caching.")
    parser.add_argument("--output", type=Path, default=None, help="Path to write resolution JSON; stdout if omitted.")
    return parser


def run(argv: Optional[list] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    client = SabioClient(cache_dir=args.cache_dir)
    taxonomy_client = NcbiTaxonomyClient(cache_dir=args.cache_dir / "ncbi_taxonomy" if args.cache_dir else None)

    entries, resolution_meta = resolve_reaction_entries(
        client,
        ec_number=args.ec,
        organism=args.organism,
        genus=args.genus,
        fallback_taxid=args.fallback_taxid,
        fallback_taxon_name=args.fallback_taxon_name,
        verified_synonyms=args.verified_synonym,
        taxonomy_client=taxonomy_client,
        pagination_mode=args.pagination_mode,
    )
    client.write_manifest()

    result = {
        "ec_number": args.ec,
        "organism": args.organism,
        "resolution": resolution_meta,
        "entries_count": len(entries),
        "entries": entries,
    }
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
