#!/usr/bin/env python3
"""A real chemical-formula parser for crosswalk mass-balance checks.

Replaces the "simple elemental formula" regex approach (`[A-Z][a-z]?\\d*`
repeated with no bracket/hydrate support) that silently mis-parses or
silently drops anything it does not recognize. This parser:

  * handles nested brackets `()`, `[]`, `{}` with a following multiplier,
    e.g. `Ca(OH)2`, `[Co(NH3)6]Cl3`;
  * handles hydrate/adduct separators `.`, `*`, `\u00b7` followed by a
    leading count and a sub-formula, e.g. `MgSO4.7H2O`, `CuSO4*5H2O`;
  * validates every element token against a fixed periodic-table symbol
    set (rejects invented/typo'd symbols instead of silently accepting
    them as a 1-letter element);
  * REJECTS rather than silently mis-parses: R-group placeholders (`R`,
    `R1`, `R2`, ...), wildcard/variable tokens (`X`, `Y`, `n` as a bare
    stoichiometric variable), unbalanced brackets, empty formulas, and any
    character that is not an element symbol, digit, bracket, or hydrate
    separator.

This is deliberately NOT a full IUPAC/InChI-formula grammar (no isotope
labels, no charge-in-formula-string parsing -- charge is tracked as a
separate independently-retrieved field per the crosswalk schema). If a
retrieved ChEBI formula uses syntax this parser does not support, that is
a signal to widen the grammar deliberately, not to fall back to a looser
regex.
"""
from __future__ import annotations

from collections import Counter

# IUPAC periodic table symbols (1-118). Kept as a flat allow-list so an
# invented/typo'd "element" (e.g. a stray "Q" or "J", neither of which is
# a real element symbol) is rejected rather than silently counted.
ELEMENT_SYMBOLS = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni
Cu Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe
Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg
Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr Rf Db Sg
Bh Hs Mt Ds Rg Cn Nh Fl Mc Lv Ts Og
""".split())

# Explicit reject-list of tokens that look like element symbols but are
# placeholders/wildcards in structure-drawing conventions, not real atoms.
# ("N" and "O" are real elements and stay valid; "R"/"R1"/"X" are not.)
PLACEHOLDER_PREFIXES = ("R",)  # matched only when the *entire* token is R/R\d+
WILDCARD_TOKENS = frozenset({"X", "Y", "Z*"})

HYDRATE_SEPARATORS = (".", "*", "\u00b7")
OPEN_BRACKETS = {"(": ")", "[": "]", "{": "}"}
CLOSE_BRACKETS = {v: k for k, v in OPEN_BRACKETS.items()}


class FormulaParseError(ValueError):
    """Raised when a formula uses syntax this parser does not support, or
    contains a token that is not a recognized element symbol. The caller
    must treat this as "cannot verify balance for this entity" (an
    explicit unresolved/unavailable check) -- never silently skip it."""


def _tokenize(formula: str):
    i, n = 0, len(formula)
    while i < n:
        c = formula[i]
        if c.isspace():
            i += 1
            continue
        if c in OPEN_BRACKETS or c in CLOSE_BRACKETS:
            yield ("BRACKET", c)
            i += 1
            continue
        if c in HYDRATE_SEPARATORS:
            yield ("HYDRATE_SEP", c)
            i += 1
            continue
        if c.isdigit():
            j = i
            while j < n and formula[j].isdigit():
                j += 1
            yield ("NUMBER", int(formula[i:j]))
            i = j
            continue
        if c.isupper():
            j = i + 1
            while j < n and formula[j].islower():
                j += 1
            token = formula[i:j]
            # A trailing digit run directly after an upper+lower symbol,
            # e.g. "R1", is part of a placeholder token, not a count.
            k = j
            while k < n and formula[k].isdigit():
                k += 1
            if token in PLACEHOLDER_PREFIXES and k > j:
                yield ("PLACEHOLDER", formula[i:k])
                i = k
                continue
            if token in WILDCARD_TOKENS or token in PLACEHOLDER_PREFIXES:
                yield ("PLACEHOLDER", token)
                i = j
                continue
            yield ("ELEMENT", token)
            i = j
            continue
        raise FormulaParseError(
            f"Unsupported character {c!r} at position {i} in formula {formula!r}"
        )


def _parse_group(tokens, pos, formula, in_bracket=False):
    """Recursive-descent parse of one bracket level. Returns (Counter, next_pos)."""
    counts: Counter = Counter()
    n = len(tokens)
    while pos < n:
        kind, val = tokens[pos]
        if kind == "BRACKET" and val in OPEN_BRACKETS:
            sub_counts, pos = _parse_group(tokens, pos + 1, formula, in_bracket=True)
            mult = 1
            if pos < n and tokens[pos][0] == "NUMBER":
                mult = tokens[pos][1]
                pos += 1
            for el, cnt in sub_counts.items():
                counts[el] += cnt * mult
            continue
        if kind == "BRACKET" and val in CLOSE_BRACKETS:
            if not in_bracket:
                raise FormulaParseError(
                    f"Unmatched closing bracket {val!r} in formula {formula!r}"
                )
            return counts, pos + 1
        if kind == "ELEMENT":
            mult = 1
            pos += 1
            if pos < n and tokens[pos][0] == "NUMBER":
                mult = tokens[pos][1]
                pos += 1
            counts[val] += mult
            continue
        if kind == "PLACEHOLDER":
            raise FormulaParseError(
                f"Formula {formula!r} contains an R-group/wildcard placeholder "
                f"({val!r}) -- this parser only balances fully-specified "
                "elemental formulas; flag this entity as unresolved instead "
                "of guessing a substituent."
            )
        if kind == "HYDRATE_SEP":
            # Handled by the caller (top level only); a separator nested
            # inside a bracket is not valid formula syntax.
            if in_bracket:
                raise FormulaParseError(
                    f"Hydrate/adduct separator {val!r} not permitted inside "
                    f"brackets in formula {formula!r}"
                )
            return counts, pos
        raise FormulaParseError(f"Unexpected token {kind}:{val!r} in formula {formula!r}")
    if in_bracket:
        raise FormulaParseError(f"Unclosed bracket in formula {formula!r}")
    return counts, pos


def parse_formula(formula: str) -> Counter:
    """Parse a molecular formula into an element -> total-atom-count Counter.

    Supports nested brackets and `.`/`*`/`\u00b7`-separated hydrate/adduct
    components (each with its own leading multiplier, default 1). Raises
    `FormulaParseError` -- never silently drops or mis-parses -- on
    R-group placeholders, wildcard tokens, unbalanced brackets, unknown
    element symbols, or any unrecognized character.
    """
    if not formula or not formula.strip():
        raise FormulaParseError("Empty formula")
    tokens = list(_tokenize(formula))
    if not tokens:
        raise FormulaParseError(f"No parseable tokens in formula {formula!r}")

    total: Counter = Counter()
    pos = 0
    n = len(tokens)
    # Leading component, then zero or more hydrate/adduct components.
    first = True
    while pos < n:
        if tokens[pos][0] == "HYDRATE_SEP":
            pos += 1
            mult = 1
            if pos < n and tokens[pos][0] == "NUMBER":
                mult = tokens[pos][1]
                pos += 1
            comp_counts, pos = _parse_group(tokens, pos, formula, in_bracket=False)
            if not comp_counts:
                raise FormulaParseError(
                    f"Hydrate/adduct separator with no following formula in {formula!r}"
                )
            for el, cnt in comp_counts.items():
                total[el] += cnt * mult
            first = False
            continue
        comp_counts, pos = _parse_group(tokens, pos, formula, in_bracket=False)
        if not comp_counts and first:
            raise FormulaParseError(f"Could not parse any element from formula {formula!r}")
        for el, cnt in comp_counts.items():
            total[el] += cnt
        first = False

    unknown = sorted(set(total) - ELEMENT_SYMBOLS)
    if unknown:
        raise FormulaParseError(
            f"Formula {formula!r} contains symbol(s) not in the IUPAC element "
            f"table: {unknown} -- refusing to silently count these as atoms"
        )
    return total


if __name__ == "__main__":
    import sys

    for f in sys.argv[1:] or ["C21H26N7O17P3", "Ca(OH)2", "CuSO4.5H2O", "[Co(NH3)6]Cl3"]:
        try:
            print(f, "->", dict(parse_formula(f)))
        except FormulaParseError as exc:
            print(f, "-> REJECTED:", exc)
