# Route-status vocabulary

Use exactly one of these values per route per access claim. Do not invent
synonyms ("broken", "down", "blocked") — they hide which route was tested and
whether the test actually happened.

| Status | Meaning | Evidence required |
|---|---|---|
| `reachable_public` | Route returns real, expected-shape data with no authentication, no license acceptance, and no account. | Live response with correct `Content-Type` and body shape (not an HTML app shell / login page). |
| `reachable_documented_auth` | Route works once a documented, non-secret step is completed (e.g. accepting a click-through license, using a public API key that anyone can self-register for). | The documented step + a successful post-step response. |
| `gated_credentialed` | Route requires a registered account/credential (email + password/API key/token) per call. | The documented contract (WSDL/OpenAPI/docs) showing the credential requirement, and confirmation no credential is available/authorized in this environment. |
| `gated_licensed_bulk` | Data is available as a bulk export/release under a specific license that must be accepted (e.g. CC-BY, click-through EULA) but does not require a personal account. | The license text/acceptance mechanism + a successful download using it. |
| `gated_paid_subscription` | Route requires a paid subscription/procurement decision, not just a login. | The publisher's own subscription/pricing terms page or equivalent official statement. |
| `dead_superseded` | The previously-documented route no longer works, AND an official successor route has been found and confirmed working. | Evidence of the dead route's current failure *and* evidence of the working successor (both dated). |
| `dead_unconfirmed` | The previously-documented route no longer works, and no successor has been found yet despite checking official docs/contracts. | Evidence of current failure + a note on what documentation was checked and did not reveal a successor. |
| `blocked_bot_challenge` | Route returns a bot/CAPTCHA/WAF challenge (e.g. Cloudflare "Just a moment…") rather than real content or a clean auth prompt. | The challenge response body/headers. |
| `unknown_untested` | Route has not actually been probed this session; any status claim is inherited from an older record. | Must cite the prior claim's date; must not be treated as equivalent to a fresh probe. |

## Rules for use

- Never assign `dead_superseded` or `dead_unconfirmed` without first checking
  official documentation/contracts (OpenAPI, WSDL, changelog, developer
  portal, or — as a last resort — the site's own client-side JS bundle) for a
  successor route.
- A `404` from a **guessed** identifier must not be recorded as
  `gated_paid_subscription` or any other access-denial status — record it as
  inconclusive and retry with a *known-valid* identifier before drawing an
  access conclusion.
- `unknown_untested` is the correct default. Do not silently promote it to
  a stronger status because an older document asserted one.
