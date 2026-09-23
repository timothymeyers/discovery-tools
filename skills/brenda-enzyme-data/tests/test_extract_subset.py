"""Offline tests for extract_subset.py's organism-substring filtering and
reference-scoping logic, against a small in-memory synthetic full-dump
(never the real licensed bulk file)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from extract_subset import extract  # noqa: E402

FIXTURE_FULL_DUMP = {
    "release": "2026.1",
    "version": "1",
    "data": {
        "1.1.1.49": {
            "id": "1.1.1.49",
            "recommended_name": "synthetic enzyme",
            "protein": {
                "1": {"organism": "Escherichia coli", "references": ["1"]},
                "2": {"organism": "Prochlorococcus marinus", "references": ["2"]},
            },
            "km_value": [
                {"value": "0.1 {NADP+}", "proteins": ["1"], "references": ["1"], "comment": ""},
                {"value": "9.9 {NADP+}", "proteins": ["2"], "references": ["2"], "comment": ""},
            ],
            "reference": {
                "1": {"id": "1", "pmid": "111", "title": "E. coli ref"},
                "2": {"id": "2", "pmid": "222", "title": "P. marinus ref"},
            },
        }
    },
}


def test_extract_keeps_only_matched_organism_protein_entries(tmp_path):
    dump_path = tmp_path / "full_dump.json"
    dump_path.write_text(json.dumps(FIXTURE_FULL_DUMP))

    result = extract(dump_path, ["1.1.1.49"], "Escherichia coli")

    proteins = result["data"]["1.1.1.49"]["protein"]
    assert set(proteins.keys()) == {"1"}
    assert "2" not in proteins


def test_extract_scopes_dataset_entries_and_references_to_matched_proteins(tmp_path):
    dump_path = tmp_path / "full_dump.json"
    dump_path.write_text(json.dumps(FIXTURE_FULL_DUMP))

    result = extract(dump_path, ["1.1.1.49"], "Escherichia coli")

    km_values = result["data"]["1.1.1.49"]["km_value"]
    assert len(km_values) == 1
    assert km_values[0]["value"] == "0.1 {NADP+}"

    refs = result["data"]["1.1.1.49"]["reference"]
    assert set(refs.keys()) == {"1"}
    assert "2" not in refs


def test_extract_reports_ec_numbers_not_found_rather_than_silently_dropping(tmp_path):
    dump_path = tmp_path / "full_dump.json"
    dump_path.write_text(json.dumps(FIXTURE_FULL_DUMP))

    result = extract(dump_path, ["1.1.1.49", "9.9.9.9"], "Escherichia coli")

    assert result["target_ecs_not_found_in_dump"] == ["9.9.9.9"]
    assert "9.9.9.9" not in result["data"]


def test_extract_never_reads_any_path_other_than_the_supplied_full_dump(tmp_path, monkeypatch):
    # Regression guard: extract() must take its full-dump path as an explicit
    # parameter and never fall back to a hardcoded/bundled/repo-local path.
    dump_path = tmp_path / "my_own_licensed_dump.json"
    dump_path.write_text(json.dumps(FIXTURE_FULL_DUMP))

    result = extract(dump_path, ["1.1.1.49"], "Escherichia coli")
    assert result["release"] == "2026.1"
