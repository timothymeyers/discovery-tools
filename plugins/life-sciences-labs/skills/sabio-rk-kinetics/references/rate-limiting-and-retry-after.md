# Rate Limiting and Retry-After Handling

## The documented contract

The SABIO-RK Export API documents (and a live probe confirmed) a rate limit
of **60 requests per 60-second window per client IP**, shared across all
`/export-api/` routes. `RateLimiter` in `scripts/sabio_rk_client.py`
enforces this as a sliding window: at most `max_requests` calls are allowed
in any `window`-second span; a call that would exceed the limit sleeps until
the oldest call in the window has aged out.

NCBI Taxonomy's E-utils service (used for taxid ancestry resolution, a
*separate* dependency from SABIO-RK itself) has its own, much stricter,
documented unauthenticated limit (3 requests/second) — `NcbiTaxonomyClient`
uses its own independent `RateLimiter` instance with those parameters, never
sharing state with the SABIO-RK client's limiter.

## Honoring `Retry-After` on HTTP 429

Per RFC 9110, a `429 Too Many Requests` response may include a `Retry-After`
header giving either an integer/float number of seconds or an HTTP-date.
`parse_retry_after_seconds(headers)` parses both forms (case-insensitive
header lookup) and returns `None` if the header is absent or unparseable.

`SabioClient.get_json` honors a present, parseable `Retry-After` value
exactly (sleeping for that duration via the injectable `sleep_fn`, so tests
never actually wait in real time) before retrying, and falls back to a
computed default backoff (`window / max_requests + small epsilon`) only when
the header is missing or malformed. This means the client defers to the
server's own stated backoff whenever it is given, rather than always
guessing.

## Why this belongs in the client layer, not the caller

Retry-on-429 with correct backoff is a cross-cutting concern that every
query tier, every page, and every EC number needs identically. Putting it in
`SabioClient.get_json` means callers (query-tier logic, the CLI, the live
probe) never need to reimplement or forget it.

## Tests

`tests/test_sabio_rk_client.py` covers: `RateLimiter` allowing a burst up to
its limit without sleeping, sleeping the correct amount when a burst exceeds
the limit, not sleeping once the window has genuinely elapsed;
`parse_retry_after_seconds` parsing an integer-seconds string, case-
insensitive header lookup, and returning `None` for an absent or
unparseable header; and `SabioClient.get_json` retrying exactly once on a
429 response (using the `outcome_429_retry_after.json` fixture) and sleeping
for the *exact* `Retry-After` value rather than a computed fallback.
