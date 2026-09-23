"""Offline tests for NCBI taxid-ancestry classification, including a
mutation-sensitive test proving the *old* literal-lineage-name-string bug
(matching only a single retired taxon name) silently misses genuinely
matching organisms whose lineage array uses the *current* NCBI name -- while
the taxid-ancestry-based classification here catches them correctly. No
live network calls; NCBI efetch XML is loaded from the checked-in fixture.
"""
from __future__ import annotations

from ncbi_taxonomy_client import (
    NcbiTaxonomyClient,
    classify_higher_taxon_lineage,
    parse_taxonomy_efetch_xml,
)
from sabio_rk_client import HttpResponse, RateLimiter

from sabio_test_helpers import load_fixture

FALLBACK_TAXID = 1224  # Pseudomonadota, per the fixture's ancestry chain.
FALLBACK_NAME = "Pseudomonadota"
VERIFIED_SYNONYMS = ("pseudomonadota", "proteobacteria")  # includes the retired name.


# --------------------------------------------------------------------------
# efetch XML parsing
# --------------------------------------------------------------------------
def test_parse_taxonomy_efetch_xml_extracts_ancestor_chain():
    body = load_fixture("ncbi_efetch_ancestry.xml")
    parsed = parse_taxonomy_efetch_xml(body)
    assert parsed[303] == [2, 1224, 1236]
    assert parsed[4932] == [2759, 4890]


# --------------------------------------------------------------------------
# classify_higher_taxon_lineage: known-answer cases
# --------------------------------------------------------------------------
def test_empty_lineage_is_always_unknown_never_a_match():
    result = classify_higher_taxon_lineage(
        organism_taxid=90371,
        ncbi_taxonomy_lineage=[],
        ancestor_taxids=[2, FALLBACK_TAXID],  # even if ancestry *would* match.
        fallback_taxid=FALLBACK_TAXID,
        verified_synonyms=VERIFIED_SYNONYMS,
    )
    assert result["match"] is False
    assert result["method"] == "unknown_empty_lineage"


def test_taxid_ancestry_match_via_ancestor_chain():
    result = classify_higher_taxon_lineage(
        organism_taxid=303,
        ncbi_taxonomy_lineage=["Bacteria", "Pseudomonadota", "Gammaproteobacteria"],
        ancestor_taxids=[2, FALLBACK_TAXID, 1236],
        fallback_taxid=FALLBACK_TAXID,
        verified_synonyms=VERIFIED_SYNONYMS,
    )
    assert result["match"] is True
    assert result["method"] == "taxid_ancestry"


def test_taxid_ancestry_no_match_for_unrelated_lineage():
    result = classify_higher_taxon_lineage(
        organism_taxid=4932,
        ncbi_taxonomy_lineage=["Eukaryota", "Fungi", "Ascomycota"],
        ancestor_taxids=[2759, 4890],
        fallback_taxid=FALLBACK_TAXID,
        verified_synonyms=VERIFIED_SYNONYMS,
    )
    assert result["match"] is False
    assert result["method"] == "taxid_ancestry"


def test_synonym_secondary_used_only_when_ancestry_unavailable():
    # ancestor_taxids=None simulates an NCBI Taxonomy lookup failure.
    result = classify_higher_taxon_lineage(
        organism_taxid=303,
        ncbi_taxonomy_lineage=["Bacteria", "Pseudomonadota"],
        ancestor_taxids=None,
        fallback_taxid=FALLBACK_TAXID,
        verified_synonyms=VERIFIED_SYNONYMS,
    )
    assert result["match"] is True
    assert result["method"] == "synonym_secondary"


# --------------------------------------------------------------------------
# Mutation-sensitive test: reproduces the historical name-drift bug and
# proves this skill's replacement logic does not repeat it.
# --------------------------------------------------------------------------
def _old_buggy_literal_string_match(lineage, retired_name_literal="Proteobacteria"):
    """The exact shape of the historical bug being guarded against: testing
    literal membership of one specific (here: retired) taxon name string,
    ignoring both taxid ancestry and any synonym set. Reproduced here only
    to prove the new logic does not repeat it -- never used in the shipped
    scripts."""
    return any(name == retired_name_literal for name in (lineage or []))


def test_mutation_old_literal_string_match_misses_current_name_lineage():
    # SABIO-RK's own lineage array uses the *current* NCBI name
    # "Pseudomonadota", never the retired "Proteobacteria" string this old
    # code searched for -- so the bug silently drops a genuine match.
    lineage = ["Bacteria", "Pseudomonadota", "Gammaproteobacteria"]
    assert _old_buggy_literal_string_match(lineage) is False  # confirms the bug reproduces.

    # The taxid-ancestry classification in this skill correctly matches the
    # same organism instead.
    fixed = classify_higher_taxon_lineage(
        organism_taxid=303,
        ncbi_taxonomy_lineage=lineage,
        ancestor_taxids=[2, FALLBACK_TAXID, 1236],
        fallback_taxid=FALLBACK_TAXID,
        verified_synonyms=VERIFIED_SYNONYMS,
    )
    assert fixed["match"] is True
    assert fixed["method"] == "taxid_ancestry"


CYANOBACTERIOTA_FALLBACK_TAXID = 1117  # exact historical case: current NCBI name "Cyanobacteriota".


def test_mutation_old_literal_cyanobacteria_matcher_misses_current_cyanobacteriota_lineage():
    # Exact historical regression: SABIO-RK's own lineage array for this
    # organism uses the *current* NCBI name "Cyanobacteriota", never the
    # retired "Cyanobacteria" string an old literal-name filter searched
    # for -- reproduced here via the checked-in regression fixtures.
    entries = load_fixture("cyanobacteria_name_drift_regression.json")["data"]
    matching_entry = next(e for e in entries if e["id"] == 301)
    lineage = matching_entry["general"]["organism"]["ncbi_taxonomy_lineage"]
    assert lineage == ["Bacteria", "Cyanobacteriota", "Synechococcales"]

    assert _old_buggy_literal_string_match(lineage, retired_name_literal="Cyanobacteria") is False

    xml_body = load_fixture("cyanobacteria_name_drift_regression_efetch.xml")

    def fake_http_get(url):
        return HttpResponse(200, xml_body, {})

    client = NcbiTaxonomyClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=1.0))
    ancestors = client.resolve_ancestors([1148])[1148]
    assert ancestors == [2, 1117, 1129]

    fixed = classify_higher_taxon_lineage(
        organism_taxid=1148,
        ncbi_taxonomy_lineage=lineage,
        ancestor_taxids=ancestors,
        fallback_taxid=CYANOBACTERIOTA_FALLBACK_TAXID,
        verified_synonyms=("cyanobacteriota", "cyanobacteria"),
    )
    assert fixed["match"] is True
    assert fixed["method"] == "taxid_ancestry"


def test_cyanobacteria_regression_fixture_empty_lineage_is_unknown_not_a_match():
    entries = load_fixture("cyanobacteria_name_drift_regression.json")["data"]
    empty_lineage_entry = next(e for e in entries if e["id"] == 302)
    lineage = empty_lineage_entry["general"]["organism"]["ncbi_taxonomy_lineage"]
    assert lineage == []

    result = classify_higher_taxon_lineage(
        organism_taxid=empty_lineage_entry["general"]["organism"]["ncbi_taxonomy_id"],
        ncbi_taxonomy_lineage=lineage,
        ancestor_taxids=[2, CYANOBACTERIOTA_FALLBACK_TAXID],  # even if ancestry *would* match.
        fallback_taxid=CYANOBACTERIOTA_FALLBACK_TAXID,
        verified_synonyms=("cyanobacteriota", "cyanobacteria"),
    )
    assert result["match"] is False
    assert result["method"] == "unknown_empty_lineage"


def test_mutation_synonym_secondary_also_recovers_when_synonym_set_includes_retired_name():
    # Even without taxid resolution, a *verified* synonym set that includes
    # the retired name (unlike the old single-literal-string bug, which
    # tested only the current or only the retired name -- never a full
    # verified set) still recovers the match.
    lineage = ["Bacteria", "Proteobacteria"]  # entry using the retired name.
    result = classify_higher_taxon_lineage(
        organism_taxid=303,
        ncbi_taxonomy_lineage=lineage,
        ancestor_taxids=None,
        fallback_taxid=FALLBACK_TAXID,
        verified_synonyms=VERIFIED_SYNONYMS,
    )
    assert result["match"] is True
    assert result["method"] == "synonym_secondary"


# --------------------------------------------------------------------------
# NcbiTaxonomyClient: transport/parser failure handling (never crashes)
# --------------------------------------------------------------------------
def test_ncbi_taxonomy_client_resolves_ancestors_from_fixture():
    xml_body = load_fixture("ncbi_efetch_ancestry.xml")

    def fake_http_get(url):
        return HttpResponse(200, xml_body, {})

    client = NcbiTaxonomyClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=1.0))
    result = client.resolve_ancestors([303, 4932])
    assert result[303] == [2, 1224, 1236]
    assert result[4932] == [2759, 4890]


def test_ncbi_taxonomy_client_transport_failure_returns_none_not_empty_list():
    def fake_http_get(url):
        return HttpResponse(500, b"internal error", {})

    client = NcbiTaxonomyClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=1.0))
    result = client.resolve_ancestors([303])
    assert result[303] is None  # explicit "unavailable", never conflated with "no ancestors" ([]).


def test_ncbi_taxonomy_client_parser_failure_returns_none():
    def fake_http_get(url):
        return HttpResponse(200, b"<not><valid", {})

    client = NcbiTaxonomyClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=1.0))
    result = client.resolve_ancestors([303])
    assert result[303] is None
