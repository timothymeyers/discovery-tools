"""Offline tests for parameterized query-tier construction, pagination
(sample vs. exhaustive, truncation reporting), and full tier-resolution
(`resolve_reaction_entries`), including the outcome/failure_kind semantics
(`resolved` / `confirmed_empty` / `query_incomplete_failure`). No live
network calls -- HTTP is injected via a fake `http_get`.
"""
from __future__ import annotations

import json

from ncbi_taxonomy_client import NcbiTaxonomyClient
from query_tiers import (
    MAX_PAGES_SAMPLE,
    PAGINATION_MODE_EXHAUSTIVE,
    PAGINATION_MODE_SAMPLE,
    build_query_tiers,
    cache_key_for,
    fetch_tier_entries,
    resolve_reaction_entries,
)
from sabio_rk_client import HttpResponse, RateLimiter, SabioClient

from sabio_test_helpers import load_fixture

FALLBACK_TAXID = 1224
FALLBACK_NAME = "Pseudomonadota"


# --------------------------------------------------------------------------
# build_query_tiers: parameterized, deterministic
# --------------------------------------------------------------------------
def test_build_query_tiers_is_parameterized_not_hardcoded():
    tiers_a = build_query_tiers("1.1.1.1", "Escherichia coli", "Escherichia", FALLBACK_NAME)
    tiers_b = build_query_tiers("2.7.2.3", "Bacillus subtilis", "Bacillus", "Bacillota")
    assert tiers_a[0]["query"] == 'ECNumber:1.1.1.1 AND Organism:"Escherichia coli"'
    assert tiers_b[0]["query"] == 'ECNumber:2.7.2.3 AND Organism:"Bacillus subtilis"'
    assert tiers_a[1]["query"] == "ECNumber:1.1.1.1 AND Organism:Escherichia*"
    assert [t["tier"] for t in tiers_a] == [
        "tier1_exact_organism",
        "tier2_genus_wildcard",
        "tier3_lineage_fallback",
        "tier4_any_organism",
    ]


def test_build_query_tiers_deterministic_across_calls():
    tiers_a = build_query_tiers("1.1.1.1", "Escherichia coli", "Escherichia", FALLBACK_NAME)
    tiers_b = build_query_tiers("1.1.1.1", "Escherichia coli", "Escherichia", FALLBACK_NAME)
    assert tiers_a == tiers_b


def test_cache_key_for_sanitizes_ec_number():
    assert cache_key_for("1.1.1.1", "tier1_exact_organism", 1) == "ec1_1_1_1__tier1_exact_organism__page1"


# --------------------------------------------------------------------------
# Pagination modes + truncation reporting
# --------------------------------------------------------------------------
def _client_from_pages(pages):
    def fake_http_get(url):
        page_num = int(url.split("page=")[1].split("&")[0])
        return HttpResponse(200, json.dumps(pages[page_num]).encode("utf-8"), {})

    return SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0))


def test_sample_mode_stops_after_one_page_and_reports_truncated():
    pages = {1: load_fixture("pagination_page1_of2.json")}
    client = _client_from_pages(pages)
    tier_spec = {"tier": "tier4_any_organism", "query": "ECNumber:1.1.1.1"}
    entries, summary = fetch_tier_entries(client, "1.1.1.1", tier_spec, pagination_mode=PAGINATION_MODE_SAMPLE)
    assert summary["pages_fetched"] == 1
    assert summary["truncated"] is True
    assert summary["total_pages_available"] == 2
    assert len(entries) == 1


def test_exhaustive_mode_follows_all_pages_and_reports_not_truncated():
    pages = {1: load_fixture("pagination_page1_of2.json"), 2: load_fixture("pagination_page2_of2.json")}
    client = _client_from_pages(pages)
    tier_spec = {"tier": "tier4_any_organism", "query": "ECNumber:1.1.1.1"}
    entries, summary = fetch_tier_entries(client, "1.1.1.1", tier_spec, pagination_mode=PAGINATION_MODE_EXHAUSTIVE)
    assert summary["pages_fetched"] == 2
    assert summary["truncated"] is False
    assert len(entries) == 2


def test_invalid_pagination_mode_raises():
    client = _client_from_pages({1: load_fixture("pagination_page1_of2.json")})
    tier_spec = {"tier": "tier4_any_organism", "query": "ECNumber:1.1.1.1"}
    try:
        fetch_tier_entries(client, "1.1.1.1", tier_spec, pagination_mode="bogus")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_malformed_response_stops_tier_as_parser_failure():
    def fake_http_get(url):
        return HttpResponse(200, json.dumps(load_fixture("outcome_malformed_response.json")).encode("utf-8"), {})

    client = SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0))
    tier_spec = {"tier": "tier4_any_organism", "query": "ECNumber:1.1.1.1"}
    entries, summary = fetch_tier_entries(client, "1.1.1.1", tier_spec)
    assert entries == []
    assert summary["outcome"] == "failure"
    assert summary["failure_kind"] == "parser"


# --------------------------------------------------------------------------
# resolve_reaction_entries: end-to-end tier fallback + outcome status
# --------------------------------------------------------------------------
def _fixed_taxonomy_client():
    xml_body = load_fixture("ncbi_efetch_ancestry.xml")

    def fake_http_get(url):
        from sabio_rk_client import HttpResponse as _HR

        return _HR(200, xml_body, {})

    return NcbiTaxonomyClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=1.0))


def test_resolve_reaction_entries_resolves_at_tier1_when_present():
    def fake_http_get(url):
        if 'Organism:"Escherichia coli"' in url or "Organism%3A%22Escherichia" in url:
            return HttpResponse(200, json.dumps(load_fixture("tier1_exact_organism_hit.json")).encode("utf-8"), {})
        return HttpResponse(200, json.dumps({"meta": {"page": 1, "page_size": 100, "total_count": 0, "total_pages": 1}, "data": []}).encode("utf-8"), {})

    client = SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0))
    entries, meta = resolve_reaction_entries(
        client,
        ec_number="1.1.1.1",
        organism="Escherichia coli",
        genus="Escherichia",
        fallback_taxid=FALLBACK_TAXID,
        fallback_taxon_name=FALLBACK_NAME,
        taxonomy_client=_fixed_taxonomy_client(),
    )
    assert meta["status"] == "resolved"
    assert meta["resolved_tier"] == "tier1_exact_organism"
    assert len(entries) == 1


def test_resolve_reaction_entries_falls_through_to_tier3_lineage_match():
    def fake_http_get(url):
        if "Organism" in url:  # tiers 1/2 always empty for this test.
            return HttpResponse(200, json.dumps({"meta": {"page": 1, "page_size": 100, "total_count": 0, "total_pages": 1}, "data": []}).encode("utf-8"), {})
        return HttpResponse(200, json.dumps(load_fixture("tier3_taxid_ancestry_mixed.json")).encode("utf-8"), {})

    client = SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0))
    entries, meta = resolve_reaction_entries(
        client,
        ec_number="1.1.1.1",
        organism="No Such Organism",
        genus="Nosuchgenus",
        fallback_taxid=FALLBACK_TAXID,
        fallback_taxon_name=FALLBACK_NAME,
        taxonomy_client=_fixed_taxonomy_client(),
    )
    assert meta["status"] == "resolved"
    assert meta["resolved_tier"] == "tier3_lineage_fallback"
    # Only entry 201 (Pseudomonas putida, taxid 303) matches Pseudomonadota;
    # entry 202 has empty lineage, entry 203 is an unrelated lineage.
    assert len(entries) == 1
    assert entries[0]["id"] == 201


def test_resolve_reaction_entries_confirmed_empty_when_all_tiers_genuinely_empty():
    def fake_http_get(url):
        return HttpResponse(200, json.dumps({"meta": {"page": 1, "page_size": 100, "total_count": 0, "total_pages": 1}, "data": []}).encode("utf-8"), {})

    client = SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0))
    entries, meta = resolve_reaction_entries(
        client,
        ec_number="9.9.9.9",
        organism="Nonexistent organism",
        genus="Nonexistentgenus",
        fallback_taxid=FALLBACK_TAXID,
        fallback_taxon_name=FALLBACK_NAME,
        taxonomy_client=_fixed_taxonomy_client(),
    )
    assert meta["status"] == "confirmed_empty"
    assert entries == []


def test_resolve_reaction_entries_query_incomplete_failure_never_conflated_with_confirmed_empty():
    def fake_http_get(url):
        return HttpResponse(500, b"server error", {})

    client = SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0))
    entries, meta = resolve_reaction_entries(
        client,
        ec_number="1.1.1.1",
        organism="Escherichia coli",
        genus="Escherichia",
        fallback_taxid=FALLBACK_TAXID,
        fallback_taxon_name=FALLBACK_NAME,
        taxonomy_client=_fixed_taxonomy_client(),
    )
    assert meta["status"] == "query_incomplete_failure"
    assert meta["status"] != "confirmed_empty"
    assert entries == []
