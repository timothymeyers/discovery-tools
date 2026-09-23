# Taxon-scoped UniProt accession lookup

## Query pattern

Use the UniProt REST search API to scope accessions to a single organism
before doing any AlphaFold work:

```
GET https://rest.uniprot.org/uniprotkb/search
    ?query=taxonomy_id:<NCBI_TAXON_ID>
    &fields=accession,reviewed,protein_name,gene_names,length,cc_subunit
    &format=tsv
    &size=500
```

Notes:

- `taxonomy_id:<ID>` (or `organism_id:<ID>`) restricts results to exactly one
  NCBI taxon. Do not substitute a free-text organism-name query
  (`organism_name:"..."`) as the sole filter — free-text names can match
  strain/synonym variants inconsistently; prefer the numeric taxon ID and
  treat a name-based query as a secondary sanity check only.
- The API paginates via the `Link` response header (`rel="next"`); follow it
  until exhausted rather than assuming a single page covers the full set.
- Combine `taxonomy_id` with `reviewed:true` if you only want Swiss-Prot
  (manually reviewed) entries; note the reviewed/unreviewed split explicitly
  in your output rather than silently dropping unreviewed accessions.
- Request `cc_subunit` (subunit structure annotation) alongside identity
  fields when the downstream step needs an oligomer/complex flag (see
  `oligomer_complex_flags.md`) — this avoids a second round-trip later.

## Provenance fields to record

For every taxon-scoped lookup, record alongside the resulting accession list:

| Field | Why |
|---|---|
| Taxon ID / query string used | Reproducibility — confirms exactly which organism scope was queried |
| Retrieval date/time | UniProt entries and annotations change over time |
| UniProt release version (if available from response headers) | Ties results to a specific UniProt snapshot |
| Reviewed vs. unreviewed count | Signals annotation quality/completeness of the scoped set |
| Total accession count | Sanity-check figure for downstream row-count reconciliation |

## Common pitfall

Never assume an accession list gathered for one project/taxon is reusable
as-is for a different organism or a different task's candidate list — always
re-run the taxon-scoped query for the actual organism in scope, and record
the taxon ID used so a reviewer can verify the scope was correct.
