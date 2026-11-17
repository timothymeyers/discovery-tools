# Query-Tier Strategy

## Why tiers instead of one query

A bare free-text species search against SABIO-RK is unreliable: the API's
`Organism` field only indexes species-level names, and organism-name strings
in biological databases are inconsistent (synonyms, strain suffixes,
outdated names). Building a *stable, reproducible* set of query tiers from
identifiers you already trust (EC number, target organism name, genus, and a
higher-taxon fallback) — and recording exactly which tier resolved each
result — keeps every returned observation traceable to how confidently it
matches your actual target organism.

## The four tiers

Given `ec_number`, `organism` (exact species name), `genus`, and
`fallback_taxon_name` (a higher taxon, e.g. a phylum), in order:

1. **`tier1_exact_organism`** — `ECNumber:<ec> AND Organism:"<organism>"`.
   Highest confidence: the query itself restricts to the exact organism
   name.
2. **`tier2_genus_wildcard`** — `ECNumber:<ec> AND Organism:<genus>*`. Widens
   to any species in the same genus.
3. **`tier3_lineage_fallback`** — `ECNumber:<ec>` (no organism clause at all
   — SABIO-RK's `Organism` field cannot filter on higher taxa). Every
   returned entry is then post-filtered client-side using taxid-ancestry
   classification (see `taxonomy-ancestry-fallback.md`) against
   `fallback_taxon_name`/`fallback_taxid`. This is a *deliberate, documented*
   broadening — never an accidental one.
4. **`tier4_any_organism`** — `ECNumber:<ec>`, no organism or lineage
   restriction at all. The broadest possible fallback. Always explicitly
   labeled as such in the result's `tier` field so it can never be mistaken
   for an organism-matched observation.

## Resolution order and provenance

`resolve_reaction_entries(...)` tries tiers 1 → 4 in order and stops at the
first with a nonzero result. Every tier attempted — including ones that
returned zero results or were skipped by reuse — is recorded in
`tiers_tried`, so a downstream consumer can always tell whether a result came
from the exact target organism or a controlled, documented fallback. Tier 4
reuses tier 3's raw (pre-lineage-filter) fetch instead of re-querying, since
both tiers issue the identical underlying EC-only query and differ only in
client-side post-filtering — this avoids a wasted duplicate API call/cache
entry.

## Worked example

For EC `1.1.1.1`, organism `"Bacillus subtilis"`, genus `Bacillus`, and
fallback taxon `Bacillota` (a Gram-positive bacterial phylum): if tiers 1–2
return zero (no *Bacillus subtilis*-specific or *Bacillus*-genus entries),
tier 3 queries all EC `1.1.1.1` entries and keeps only those whose taxid
ancestry includes Bacillota's taxid; if that is also empty, tier 4 keeps
every EC `1.1.1.1` entry regardless of organism, clearly tagged
`tier4_any_organism` in every emitted record.
