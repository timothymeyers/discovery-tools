# Outcome and Failure-Kind Semantics

## The three outcomes that must never be conflated

Every HTTP call via `SabioClient.get_json` records an `outcome`:

- **`"success"`** — HTTP 200, body parsed as valid JSON with the expected
  `{"meta": {...}, "data": [...]}` envelope shape. Says nothing about
  whether `data` is empty or not — that is a caller-level concept (see
  below).
- **`"failure"`** — the call could not be confirmed as either a genuine
  success or a genuine empty result. Always paired with a `failure_kind`:
  - **`"transport"`** — network-level failure, unexpected non-2xx/4xx
    status (e.g. 5xx, or no response at all).
  - **`"authorization"`** — HTTP 401/403. Distinct from transport: the
    server is reachable but is refusing this specific request.
  - **`"parser"`** — HTTP 200 but the body is not valid JSON, or is valid
    JSON but missing the expected `meta`/`data` keys (a malformed top-level
    response).

## Tier- and query-level outcomes

At the tier-resolution level (`resolve_reaction_entries`), the same
three-way distinction is preserved as an overall `status`:

- **`"resolved"`** — some tier returned a nonzero number of entries.
- **`"confirmed_empty"`** — every tier's API call(s) succeeded (HTTP 200,
  well-formed) and genuinely returned zero results. This is a real,
  positive finding: "we asked, and there is nothing here" — not the same
  as not having asked successfully at all.
- **`"query_incomplete_failure"`** — at least one tier could not be
  confirmed empty or resolved, because of a transport, authorization, or
  parser failure somewhere along the way. **This must never be reported the
  same way as `confirmed_empty`** — a downstream consumer treating a failed
  query as "confirmed nothing exists" would silently misrepresent an
  inconclusive result as a conclusive negative finding.

## Why this distinction is load-bearing

A common and expensive mistake in database ingestion is to treat "the API
call didn't return the entries I expected" as a single undifferentiated
"unavailable" state, then design a fallback (or a scientific conclusion)
around that state without knowing whether the underlying cause was "this
data genuinely doesn't exist" (confirmed_empty) or "something went wrong
while asking" (query_incomplete_failure, potentially recoverable by
retrying, fixing auth, or fixing a parser bug). Keeping these separate at
every layer — HTTP call, tier fetch, and overall resolution — means a
downstream QC or reporting step can always trace exactly which case
happened, rather than inheriting an already-collapsed "empty" state.

## Tests

`tests/test_sabio_rk_client.py` covers the four HTTP-level outcome/
failure_kind combinations (success, transport, authorization, parser) using
the `outcome_*` fixtures. `tests/test_query_tiers_and_pagination.py` covers
the tier/overall-status level, including a test proving
`query_incomplete_failure` is never reported as `confirmed_empty` when every
tier's HTTP call actually fails (HTTP 500).
