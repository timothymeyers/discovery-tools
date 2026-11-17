# Missingness vs. access-outcome vocabulary

These are two different axes. Conflating them is the single most common way
a QC layer turns "we couldn't check" into "the value doesn't exist" (or vice
versa).

## Axis 1 — observation missingness (per-field state)

Every scalar field (`ObservedValue`) and every cross-database identifier
(`SourceIdentifier`) in the reaction-evidence schema carries a `state` from
this fixed six-value enum:

| State | Meaning | Requires |
|---|---|---|
| `present` | Value/identifier is known and populated. | `raw_value` + `normalized_value` (or `id`) |
| `unknown` | Applicable, but not yet determined from any source *actually consulted*. | — |
| `not_applicable` | The concept doesn't apply to this observation (e.g. `ph` for a reaction-stoichiometry-only row). | — |
| `imputed` | Value was derived/estimated rather than directly observed. | `normalized_value` + `imputation_method` (log the *how* in `transformation_history` too) |
| `unavailable_source` | The source that would hold this value exists but could not be reached right now (paywalled, API down, credential-gated). | — |
| `deferred_resolution` | Intentionally left for a later ingestion/crosswalk pass. | — |

**Worked examples of picking the right one:**

- BRENDA reports that it has no Km value for this enzyme/substrate pair, and
  the query itself succeeded (HTTP 200, well-formed response, the field is
  simply empty in that response) → `unknown`. The source was consulted; the
  measurement is genuinely absent from it. This is *not* the same as "no
  such kinetic behavior exists" — see `references/coverage-counting.md` for
  why this must be counted as an explicit gap, not silently omitted.
- The same query fails with a timeout, a 5xx, or a credential wall before
  any answer is returned → `unavailable_source`. You do not yet know whether
  the value exists; you know the *attempt* did not complete.
- A field genuinely doesn't apply to this evidence type (e.g. `ph` for a
  `reaction_stoichiometry` record with no assay) → `not_applicable`.
- You have a raw text ID but haven't run it through the crosswalk yet →
  `deferred_resolution`, not `unknown` — this distinguishes "not yet tried"
  work from "tried, not found."

## Axis 2 — access outcome (per-query/per-source, orthogonal to state)

A source/route can be:

- **reachable and answering** — request succeeded, and the response is
  either populated (`present`) or a genuine empty/absent result (`unknown`).
- **reachable but incomplete** — e.g. paginated results truncated by a page
  cap; report the truncation itself (count of pages retrieved vs. total
  available) as its own finding, not silently as `unknown` for the un-fetched
  records.
- **unreachable** — transport failure, authentication failure, or a parser
  error on a malformed response. This is `unavailable_source`, and should
  further distinguish *why* (transport vs. auth vs. parse) wherever the
  adapter can tell, so a retry strategy can target the right layer.

**Do not let a query failure downgrade to `unknown`.** If the request never
completed, you have no evidence the value is actually absent — recording it
as `unknown` erases that distinction and will read as "consulted, not
found" to every downstream consumer. Keep access outcome and per-field
missingness as two separate recorded facts, even when a query failure means
every field on that would-be record ends up `unavailable_source`.

## Reference implementation split (informative)

A concrete adapter can implement this split as two constructor helpers: one
for a query that completed and genuinely found nothing (missingness state
`unknown`), and a distinct one for a query that could not complete at all
(missingness state `unavailable_source`, with a `failure_kind` of
`transport` / `authorization` / `parser` recorded alongside it). Never share
one code path for both — the DX-36 worked example in
`known-answer-and-mutation-tests.md` shows what happens when pagination
truncation and lineage-matching are conflated in a similar way.
