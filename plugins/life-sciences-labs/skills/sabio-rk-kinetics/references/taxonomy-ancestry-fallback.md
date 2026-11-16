# Taxonomy Ancestry Fallback

## The historical bug this generalizes

A prior ingestion pass's tier-3 lineage post-filter tested exact string
membership of one hardcoded higher-taxon name (e.g. a phylum's *retired*
NCBI name) against each entry's `ncbi_taxonomy_lineage` array. The source
database's lineage data, however, used NCBI Taxonomy's *current* name for
that phylum (NCBI periodically renames/reclassifies taxa) — so the literal
string the filter searched for never appeared in any entry's lineage array.
Every record silently fell through to the broadest "any organism" tier,
including genuinely matching organisms that were incorrectly excluded from
the more specific tier. A prior QC pass's "N/N passed" summary did not catch
this because no test exercised the real lineage string values — only
synthetic fixtures using the filter's own hardcoded string.

**General lesson:** any lineage/taxonomy filter that matches on a *name
string* is fragile to renaming, synonym drift, and inconsistent naming
across data sources. Taxid-based ancestry resolution against an
authoritative taxonomy service is far more robust, because a taxid is a
stable identifier that does not change when a name is renamed.

## The fix, generalized

`classify_higher_taxon_lineage(organism_taxid, ncbi_taxonomy_lineage,
ancestor_taxids, fallback_taxid, verified_synonyms=None)` in
`scripts/ncbi_taxonomy_client.py`:

1. **An empty/missing lineage array is always `unknown_empty_lineage`** —
   never a match by omission, regardless of taxid availability. Some
   entries genuinely have no lineage data; treating that as either "match"
   or silently skipping it would misrepresent real data as decided when it
   is not.
2. **Primary check: taxid ancestry.** `NcbiTaxonomyClient.resolve_ancestors`
   resolves each organism's own taxid to its ancestor-taxid chain via the
   NCBI Taxonomy E-utils `efetch` endpoint (batched, cached, injectable
   `http_get` for offline tests). A match is: the organism's own taxid *is*
   `fallback_taxid`, or `fallback_taxid` is among its resolved ancestors.
   `ancestor_taxids=None` means the lookup failed (transport/parse) — this
   is distinct from "resolved, genuinely no ancestors" (`[]`) and must never
   be treated as a match.
3. **Secondary check (only when taxid resolution is unavailable):**
   case-insensitive match against a caller-supplied `verified_synonyms` set.
   This set must be sourced from the target taxon's own current NCBI
   `OtherNames` record — including historical/retired names precisely
   because lineage-string drift across renamed taxa is the exact failure
   mode this fallback exists to catch. Never assume the current name alone
   is a sufficient synonym set.

## Why the primary/secondary split matters

If taxid resolution silently fell back to a *guessed* synonym list whenever
convenient, the same class of drift bug could recur (a differently-drifted
name not in the guessed list). Making taxid ancestry the primary path, and
requiring the secondary synonym set to be *sourced from the taxon's own
verified `OtherNames`* rather than assumed, keeps the fallback itself
auditable.

## Mutation-sensitive test

`tests/test_taxonomy_ancestry.py::test_mutation_old_literal_string_match_misses_current_name_lineage`
reproduces the exact shape of the historical bug (a single hardcoded literal
name check) against a lineage array using the *current* name for the same
taxon, proves the old-style check fails to match, and then proves
`classify_higher_taxon_lineage`'s taxid-ancestry path correctly matches the
same organism. A companion test proves the secondary synonym path also
recovers the match when a *verified* (not guessed) synonym set includes the
older name.
