# Rhea

**Last verified:** 2026-09-22
**Evidence:** `docs/database-evidence/source-priority-matrix.md`,
`docs/database-evidence/raw/rhea_expasy_ec_iubmb_bulk_mirror.tsv`

## Routes

| Route | Status | Detail |
|---|---|---|
| Interactive website (`rhea-db.org`) | `blocked_bot_challenge` | Returns a Cloudflare bot-challenge ("Just a moment…") even with a normal browser user agent. |
| SIB Expasy FTP bulk mirror (TSV) | `reachable_public` | Confirmed 200 OK; all test EC numbers present in the bulk file. No auth, no rate limit observed. **This is the recommended route** — do not attempt to scrape the main site. |
| EBI/`rheadb` Python mirrors | Not directly probed this pass; documented as an alternative to the Expasy FTP route. | — |

## Key facts worth preserving

- License: free, CC-BY 4.0. Dated releases; version recorded in the file
  header — use it for pinning.
- Rhea is a reaction dictionary (mass-balanced stoichiometry, EC/ChEBI
  cross-refs) by design, not a kinetics source — do not expect condition
  metadata.

## Do not generalize

- A bot-challenge on the interactive site does not mean the underlying data
  is unavailable — the official bulk mirror is a fully separate, working
  route with no such gate.
