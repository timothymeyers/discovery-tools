"""Parser for BRENDA bulk-JSON `numeric_dataset`/`text_dataset` value strings.

BRENDA's bulk JSON release (schema v2.0.0, see
`fixtures/brenda_schema/enzyme.schema.json`) encodes every
substrate/inhibitor-linked numeric observation (km_value, turnover_number,
kcat_km_value, ki_value, specific_activity, ...) as a single free-text `value`
string of the form:

    "<number>[-<number>] {<substrate/inhibitor name>}"   e.g. "0.045 {NADP+}"
    "<number>[-<number>]"                                 e.g. "12.34" (specific_activity,
                                                            enzyme-level, no named substrate)
    "-999 {more}"                                          BRENDA's documented sentinel for
                                                            "no numeric value here; see the
                                                            sibling `comment` field instead"

This module turns that single string into a structured, lossless representation
(raw string retained verbatim; range bounds and the sentinel case kept explicit
rather than collapsed to a single point estimate) that
`scripts/build_canonical_records.py` maps onto per-observation canonical
records.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_VALUE_RE = re.compile(
    r"^(?P<number>-?[0-9]+(?:\.[0-9]+)?(?:-[0-9]+(?:\.[0-9]+)?)?|-999)"
    r"(?:\s*\{(?P<label>.*)\})?\s*$"
)

SENTINEL_MORE = "-999"


@dataclass(frozen=True)
class ParsedBrendaValue:
    raw: str
    substrate_or_inhibitor_name: Optional[str]
    is_sentinel_more: bool
    is_range: bool
    point_value: Optional[float]
    range_low: Optional[float]
    range_high: Optional[float]


class BrendaValueParseError(ValueError):
    """Raised when a BRENDA numeric_dataset `value` string doesn't match the
    documented `<number>[-<number>] {label}` / bare-number / `-999 {more}` grammar."""


def parse_brenda_numeric_value(raw_value: str) -> ParsedBrendaValue:
    """Parse a single BRENDA bulk-JSON numeric_dataset `value` string.

    Raises BrendaValueParseError on unrecognized input rather than silently
    guessing, so malformed/unexpected upstream data surfaces instead of being
    quietly misrepresented.
    """
    if raw_value is None:
        raise BrendaValueParseError("value is None")

    match = _VALUE_RE.match(raw_value.strip())
    if not match:
        raise BrendaValueParseError(f"unrecognized BRENDA value string: {raw_value!r}")

    number_part = match.group("number")
    label = match.group("label")
    label = label.strip() if label else None

    if number_part == SENTINEL_MORE:
        return ParsedBrendaValue(
            raw=raw_value,
            substrate_or_inhibitor_name=label,
            is_sentinel_more=True,
            is_range=False,
            point_value=None,
            range_low=None,
            range_high=None,
        )

    if "-" in number_part[1:]:
        # A bound-to-bound range, e.g. "0.47-1". Split on the first '-' that is
        # not a leading negative sign (numbers here are all non-negative in
        # practice, but guard the leading '-' anyway for robustness).
        low_str, high_str = number_part[1:].split("-", 1)
        low_str = number_part[0] + low_str if number_part[0] == "-" else low_str
        low = float(low_str)
        high = float(high_str)
        return ParsedBrendaValue(
            raw=raw_value,
            substrate_or_inhibitor_name=label,
            is_sentinel_more=False,
            is_range=True,
            point_value=None,
            range_low=low,
            range_high=high,
        )

    return ParsedBrendaValue(
        raw=raw_value,
        substrate_or_inhibitor_name=label,
        is_sentinel_more=False,
        is_range=False,
        point_value=float(number_part),
        range_low=None,
        range_high=None,
    )
