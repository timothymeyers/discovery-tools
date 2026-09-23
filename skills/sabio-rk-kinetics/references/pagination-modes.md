# Pagination Modes

## The problem a fixed page cap creates

A hardcoded `max_pages=1` cap silently truncates any query whose result set
spans more than one page — a downstream consumer sees only the first
`page_size` entries with no indication that more existed, unless the
truncation is explicitly reported. For a low-volume EC number this is
invisible; for a high-volume one it can retain a small, unrepresentative
slice while implying (by omission) that it is the complete result.

## Two named modes, both honest about truncation

`fetch_tier_entries(..., pagination_mode=...)` supports:

- **`"sample"`** — bounded to `MAX_PAGES_SAMPLE` (currently 1) page. Fast,
  representative for exploration, but **always** reports whether more pages
  existed via `total_pages_available` and `truncated: true` when they did.
- **`"exhaustive"`** — follows `meta.total_pages` up to a safety cap
  (`MAX_PAGES_EXHAUSTIVE_SAFETY_CAP`, currently 50) so it can never run away
  on an unexpectedly huge result set, while still retrieving the full result
  for realistically-sized queries.

An explicit `max_pages` argument overrides the mode-derived default when a
caller needs a different bound.

## Every tier summary reports the same fields regardless of mode

```json
{
  "pagination_mode": "sample",
  "pages_fetched": 1,
  "total_count": 628,
  "total_pages_available": 7,
  "truncated": true,
  "entries_fetched": 100
}
```

`truncated` is computed as `pages_fetched < total_pages_available` whenever
`total_pages_available` is known — never silently omitted. A caller relying
on a `"sample"` pass for a quick check can always tell, from this one field,
whether it needs to re-run in `"exhaustive"` mode to get the complete result.

## Tests

`tests/test_query_tiers_and_pagination.py` covers: sample mode stopping
after one page with `truncated: true` (`pagination_page1_of2.json`
fixture), exhaustive mode following both pages with `truncated: false`
(`pagination_page1_of2.json` + `pagination_page2_of2.json`), an invalid
`pagination_mode` raising `ValueError`, and a malformed top-level response
(`outcome_malformed_response.json`) stopping the tier as a `parser` failure
rather than crashing.
