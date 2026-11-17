# Bounded reaction -> enzyme/complex -> gene -> pathway traversal strategy

`scripts/traversal.py::traverse_reaction(client, orgid, reaction_frameid,
entry_id, expected_ec=None, expected_taxid=None, pathway_detail_limit=1)`
generalizes the traversal validated twice against real BioCyc-family PGDBs:
DX-30 (MED4/CyanoCyc, 3 Calvin-Benson-cycle reactions) and DX-53 (ECOLI/
EcoCyc, 3 central-metabolism reactions: pgi/pfk/zwf). Every organism, orgid,
frame ID, EC number, and taxid is a caller-supplied parameter.

## The four hops, each bounded to exactly the frames actually discovered

1. **Reaction** — one `getxml?<orgid>:<reaction_frameid>` call. If this
   fails (`missing_frame`/`gated`/`failure`), the traversal stops there and
   reports that outcome explicitly -- it never guesses at downstream
   structure from a failed root fetch.
2. **Enzyme(s)/complex(es)** — every `<enzymatic-reaction>
   <Enzymatic-Reaction><enzyme><Protein resource=.../></Protein></enzyme>
   </Enzymatic-Reaction>` in the reaction frame is walked. **This is the
   key generalization beyond either source ingestion pass**: a reaction can
   be catalyzed by more than one distinct enzyme (EcoCyc's `PGLUCISOM-RXN`
   has two: the canonical Pgi homodimer and a cross-reactive KduI
   homodimer performing a documented promiscuous side activity). Every one
   is fetched and returned as its own entry -- never collapsed to "the"
   enzyme for that EC number.
3. **Gene(s)** — for each enzyme `Protein` frame fetched in step 2:
   - If it has a direct `<gene><Gene resource=.../></gene>` child, it is a
     **monomer**: fetch that one gene frame.
   - If it instead has one or more `<component><Protein resource=.../>
     </component>` children, it is a **multi-subunit complex**: fetch each
     *distinct* component subunit's own frame, then that subunit's own
     `<gene>` link. A homodimer/homotetramer (one subunit type, e.g.
     `CPLX0-7877` -> `PGLUCISOM` x2) still yields exactly one
     `GeneEnzymeLink` (role `"complex"`); a genuine heteromultimer (more
     than one distinct subunit type) yields one `GeneEnzymeLink` per
     distinct subunit (role `"complex_subunit"`), each with its own gene
     identity. This is a real generalization over DX-53's build script,
     which hardcoded a single gene file per complex because both validated
     complexes happened to be homomeric.
   - Every gene frame yields `gene_symbol` (from `common-name`),
     `legacy_locus_tag` (from `accession-1`), and `genome_coordinates`
     (`left_end`/`right_end`/`strand`).
4. **Pathway(s)** — every `<in-pathway><Pathway resource=.../></Pathway>`
   frame ID is recorded, namespace-qualified, but only the first
   `pathway_detail_limit` (default 1) are independently fetched for a
   `common_name` -- the rest are still present in the output, just without
   a fetched detail, and are explicitly noted as bounded rather than
   silently dropped. Raise `pathway_detail_limit` if you need more than one
   pathway's detail, understanding that this issues one additional
   rate-limited request per extra pathway.

## Organism identity: cross-reference, never orgid-string assumption

`traverse_reaction` reads the `NCBI-TAXONOMY-DB` `dblink` inside
`<metadata><PGDB orgid='...'>` on the reaction frame's own response and
compares it to a caller-supplied `expected_taxid`, reporting the result as
an explicit `organism_verified` boolean (`None` if no `expected_taxid` was
given -- never silently assumed `True`). This is the same verification
method DX-53 used to confirm `ECOLI` really is taxid `511145`, generalized
so any orgid/taxid pair can be checked the same way -- never trust that an
orgid string like `"ECOLI"` or `"MED4"` by itself proves organism identity.

## EC-number verification: reported, not enforced

If `expected_ec` is given, the reaction frame's own `<ec-number>` value is
compared and reported as `ec_match` (`True`/`False`/`None`). A mismatch is
never raised as an exception -- it is surfaced to the caller so a batch of
many reactions can still complete and report which ones didn't match,
rather than crashing the whole run on the first surprise.

## What is bounded, explicitly

- No step issues more than one request per **discovered** frame ID (a
  frame already fetched once in a given `client` instance is not
  re-fetched by the traversal itself -- add your own memoization/caching
  in a long-lived `client` if you want cross-call reuse).
- No bulk/systematic multi-object endpoint is ever called.
- A `missing_frame`/`gated`/`failure` outcome on any one branch (an
  enzyme, a subunit, a gene, a pathway) is recorded on that branch alone
  and does not abort the rest of the traversal for that reaction, nor the
  rest of a caller's batch across reactions.
