"""Offline, deterministic tests for the brenda_value_parser module.

No network calls anywhere in this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from brenda_value_parser import BrendaValueParseError, parse_brenda_numeric_value  # noqa: E402


def test_positive_scalar_value_with_ligand():
    parsed = parse_brenda_numeric_value("0.1 {D-glucose 6-phosphate}")
    assert parsed.point_value == 0.1
    assert parsed.substrate_or_inhibitor_name == "D-glucose 6-phosphate"
    assert not parsed.is_range
    assert not parsed.is_sentinel_more


def test_positive_bare_value_no_ligand():
    parsed = parse_brenda_numeric_value("212.6")
    assert parsed.point_value == 212.6
    assert parsed.substrate_or_inhibitor_name is None


def test_range_value_preserves_both_bounds_not_a_midpoint():
    parsed = parse_brenda_numeric_value("0.05-0.09 {fabricated-substrate-X}")
    assert parsed.is_range
    assert parsed.range_low == 0.05
    assert parsed.range_high == 0.09
    assert parsed.point_value is None


def test_sentinel_more_is_not_coerced_to_a_negative_reading():
    parsed = parse_brenda_numeric_value("-999 {more}")
    assert parsed.is_sentinel_more
    assert parsed.point_value is None
    assert parsed.substrate_or_inhibitor_name == "more"


def test_bare_sentinel_without_label():
    parsed = parse_brenda_numeric_value("-999")
    assert parsed.is_sentinel_more
    assert parsed.point_value is None


def test_charged_ion_ligand_not_split_on_plus():
    parsed = parse_brenda_numeric_value("0.45 {Zn2+}")
    assert parsed.substrate_or_inhibitor_name == "Zn2+"
    assert parsed.point_value == 0.45


def test_unrecognized_value_string_raises_rather_than_guesses():
    with pytest.raises(BrendaValueParseError):
        parse_brenda_numeric_value("not-a-brenda-value")


def test_none_value_raises():
    with pytest.raises(BrendaValueParseError):
        parse_brenda_numeric_value(None)  # type: ignore[arg-type]
