# Formula grammar: what the parser accepts and rejects

`scripts/formula_parser.py` replaces a regex like `[A-Z][a-z]?\d*` applied
repeatedly, which silently accepts only the simplest elemental formulas
and silently mis-parses (or silently drops) anything else. The parser used
by this skill is a small recursive-descent grammar with an explicit
allow-list of real element symbols.

## Accepted

| Construct | Example | Parses as |
|---|---|---|
| Flat elemental formula | `C21H26N7O17P3` | `{C:21, H:26, N:7, O:17, P:3}` |
| Nested brackets with multiplier | `Ca(OH)2` | `{Ca:1, O:2, H:2}` |
| Deeply nested brackets | `[Co(NH3)6]Cl3` | `{Co:1, N:6, H:18, Cl:3}` |
| Hydrate/adduct separator (`.`, `*`, `·`) | `CuSO4.5H2O` | `{Cu:1, S:1, O:9, H:10}` |
| Implicit count of 1 | `NaCl` | `{Na:1, Cl:1}` |

## Rejected (raises `FormulaParseError`, never silently parsed as
something else)

| Construct | Example | Why rejected |
|---|---|---|
| R-group placeholder | `RCOOH`, `C6H12O6R1` | Not a fully-specified molecule; balancing against it is meaningless. |
| Wildcard token | `X2O3` | `X`/`Y`/`Z*` are drawing-convention wildcards, not element symbols. |
| Unknown/typo element symbol | `Qx2` | Not in the IUPAC element table — silently accepting it would count phantom atoms. |
| Unbalanced brackets | `Ca(OH2` | Cannot determine grouping/multiplier scope. |
| Hydrate separator inside brackets | `(H2O.NaCl)` | Not valid formula syntax; ambiguous scope. |
| Empty formula | `""` | Nothing to balance. |

## Explicitly out of scope (would need a grammar extension, not a
work-around)

- Isotope labels (e.g. `[13C]C5H11O`).
- Charge annotations embedded in the formula string itself (this skill
  tracks `charge` as a separate independently-retrieved field, per the
  crosswalk schema's `MappedIdentifier`/ChEBI-properties convention —
  never parse charge out of the formula string).
- Polymeric/repeat-unit notation (e.g. `(C6H10O5)n` with a literal `n`).

If a real retrieved ChEBI formula needs one of these, that is a signal to
extend `formula_parser.py`'s grammar deliberately (with tests) — not to
fall back to a looser regex that would silently mis-parse other, simpler
formulas too.

## Why this matters for the mass/charge balance check

`scripts/validate_crosswalk.py` calls `parse_formula` once per
stoichiometry participant. A `FormulaParseError` is reported as a
**validation error requiring curator attention**, not silently caught and
skipped — an un-parseable formula means the balance check for that whole
reaction cannot run, which is itself an important finding (either the
formula needs curation, or the parser's grammar needs a deliberate,
tested extension).
