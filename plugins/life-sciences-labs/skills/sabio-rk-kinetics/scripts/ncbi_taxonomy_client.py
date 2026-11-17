#!/usr/bin/env python3
"""NCBI Taxonomy ancestry resolution for a caller-supplied higher-taxon
lineage fallback (generalized from the taxid-based fix for the
"Cyanobacteria" vs. "Cyanobacteriota" name-drift bug -- see
`../references/taxonomy-ancestry-fallback.md`).

Key property preserved from the original fix: taxid ancestry is the
*primary* classification method; a caller-supplied verified synonym set is
only a *secondary* check used when taxid resolution is unavailable. An
empty/missing lineage array is always classified explicitly as
"unknown_empty_lineage" -- never treated as a match by omission, regardless
of which method would otherwise apply.

No specific taxon (e.g. Cyanobacteriota / taxid 1117) is hardcoded: callers
pass their own `fallback_taxid` and `verified_synonyms`.
"""
from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from sabio_rk_client import HttpResponse, RateLimiter, default_http_get

NCBI_TAXONOMY_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
# NCBI's documented unauthenticated rate limit -- a distinct service/limit
# from SABIO-RK's own 60/60s Export API limit.
NCBI_RATE_LIMIT_REQUESTS = 3
NCBI_RATE_LIMIT_WINDOW_SECONDS = 1.0
NCBI_TAXONOMY_BATCH_SIZE = 200  # efetch accepts comma-separated ids in one call.


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class NcbiTaxonomyClient:
    """Resolves NCBI taxonomy IDs to their ancestor-taxid chain via the
    public E-utils `efetch` endpoint (`db=taxonomy`), batching IDs into one
    request. `http_get` is injectable (same pattern as `SabioClient`), so
    this is fully mockable/offline-safe in tests.

    A failed lookup for a taxid returns `None` for that taxid (transport/
    parse failure), never an empty list -- callers must never conflate
    "couldn't determine ancestry" with "confirmed no ancestors".
    """

    def __init__(
        self,
        base_url: str = NCBI_TAXONOMY_EFETCH_URL,
        http_get: Callable[[str], HttpResponse] = default_http_get,
        rate_limiter: Optional[RateLimiter] = None,
        cache_dir: Optional[Path] = None,
        sleep_fn=None,
    ):
        self.base_url = base_url
        self._http_get = http_get
        self.rate_limiter = rate_limiter or RateLimiter(
            max_requests=NCBI_RATE_LIMIT_REQUESTS, window=NCBI_RATE_LIMIT_WINDOW_SECONDS
        )
        self.cache_dir = cache_dir
        self._ancestor_cache: Dict[int, Optional[List[int]]] = {}

    def resolve_ancestors(self, taxids: Iterable[int]) -> Dict[int, Optional[List[int]]]:
        """Return {taxid: [ancestor_taxid, ...] or None-on-failure} for every
        taxid in `taxids`, batching lookups and reusing already-resolved
        entries in this client instance. `taxids` is materialized into a
        list immediately -- a one-shot generator would otherwise be
        exhausted after the first pass over it below."""
        taxid_list = [t for t in taxids if t is not None]
        unresolved = sorted({int(t) for t in taxid_list} - set(self._ancestor_cache))
        for i in range(0, len(unresolved), NCBI_TAXONOMY_BATCH_SIZE):
            self._fetch_batch(unresolved[i : i + NCBI_TAXONOMY_BATCH_SIZE])
        return {t: self._ancestor_cache.get(t) for t in taxid_list}

    def _fetch_batch(self, taxids: List[int]) -> None:
        if not taxids:
            return
        ids_param = ",".join(str(t) for t in taxids)
        query = urllib.parse.urlencode({"db": "taxonomy", "id": ids_param, "rettype": "xml"})
        url = f"{self.base_url}?{query}"
        self.rate_limiter.acquire()
        response = self._http_get(url)
        meta = {"url": url, "http_status": response.status, "retrieved_at": _utc_now_iso(), "taxids": taxids}
        if response.status != 200:
            meta["outcome"] = "failure"
            meta["failure_kind"] = "authorization" if response.status in (401, 403) else "transport"
            for t in taxids:
                self._ancestor_cache[t] = None
            self._write_cache(taxids, response.body, meta)
            return
        try:
            resolved = parse_taxonomy_efetch_xml(response.body)
            meta["outcome"] = "success"
        except Exception as exc:  # noqa: BLE001 -- never crash the run on a parse failure.
            meta["outcome"] = "failure"
            meta["failure_kind"] = "parser"
            meta["parse_error"] = str(exc)
            for t in taxids:
                self._ancestor_cache[t] = None
            self._write_cache(taxids, response.body, meta)
            return
        for t in taxids:
            self._ancestor_cache[t] = resolved.get(t, [])  # confirmed reachable; genuinely no data => [].
        self._write_cache(taxids, response.body, meta)

    def _write_cache(self, taxids: List[int], body: bytes, meta: Dict[str, Any]) -> None:
        if self.cache_dir is None:
            return
        import json

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        key = "taxid_" + "_".join(str(t) for t in taxids[:5]) + (
            f"_and_{len(taxids) - 5}_more" if len(taxids) > 5 else ""
        )
        (self.cache_dir / f"{key}.xml").write_bytes(body)
        (self.cache_dir / f"{key}.meta.json").write_text(
            json.dumps(meta, indent=2, sort_keys=True), encoding="utf-8"
        )


def parse_taxonomy_efetch_xml(body: bytes) -> Dict[int, List[int]]:
    """Parse an NCBI Taxonomy `efetch` XML response into
    {taxid: [ancestor_taxid, ...]} using each <Taxon>'s own <TaxId> plus its
    <LineageEx><Taxon><TaxId> ancestor list."""
    root = ET.fromstring(body)
    out: Dict[int, List[int]] = {}
    for taxon in root.findall("Taxon"):
        taxid_el = taxon.find("TaxId")
        if taxid_el is None or not (taxid_el.text or "").strip():
            continue
        taxid = int(taxid_el.text.strip())
        ancestors = []
        lineage_ex = taxon.find("LineageEx")
        if lineage_ex is not None:
            for anc in lineage_ex.findall("Taxon"):
                anc_taxid_el = anc.find("TaxId")
                if anc_taxid_el is not None and (anc_taxid_el.text or "").strip():
                    ancestors.append(int(anc_taxid_el.text.strip()))
        out[taxid] = ancestors
    return out


def classify_higher_taxon_lineage(
    organism_taxid: Optional[int],
    ncbi_taxonomy_lineage: Optional[List[str]],
    ancestor_taxids: Optional[List[int]],
    fallback_taxid: int,
    verified_synonyms: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Decide whether one organism belongs to a caller-specified higher taxon
    (identified by `fallback_taxid`), generalizing the fix for the
    "Cyanobacteria" (old/retired NCBI name) vs. "Cyanobacteriota" (current
    NCBI name) lineage-string drift:

    1. An empty/missing `ncbi_taxonomy_lineage` is *always* explicit
       "unknown_empty_lineage" -- never a match by omission, regardless of
       taxid availability.
    2. Primary: NCBI taxid ancestry. Matches if the organism's own taxid
       *is* `fallback_taxid`, or `fallback_taxid` is among its resolved
       ancestors (`ancestor_taxids`, `None` meaning lookup unavailable --
       distinct from "resolved, no ancestors").
    3. Secondary (only when taxid resolution is unavailable): case-insensitive
       match of any lineage name against caller-supplied `verified_synonyms`.
       Callers must source this set from the taxon's own current NCBI
       `OtherNames` record (not guessed), and should include historical/
       retired names precisely because lineage-string drift is what this
       fallback exists to catch.
    """
    lineage = [n for n in (ncbi_taxonomy_lineage or []) if n]
    if not lineage:
        return {
            "match": False,
            "method": "unknown_empty_lineage",
            "note": "ncbi_taxonomy_lineage is empty/absent; never treated as a match.",
        }

    if ancestor_taxids is not None and organism_taxid is not None:
        match = organism_taxid == fallback_taxid or fallback_taxid in ancestor_taxids
        return {
            "match": match,
            "method": "taxid_ancestry",
            "note": (
                f"organism taxid {organism_taxid} ancestry resolved via NCBI Taxonomy efetch; "
                f"fallback taxid {fallback_taxid} {'found' if match else 'not found'} in self+ancestors."
            ),
        }

    synonyms = {s.strip().lower() for s in (verified_synonyms or ())}
    match = any(name.strip().lower() in synonyms for name in lineage)
    return {
        "match": match,
        "method": "synonym_secondary",
        "note": (
            "taxid-based ancestry resolution unavailable (missing organism taxid or NCBI Taxonomy "
            "lookup failure); used caller-supplied verified synonym set as secondary check."
        ),
    }
