"""Offline tests for validate_schema.py: the publisher's EC-key regex defect
must be surfaced honestly (never hidden behind a false '0 errors'), and the
release/version/schema-document-version distinction must never be conflated.
No network calls anywhere in this file."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from validate_schema import matches_publisher_ec_key_pattern, validate_entries  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SYNTHETIC_SUBSET_PATH = FIXTURES_DIR / "synthetic_subset.json"

jsonschema = pytest.importorskip("jsonschema")


def _load_subset() -> dict:
    return json.loads(SYNTHETIC_SUBSET_PATH.read_text(encoding="utf-8"))


def test_ec_starting_with_9_serial_fails_publisher_pattern():
    # 5.3.1.9's 4th group is '9', which the publisher's own pattern
    # (B?[1-7][0-9]{0,2}) cannot match -- a genuine publisher-side defect,
    # not a bug in this skill's extraction.
    assert matches_publisher_ec_key_pattern("5.3.1.9") is False


def test_ec_with_serial_in_1_to_7_range_matches_publisher_pattern():
    assert matches_publisher_ec_key_pattern("1.1.1.49") is True


def test_validate_entries_surfaces_the_silent_skip_rather_than_hiding_it():
    subset = _load_subset()
    result = validate_entries(subset["data"])
    assert "5.3.1.9" in result["silently_skipped_by_publisher_pattern_property_gate"]
    assert "1.1.1.49" not in result["silently_skipped_by_publisher_pattern_property_gate"]


def test_direct_validation_still_checks_the_silently_skipped_entry():
    subset = _load_subset()
    result = validate_entries(subset["data"])
    # Even though 5.3.1.9 would be silently skipped by a naive corpus-level
    # pass, this skill validates it directly against enzyme.schema.json
    # anyway, so a real defect there could never be masked.
    assert "5.3.1.9" in result["per_ec_result"]
    assert result["per_ec_result"]["5.3.1.9"]["would_be_silently_skipped_by_publisher_pattern_property_gate"] is True


def test_release_version_and_schema_document_version_are_never_conflated():
    subset = _load_subset()
    assert subset["release"] == "2026.1"
    assert subset["version"] == "1"
    schema = json.loads((FIXTURES_DIR / "brenda_schema" / "brenda.schema.json").read_text())
    assert "2.0.0" in schema["$id"]
    # Three genuinely distinct numbers must never be treated as the same field.
    assert len({subset["release"], subset["version"], "2.0.0"}) == 3
