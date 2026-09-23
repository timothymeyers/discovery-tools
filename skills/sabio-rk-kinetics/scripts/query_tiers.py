#!/usr/bin/env python3
"""Parameterized SABIO-RK query-tier construction and paginated fetch/resolve
logic (generalized from a prior EC-number-scoped ingestion pass -- see
`../references/query-tier-strategy.md` and `../references/pagination-modes.md`).

No organism, genus, strain, EC number, or taxon is hardcoded: every tier is
built from caller-supplied parameters via `build_query_tiers(...)`.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from ncbi_taxonomy_client import NcbiTaxonomyClient, classify_higher_taxon_lineage
from sabio_rk_client import SabioClient

PAGINATION_MODE_SAMPLE = "sample"
PAGINATION_MODE_EXHAUSTIVE = "exhaustive"
PAGINATION_MODES = (PAGINATION_MODE_SAMPLE, PAGINATION_MODE_EXHAUSTIVE)
DEFAULT_PAGINATION_MODE = PAGINATION_MODE_EXHAUSTIVE
MAX_PAGES_SAMPLE = 1  # pageSize=100 => up to 100 entries/query in sample mode.
MAX_PAGES_EXHAUSTIVE_SAFETY_CAP = 50  # pageSize=100 => up to 5,000 entries/query.
PAGE_SIZE = 100


def build_query_tiers(
    ec_number: str,
    organism: str,
    genus: str,
    fallback_taxon_name: str,
) -> List[Dict[str, str]]:
    """Deterministically derive the ordered SABIO-RK Solr query tiers for one
    EC number and one target organism -- never a bare free-text species
    search. All of `ec_number`, `organism`, `genus`, and
    `fallback_taxon_name` are caller-supplied parameters.

    Tier 1 -- exact organism match.
    Tier 2 -- genus-level wildcard fallback.
    Tier 3 -- no organism filter in the query itself (the Solr `Organism`
              field only indexes species-level names, not higher taxa);
              results are post-filtered client-side to entries whose
              `ncbi_taxonomy_lineage`/taxid ancestry matches
              `fallback_taxon_name` (see `resolve_reaction_entries`).
    Tier 4 -- broadest fallback: EC-number match only, any organism.
    """
    ec_clause = f"ECNumber:{ec_number}"
    return [
        {
            "tier": "tier1_exact_organism",
            "query": f'{ec_clause} AND Organism:"{organism}"',
            "organism_filter": "exact",
            "note": f"Exact target species match ({organism}).",
        },
        {
            "tier": "tier2_genus_wildcard",
            "query": f"{ec_clause} AND Organism:{genus}*",
            "organism_filter": "genus_wildcard",
            "note": f"Genus-level wildcard fallback ({genus}*).",
        },
        {
            "tier": "tier3_lineage_fallback",
            "query": ec_clause,
            "organism_filter": "lineage_postfilter",
            "note": (
                "No organism restriction in the query itself (Solr Organism field only indexes "
                f"species-level names, not higher taxa); results are post-filtered client-side to "
                f"entries whose ncbi_taxonomy_lineage/taxid ancestry matches '{fallback_taxon_name}'."
            ),
        },
        {
            "tier": "tier4_any_organism",
            "query": ec_clause,
            "organism_filter": "none",
            "note": (
                "Broadest fallback: EC-number match only, any organism. Explicitly flagged as such -- "
                "never conflated with an organism-matched observation."
            ),
        },
    ]


def cache_key_for(ec_number: str, tier: str, page: int) -> str:
    safe_ec = ec_number.replace(".", "_").replace("*", "x")
    return f"ec{safe_ec}__{tier}__page{page}"


def fetch_tier_entries(
    client: SabioClient,
    ec_number: str,
    tier_spec: Dict[str, str],
    page_size: int = PAGE_SIZE,
    max_pages: Optional[int] = None,
    pagination_mode: str = DEFAULT_PAGINATION_MODE,
) -> Tuple[List[dict], Dict[str, Any]]:
    """Fetch pages for one query tier, following `meta.total_pages`.
    Returns (raw_entries, tier_summary).

    `pagination_mode` selects "sample" (bounded to `MAX_PAGES_SAMPLE`) or
    "exhaustive" (follows all pages up to `MAX_PAGES_EXHAUSTIVE_SAFETY_CAP`).
    An explicit `max_pages` overrides the mode-derived default. Truncation
    (fewer pages retained than the API reports existing) is always recorded
    in the summary -- never silent.
    """
    if pagination_mode not in PAGINATION_MODES:
        raise ValueError(f"pagination_mode must be one of {PAGINATION_MODES}, got {pagination_mode!r}")
    if max_pages is None:
        max_pages = MAX_PAGES_SAMPLE if pagination_mode == PAGINATION_MODE_SAMPLE else MAX_PAGES_EXHAUSTIVE_SAFETY_CAP

    entries: List[dict] = []
    page = 1
    total_pages = 1
    summary: Dict[str, Any] = {
        "tier": tier_spec["tier"],
        "query": tier_spec["query"],
        "pagination_mode": pagination_mode,
        "pages_fetched": 0,
        "total_count": None,
        "total_pages_available": None,
        "truncated": False,
    }
    while page <= total_pages and page <= max_pages:
        params = {"q": tier_spec["query"], "page": page, "pageSize": page_size}
        cache_key = cache_key_for(ec_number, tier_spec["tier"], page)
        parsed, meta = client.get_json("/kinlaw-entry/json", params, cache_key=cache_key)
        summary["pages_fetched"] += 1
        if parsed is None:
            summary["outcome"] = "failure"
            summary["failure_kind"] = meta.get("failure_kind", "transport")
            summary["error"] = meta.get("parse_error") or f"http_status={meta.get('http_status')}"
            break
        meta_block = parsed.get("meta") if isinstance(parsed, dict) else None
        data_block = parsed.get("data") if isinstance(parsed, dict) else None
        if not isinstance(meta_block, dict) or not isinstance(data_block, list):
            summary["outcome"] = "failure"
            summary["failure_kind"] = "parser"
            summary["error"] = "malformed_top_level_response: missing meta/data"
            break
        summary["total_count"] = meta_block.get("total_count")
        total_pages = meta_block.get("total_pages") or 1
        summary["total_pages_available"] = total_pages
        entries.extend(e for e in data_block if isinstance(e, dict))
        page += 1
    if "outcome" not in summary:
        summary["outcome"] = "success" if entries else "confirmed_empty"
    if summary["total_pages_available"] is not None:
        summary["truncated"] = summary["pages_fetched"] < summary["total_pages_available"]
    summary["entries_fetched"] = len(entries)
    return entries, summary


def resolve_reaction_entries(
    client: SabioClient,
    ec_number: str,
    organism: str,
    genus: str,
    fallback_taxid: int,
    fallback_taxon_name: str,
    verified_synonyms: Optional[Iterable[str]] = None,
    taxonomy_client: Optional[NcbiTaxonomyClient] = None,
    pagination_mode: str = DEFAULT_PAGINATION_MODE,
) -> Tuple[List[dict], Dict[str, Any]]:
    """Run tiers in order; stop at the first tier with a nonzero result count.

    Returns (raw_entries, resolution_meta). `resolution_meta` always records
    every tier attempted (for reproducibility/audit) plus an overall status:

      - "resolved"                 -- some tier returned entries.
      - "confirmed_empty"          -- every tier's API calls succeeded
                                      (HTTP 200, well-formed) and genuinely
                                      returned zero matching entries.
      - "query_incomplete_failure" -- at least one tier could not be
                                      confirmed empty or resolved because of
                                      a transport, authorization, or parser
                                      failure -- never reported the same way
                                      as a confirmed-empty result.

    Tiers 3 (lineage fallback) and 4 (any organism) issue the *same*
    underlying EC-only query (they differ only in client-side lineage
    post-filtering), so tier 4 reuses tier 3's already-fetched raw entries
    instead of re-querying the API.
    """
    tiers_tried = []
    ec_only_entries_cache: Optional[List[dict]] = None
    any_tier_failed = False
    for tier_spec in build_query_tiers(ec_number, organism, genus, fallback_taxon_name):
        if tier_spec["organism_filter"] == "none" and ec_only_entries_cache is not None:
            entries = ec_only_entries_cache
            summary = {
                "tier": tier_spec["tier"],
                "query": tier_spec["query"],
                "reused_tier3_fetch": True,
                "entries_fetched": len(entries),
                "outcome": "success" if entries else "confirmed_empty",
            }
        else:
            entries, summary = fetch_tier_entries(client, ec_number, tier_spec, pagination_mode=pagination_mode)
            if summary.get("outcome") == "failure":
                any_tier_failed = True
            if tier_spec["organism_filter"] == "lineage_postfilter":
                ec_only_entries_cache = entries
                before = len(entries)
                taxonomy_client = taxonomy_client or NcbiTaxonomyClient()
                organism_taxids = [
                    (e.get("general", {}).get("organism", {}) or {}).get("ncbi_taxonomy_id") for e in entries
                ]
                # Must be a materialized list, not a generator: `resolve_ancestors`
                # iterates its `taxids` argument twice (once to find unresolved
                # IDs, once to build the returned map) -- a generator would be
                # exhausted after the first pass and silently return an empty map.
                present_taxids = [t for t in organism_taxids if t is not None]
                ancestor_map = taxonomy_client.resolve_ancestors(present_taxids)
                kept = []
                classifications = []
                for e in entries:
                    org = e.get("general", {}).get("organism", {}) or {}
                    taxid = org.get("ncbi_taxonomy_id")
                    classification = classify_higher_taxon_lineage(
                        taxid,
                        org.get("ncbi_taxonomy_lineage"),
                        ancestor_map.get(taxid) if taxid is not None else None,
                        fallback_taxid=fallback_taxid,
                        verified_synonyms=verified_synonyms,
                    )
                    classifications.append(classification["method"])
                    if classification["match"]:
                        kept.append(e)
                entries = kept
                summary["lineage_postfilter_kept"] = len(entries)
                summary["lineage_postfilter_dropped"] = before - len(entries)
                summary["lineage_postfilter_methods"] = dict(Counter(classifications))
                summary["outcome"] = "success" if entries else summary.get("outcome", "confirmed_empty")
        summary["note"] = tier_spec["note"]
        tiers_tried.append(summary)
        if entries:
            return entries, {"resolved_tier": tier_spec["tier"], "status": "resolved", "tiers_tried": tiers_tried}
    status = "query_incomplete_failure" if any_tier_failed else "confirmed_empty"
    return [], {"resolved_tier": None, "status": status, "tiers_tried": tiers_tried}
