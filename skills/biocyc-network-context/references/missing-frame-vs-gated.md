# Missing-frame vs gated-response semantics

`scripts/response_classifier.py::classify_response(http_status, body_text)`
distinguishes two outcomes that are easy to conflate as a generic "the
request failed":

| Outcome | Meaning | Signature |
|---|---|---|
| `missing_frame` | The queried frame ID genuinely does not exist in the target PGDB. A confirmed, object-scoped negative result. | HTTP 404 **and** body contains `"was not found in database <ORGID>"`. |
| `gated` | The object page is blocked by a bot/subscription challenge rather than a genuine "no such frame" result -- access could not be confirmed either way. | HTTP 404 **and** an hCaptcha-bearing Content-Security-Policy marker **and** the not-found message above is absent. |
| `ok` | A normal response. | Not a 404, regardless of whether generic subscription/login marketing chrome (present on essentially every BioCyc page, including fully open ones) appears in the body. |

## Why these must never be conflated

- Reporting a `gated` response as `missing_frame` would silently assert "this
  reaction/gene/pathway does not exist" when the true state is "access could
  not be confirmed" -- a false negative that could propagate into a
  downstream conclusion that a real biological entity is absent.
- Reporting a `missing_frame` response as `gated` would silently assert "this
  requires a subscription" when the true state is "the specific frame ID was
  wrong or stale" -- masking a real, fixable identifier bug behind a false
  access-restriction story.

## Evidence for each branch

- **`missing_frame`**: `fixtures/missing_frame_response.xml` reproduces the
  real, live HTTP 404 body shape observed in DX-53 for a syntactically
  valid but non-existent frame ID
  (`getxml?ECOLI:NOT-A-REAL-FRAME-DX53` -> *"The object
  NOT-A-REAL-FRAME-DX53 was not found in database ECOLI."*).
- **`gated`**: `fixtures/synthetic_gated_response.html` is a small,
  **synthetic** fixture (never captured from any live vendor response),
  authored fresh to exercise this branch -- because EcoCyc itself, being a
  BioCyc "Open Database", never actually returned a gated response during
  DX-53's live traversal. It stands in for the class of response DX-30
  documented for a Tier-2/3 organism-specific PGDB (MED4) object page: an
  HTTP 404 with an hCaptcha-bearing CSP header. It contains no MED4 or
  other organism-specific content.
- **`ok`**: any of the reaction/protein/gene/pathway fixture XML files
  (all real, retained HTTP 200 `getxml` responses, trimmed for size).

`tests/test_response_classifier.py` and `tests/test_traversal.py` both
assert these three states are pairwise distinguishable and that a
`missing_frame`/`gated` outcome on a fetched frame stops only that one
traversal branch (see `traversal-strategy.md`), never the whole run.
