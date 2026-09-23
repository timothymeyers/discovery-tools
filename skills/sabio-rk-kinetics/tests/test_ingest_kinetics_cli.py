"""Offline integration test for the `ingest_kinetics` CLI wiring: argument
parsing plumbs organism/EC/taxon parameters through to
`resolve_reaction_entries` and produces the documented JSON envelope. HTTP
is injected via monkeypatching `SabioClient`/`NcbiTaxonomyClient` -- no live
network call.
"""
from __future__ import annotations

import json

import ingest_kinetics
from sabio_rk_client import HttpResponse, RateLimiter, SabioClient
from ncbi_taxonomy_client import NcbiTaxonomyClient

from sabio_test_helpers import load_fixture


def test_run_writes_expected_envelope(tmp_path, monkeypatch):
    def fake_sabio_client(*args, **kwargs):
        def fake_http_get(url):
            if 'Organism%3A%22Escherichia' in url or 'Organism:"Escherichia' in url:
                return HttpResponse(200, json.dumps(load_fixture("tier1_exact_organism_hit.json")).encode("utf-8"), {})
            return HttpResponse(
                200,
                json.dumps({"meta": {"page": 1, "page_size": 100, "total_count": 0, "total_pages": 1}, "data": []}).encode(
                    "utf-8"
                ),
                {},
            )

        return SabioClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=60.0), cache_dir=kwargs.get("cache_dir"))

    def fake_taxonomy_client(*args, **kwargs):
        xml_body = load_fixture("ncbi_efetch_ancestry.xml")

        def fake_http_get(url):
            return HttpResponse(200, xml_body, {})

        return NcbiTaxonomyClient(http_get=fake_http_get, rate_limiter=RateLimiter(max_requests=1000, window=1.0))

    monkeypatch.setattr(ingest_kinetics, "SabioClient", fake_sabio_client)
    monkeypatch.setattr(ingest_kinetics, "NcbiTaxonomyClient", fake_taxonomy_client)

    output_path = tmp_path / "result.json"
    exit_code = ingest_kinetics.run(
        [
            "--ec",
            "1.1.1.1",
            "--organism",
            "Escherichia coli",
            "--genus",
            "Escherichia",
            "--fallback-taxid",
            "1224",
            "--fallback-taxon-name",
            "Pseudomonadota",
            "--pagination-mode",
            "sample",
            "--output",
            str(output_path),
        ]
    )
    assert exit_code == 0
    result = json.loads(output_path.read_text())
    assert result["ec_number"] == "1.1.1.1"
    assert result["organism"] == "Escherichia coli"
    assert result["resolution"]["status"] == "resolved"
    assert result["resolution"]["resolved_tier"] == "tier1_exact_organism"
    assert result["entries_count"] == 1
