"""Offline, deterministic known-answer tests for build_canonical_records.py
against fixtures/synthetic_subset.json (a fabricated, clearly-labeled
fixture -- never real BRENDA evidence)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from build_canonical_records import build_records  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SYNTHETIC_SUBSET_PATH = FIXTURES_DIR / "synthetic_subset.json"


def _load_subset() -> dict:
    return json.loads(SYNTHETIC_SUBSET_PATH.read_text(encoding="utf-8"))


def _record_for(records: list[dict], ec: str, protein_id: str) -> dict:
    for r in records:
        if r["ec_number"] == ec and r["brenda_protein_id"] == protein_id:
            return r
    raise AssertionError(f"no record for {ec}/{protein_id}")


def test_fixture_is_labeled_synthetic_and_never_treated_as_evidence():
    subset = _load_subset()
    assert subset["_synthetic_do_not_use_as_evidence"] is True


def test_deterministic_replay_is_byte_identical():
    subset = _load_subset()
    pass1 = json.dumps(build_records(subset), indent=2, sort_keys=True)
    pass2 = json.dumps(build_records(subset), indent=2, sort_keys=True)
    assert pass1 == pass2


def test_positive_value_and_ligand_linkage_preserved():
    subset = _load_subset()
    records = build_records(subset)["records"]
    rec = _record_for(records, "1.1.1.49", "9001")
    km_obs = next(o for o in rec["kinetic_observations"] if o["parameter_type"] == "km_value" and o["ligand"] == "D-glucose 6-phosphate")
    assert km_obs["point_value"] == 0.1
    assert not km_obs["is_range"]
    assert not km_obs["is_sentinel_more"]


def test_range_value_preserves_bounds():
    subset = _load_subset()
    records = build_records(subset)["records"]
    rec = _record_for(records, "1.1.1.49", "9001")
    range_obs = next(o for o in rec["kinetic_observations"] if o["ligand"] == "fabricated-substrate-X")
    assert range_obs["is_range"]
    assert range_obs["range_low"] == 0.05
    assert range_obs["range_high"] == 0.09


def test_sentinel_more_value_is_not_a_fake_negative_number():
    subset = _load_subset()
    records = build_records(subset)["records"]
    rec = _record_for(records, "1.1.1.49", "9001")
    sentinel_obs = next(o for o in rec["kinetic_observations"] if o["parameter_type"] == "turnover_number")
    assert sentinel_obs["is_sentinel_more"]
    assert sentinel_obs["point_value"] is None
    assert "174 s-1" in sentinel_obs["assay_comment"]


def test_literature_linkage_populated_from_referenced_pmid():
    subset = _load_subset()
    records = build_records(subset)["records"]
    rec = _record_for(records, "1.1.1.49", "9001")
    km_obs = next(o for o in rec["kinetic_observations"] if o["ligand"] == "D-glucose 6-phosphate")
    assert km_obs["literature"][0]["pmid"] == "10194349"


def test_literature_missing_pmid_is_explicit_not_fabricated():
    subset = _load_subset()
    records = build_records(subset)["records"]
    rec = _record_for(records, "5.3.1.9", "9002")
    km_obs = rec["kinetic_observations"][0]
    assert km_obs["literature"][0]["pmid"] is None


def test_organism_is_species_level_and_strain_is_never_promoted():
    subset = _load_subset()
    records = build_records(subset)["records"]
    rec = _record_for(records, "1.1.1.49", "9001")
    assert rec["organism_species"] == "Escherichia coli"
    assert "does not assert or infer any strain" in rec["strain_note"]
    assert any("K-10" in hint for hint in rec["strain_hints_from_comments"])


def test_species_distinction_across_two_different_ecs():
    subset = _load_subset()
    records = build_records(subset)["records"]
    ecoli_rec = _record_for(records, "1.1.1.49", "9001")
    pmarinus_rec = _record_for(records, "4.1.1.39", "9003")
    assert ecoli_rec["organism_species"] != pmarinus_rec["organism_species"]
    assert pmarinus_rec["organism_species"] == "Prochlorococcus marinus"


def test_manual_vs_text_mined_provenance_is_explicit_not_fabricated():
    subset = _load_subset()
    records = build_records(subset)["records"]
    rec = _record_for(records, "1.1.1.49", "9001")
    for obs in rec["kinetic_observations"]:
        assert obs["manual_vs_text_mined"].startswith("unavailable_source")
