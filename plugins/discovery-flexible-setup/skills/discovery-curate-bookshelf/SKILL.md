---
name: discovery-curate-bookshelf
description: |
  Cross-domain / Research data management — Intake and place a Discovery
  project's sources: ingest unstructured documents (PDFs, papers, proposals,
  slides) into Bookshelf shelves, stage documents when no shelf exists yet,
  import structured datasets (CSV, XLSX, Parquet, JSON) into data/, and
  track external databases, APIs, and large local files in
  data/external-sources.md. Warns and asks before copying anything over
  50 MB. USE FOR: 'ingest these papers', 'add this PDF to the bookshelf',
  'import this dataset', 'track this database', 'what data does this
  project have', 'ingest staged documents'. DO NOT USE FOR: querying shelf
  content (use bookshelf search), building a data-access tool or MCP server
  (use discovery-provision-tools), or full project setup (use
  discovery-project-setup).
metadata:
  version: "0.1.0"
  category: "Cross-domain"
  subfield: "Research data management"
---

# discovery-curate-bookshelf

## Modes
- **Proposal-only** (called by `discovery-project-setup`): return ops and stop.
- **Standalone**: gather, propose, approve, apply using
  `../discovery-project-setup/references/{gather-state,proposal-format,apply,git-ledger,retirement}.md`.
  Ask questions as plain chat.

## Classify every source

| Source | Placement | Op |
|---|---|---|
| PDF, DOCX, PPTX, MD, HTML, paper URL | Bookshelf shelf | `INGEST` (or `STAGE` if no shelf yet) |
| CSV, TSV, XLSX, Parquet, JSON, NetCDF, HDF5, 50 MB or less | `data/<name>` | `COPY` |
| Same, over 50 MB | Warn, then ask. If approved: `COPY` and suggest Git LFS. If declined: `TRACK` as `local-only` | `COPY` / `TRACK` |
| Database, API, data portal, object store, remote share | `data/external-sources.md` | `TRACK` (`access-pending`) |
| Code repo | Not data: hand off to `discovery-provision-tools` | — |

When it's ambiguous (for example, a 30 MB PDF of tables), state your choice and why
in the op.

## Staging (no shelf yet)
Copy to `.discovery/setup/staged/<filename>` and add it to `setup.json`:
```json
{"staged":[{"file":"staged/grant.pdf","sha256":"…","targetShelf":"S1","status":"staged"}]}
```
Emit `STAGE` now and `INGEST` with `depends: <shelf CREATE op>`. Every run
checks for leftover `staged` entries and proposes ingesting them.

## Shelves
- Default: one shelf named after the project slug. Propose more only for
  clearly separate corpora (for example "literature" vs "project-docs").
- Before `CREATE`, check `bookshelf list` and reuse a matching shelf.
- Before `INGEST`, skip any file whose sha256 is already in the shelf's
  `ingest-state.json`. Report it as `KEEP (already ingested)`.
- Ingestion runs in the background. Verification means the queued or complete
  status, not finished indexing.

## data/README.md (manifest)
```markdown
| File | Source | Imported | Size | sha256 | License | Sensitivity | Notes |
```
Sensitivity values: `public` | `internal` | `PII` | `controlled`. Ask whenever
`PII` or `controlled` is plausible. Never copy controlled data without
explicit approval.

## data/external-sources.md
One section per source:
```markdown
### ERCOT historian
- Kind: database (PostgreSQL) | API | portal | local-only path
- Location: <host / URL / absolute path>   (no credentials)
- Auth: env `ERCOT_PG_DSN` | Key Vault `kv-x/ercot` | none
- Owner / contact:
- Needed by: <task refs>
- Access: pending | tool:<mcp-server-name> | manual
- Notes: local-only entries exist only on this machine
```
`Access: pending` sources feed `discovery-provision-tools` and label their
tasks `access-pending`.
