# BioCyc / CyanoCyc

**Last verified:** 2026-09-22
**Evidence:** `docs/database-evidence/dx30-biocyc-network-context-methods.md`,
`docs/database-evidence/raw/biocyc_metacyc_rubisco_rxn.xml`,
`docs/database-evidence/raw/biocyc_org_id_guess_404_NOT_A_VALID_ORGID.html`,
`docs/database-evidence/raw/dx30/`

## Routes

| Route | Status | Detail |
|---|---|---|
| Public Tier-1 `getxml` (MetaCyc) | `reachable_public` | Confirmed 200 OK for public Tier-1 PGDB reactions with no auth. |
| Organism-specific `getxml` (e.g. `https://websvc.biocyc.org/getxml?MED4:<frameid>`) | `reachable_public` (individual-object lookups only) | Confirmed working with **no authentication** for 9 distinct MED4 object frames (reactions, complexes, monomers, genes, a pathway) — all HTTP 200, stamped `ptools-version`. This is narrower than full database access: it works for specific, known-valid frame IDs, not systematic traversal. |
| Interactive website (`biocyc.org/<ORGID>/organism-summary`, gene pages) | `gated_paid_subscription` (full browsing) / `blocked_bot_challenge` (some object pages) | Renders with explicit login/subscription banners for full content; a direct object page 404'd with an hCaptcha-bearing CSP header. |
| Bulk/systematic API download of a Tier-2/3 organism-specific PGDB (e.g. full CyanoCyc/MED4) | `gated_paid_subscription` | BioCyc's published subscription terms require a paid non-profit subscription (individual ~$400/yr up to lab-tier ~$5,000/yr) for full download/API rights. Not re-tested directly (no subscription available) — carried forward from prior documented terms, so treat as `unknown_untested` if a fresh confirmation is needed. |

## Key facts worth preserving

- A blind org-ID guess against `getxml` (e.g. guessing `Prochlorococcus`
  instead of the correct `MED4` orgid) 404s — that is a **wrong-identifier**
  result, not proof the endpoint requires a subscription. Confirm the correct
  orgid before drawing an access conclusion.
- Rate limit: 1 query/sec guideline (BioCyc Limited Use License) — was
  followed with an explicit `sleep 1` between the 14 live requests made.
- Anonymous `PTools-session`/`anonID` cookies are session tracking, not
  authentication.
- `ptools-version` is a *software* version stamp, not necessarily an
  independently verified PGDB dataset release — keep them conceptually
  distinct.

## Do not generalize

- Successful unauthenticated individual-object `getxml` responses are
  **not** proof of unrestricted systematic-download rights, and are not
  proof every PGDB (or every organism-specific BioCyc collection) is
  publicly reachable this way.
