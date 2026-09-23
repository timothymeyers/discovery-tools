"""Offline tests for `traversal.traverse_reaction`, exercising the bundled
3-EC-example fixtures (`build_context.EXAMPLE_REACTIONS`/
`EXAMPLE_FRAME_TO_FIXTURE`) via `biocyc_client.CachedFixtureClient`. No
network calls anywhere in this file.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from biocyc_client import CachedFixtureClient, RateLimiter
from build_context import EXAMPLE_FRAME_TO_FIXTURE, EXAMPLE_ORGID, EXAMPLE_TAXID, FIXTURES_DIR
from traversal import traverse_reaction


def make_client():
    return CachedFixtureClient(FIXTURES_DIR, EXAMPLE_FRAME_TO_FIXTURE, rate_limiter=RateLimiter(min_spacing_seconds=0.0))


def test_pgi_preserves_both_catalysts_never_collapsed():
    """PGLUCISOM-RXN is catalyzed by two distinct enzyme complexes in
    EcoCyc -- the canonical Pgi homodimer and a cross-reactive KduI
    homodimer. Both must be returned, each attributed to its own gene."""
    entry = traverse_reaction(make_client(), EXAMPLE_ORGID, "PGLUCISOM-RXN", "pgi",
                               expected_ec="5.3.1.9", expected_taxid=EXAMPLE_TAXID)
    assert entry["outcome"] == "ok"
    assert entry["ec_match"] is True
    assert entry["organism_verified"] is True
    gene_symbols = {ge["gene_symbol"] for ge in entry["genes_and_enzymes"]}
    assert gene_symbols == {"pgi", "kduI"}
    legacy_tags = {ge["legacy_locus_tag"] for ge in entry["genes_and_enzymes"]}
    assert legacy_tags == {"b4025", "b2843"}
    # Neither enzyme is dropped even though they share one EC number.
    assert len(entry["genes_and_enzymes"]) == 2


def test_pfk_isoenzyme_ambiguity_preserved():
    """pfkA and pfkB are both retained against the single EC 2.7.1.11,
    never collapsed to one gene."""
    entry = traverse_reaction(make_client(), EXAMPLE_ORGID, "6PFRUCTPHOS-RXN", "pfk",
                               expected_ec="2.7.1.11", expected_taxid=EXAMPLE_TAXID)
    assert entry["outcome"] == "ok"
    gene_symbols = {ge["gene_symbol"] for ge in entry["genes_and_enzymes"]}
    assert gene_symbols == {"pfkA", "pfkB"}
    assert len(entry["genes_and_enzymes"]) == 2


def test_zwf_single_monomer_not_over_expanded():
    """A single-monomer enzyme must yield exactly one GeneEnzymeLink, not
    be spuriously split."""
    entry = traverse_reaction(make_client(), EXAMPLE_ORGID, "GLU6PDEHYDROG-RXN", "zwf",
                               expected_ec="1.1.1.49", expected_taxid=EXAMPLE_TAXID)
    assert entry["outcome"] == "ok"
    assert len(entry["genes_and_enzymes"]) == 1
    ge = entry["genes_and_enzymes"][0]
    assert ge["role"] == "monomer"
    assert ge["gene_symbol"] == "zwf"
    assert ge["legacy_locus_tag"] == "b1852"


def test_organism_cross_reference_verified_not_assumed_from_orgid_string():
    """organism_verified must come from the PGDB's own NCBI-TAXONOMY-DB
    dblink, not merely be True because the caller passed a plausible taxid."""
    entry_correct = traverse_reaction(make_client(), EXAMPLE_ORGID, "GLU6PDEHYDROG-RXN", "zwf",
                                       expected_taxid=EXAMPLE_TAXID)
    assert entry_correct["organism_verified"] is True
    assert entry_correct["ncbi_taxonomy_id"] == EXAMPLE_TAXID

    entry_wrong = traverse_reaction(make_client(), EXAMPLE_ORGID, "GLU6PDEHYDROG-RXN", "zwf",
                                     expected_taxid="59919")  # MED4's taxid -- deliberately wrong for ECOLI.
    assert entry_wrong["organism_verified"] is False


def test_ec_mismatch_reported_not_silently_accepted():
    entry = traverse_reaction(make_client(), EXAMPLE_ORGID, "GLU6PDEHYDROG-RXN", "zwf", expected_ec="9.9.9.9")
    assert entry["ec_number"] == "1.1.1.49"
    assert entry["ec_match"] is False


def test_bounded_pathway_detail_limit_records_but_does_not_fetch_extra_pathways():
    entry = traverse_reaction(make_client(), EXAMPLE_ORGID, "PGLUCISOM-RXN", "pgi", pathway_detail_limit=1)
    assert len(entry["pathways"]) == 2  # GLYCOLYSIS + UDPNAGSYN-PWY both recorded
    fetched = [p for p in entry["pathways"] if p["pathway"].get("common_name")]
    unfetched = [p for p in entry["pathways"] if not p["pathway"].get("common_name")]
    assert len(fetched) == 1
    assert len(unfetched) == 1
    assert "bounded" in unfetched[0]["pathway"]["note"]


def test_missing_frame_stops_that_branch_without_crashing_whole_traversal():
    client = make_client()
    entry = traverse_reaction(client, EXAMPLE_ORGID, "NOT-A-REAL-FRAME", "bogus", expected_ec="0.0.0.0")
    assert entry["outcome"] == "missing_frame"
    assert entry["genes_and_enzymes"] == []
    assert entry["biocyc_reaction"]["state"] == "unavailable_source"


def test_gated_reaction_frame_distinct_outcome_from_missing_frame():
    frame_map = dict(EXAMPLE_FRAME_TO_FIXTURE)
    frame_map["ECOLI:GATED-RXN"] = ("synthetic_gated_response.html", 404)
    client = CachedFixtureClient(FIXTURES_DIR, frame_map, rate_limiter=RateLimiter(min_spacing_seconds=0.0))
    entry = traverse_reaction(client, EXAMPLE_ORGID, "GATED-RXN", "gated-example")
    assert entry["outcome"] == "gated"
    assert entry["outcome"] != "missing_frame"


def test_result_is_json_serializable():
    entry = traverse_reaction(make_client(), EXAMPLE_ORGID, "PGLUCISOM-RXN", "pgi")
    json.dumps(entry)  # must not raise
