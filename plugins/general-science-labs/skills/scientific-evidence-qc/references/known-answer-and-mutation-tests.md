# Known-answer and mutation tests are required, not optional

## Why a requirement audit is not enough

A requirement audit asks: "does every bullet in the spec have a
corresponding check function?" That is a real and useful question, but it
only proves *coverage of stated requirements* — it cannot prove any
individual check computes the *correct* answer. A check can exist, run
cleanly, and pass 100% of records while silently encoding the wrong
classification logic. Requirement audits cannot catch that class of bug by
construction: they never independently re-derive what the correct answer
should have been.

A **known-answer test** closes this gap: it exercises a check against data
whose correct outcome you can verify independently of the check itself (from
the raw source, a documented reference set, or a hand-computed expectation),
and asserts the check produces that outcome. A **mutation test** goes one
step further: it reintroduces the *specific* wrong outcome a plausible bug
would produce and asserts the check now fails — proving the check is
actually sensitive to the defect it exists to catch, not just structurally
present.

**Required for every check that encodes a domain classification (not just
"does this parse"):** at least one known-answer assertion, plus at least one
mutation test per consequential failure mode.

## Canonical worked example: the DX-36 SABIO-RK taxonomy-lineage bug

This is the case study to reference (and re-verify) whenever this skill is
applied to a new check — it is a real historical bug in this repository's
own evidence pipeline, not a hypothetical.

**The bug (pre-fix):** the SABIO-RK ingestion adapter classified organism
specificity by testing exact string membership of the literal value
`"Cyanobacteria"` in each record's `ncbi_taxonomy_lineage`. Real SABIO-RK
lineage data uses `"Cyanobacteriota"` — the NCBI Taxonomy phylum name after
its 2021/2022 rename. The string comparison never matched, so every
genuinely cyanobacterial record silently fell through to the broadest
any-organism fallback tier instead of the narrower lineage-matched tier.

**How the bug hid from its own QC pass:** the QC invariant written against
this (pre-fix) corpus asserted, as a blanket rule, that *all* records from
this source must resolve at the broadest fallback tier and that none may
claim the project's specific target organism. Every requirement-audit
checklist item was satisfied — schema conformance passed, the stated
invariant had a corresponding check function, the check ran and passed. But
the invariant itself had baked in the bug's *wrong output* as the *expected*
output: once the lineage-matching bug was fixed and records began correctly
resolving at the narrower tier, this same "blanket all-fallback" check would
have started **failing the correct, fixed behavior** — actively rewarding
the regression and penalizing the fix. A pure requirement audit ("is there a
check for organism specificity? yes.") would never have surfaced this,
because the audit only verifies a check exists, not that its asserted
expected outcome is right.

**The fix, and the replacement checks:**

1. **Tier/organism consistency** (general, source-agnostic invariant): a
   record's *claimed* resolution tier must be consistent with its *own*
   recorded organism — a record claiming the narrowest exact-match tier must
   actually name the exact target organism, and a record naming the exact
   target organism must never appear at a broader fallback tier. This
   captures the real intent behind the original rule (catch mis-tagging)
   without penalizing legitimate narrower-tier results once the underlying
   bug is fixed.
2. **Known-answer check** (this part is project-specific and belongs in a
   fixture, not shared logic — see below): a documented, independently
   verifiable set of organisms that are known to belong to the target
   lineage must be present in the corpus and retained at the
   lineage-matched tier, never silently demoted to the any-organism
   fallback tier.
3. **Mutation test:** re-label one of the known-lineage records back to the
   any-organism fallback tier (the exact wrong outcome the original bug
   produced) and assert the known-answer check now fails. This is the test
   that proves the new check would have caught the original defect — not
   merely that it runs.

**Net effect:** the corpus's true tier distribution changed materially once
the string-match bug was replaced with taxid-based ancestry resolution (a
sizeable share of records moved from the broadest fallback tier to the
narrower lineage-matched tier), and the QC suite's pass count changed
(the blanket invariant and its associated test were replaced, and the full
suite was re-run and confirmed green against the corrected corpus and
corrected methods documentation). The precise before/after record counts and
tier numbers are project-specific evidence, not general rules — see
`fixtures/project-example/` for how to reference a corpus's actual numbers
without asserting them as durable facts about any organism's biology.

## Where project-specific assertions belong

The organism names, taxonomic IDs, gap-fill identifiers, and expected tier
distributions from any one project's corpus (the DX-36 example above
included) are **not** general invariants. They must live in `fixtures/`,
clearly labeled as belonging to one project's dataset at one point in time,
and referenced by known-answer tests as "this project's fixture asserts X,"
never folded into the shared check function as if they were universal
biochemical facts. A new project's valid, correctly-classified exact-organism
evidence must never fail a shared check merely because an earlier project's
corpus only ever contained broad-fallback records.

Practically: the shared check function takes a *configurable* known-answer
set (or reads it from a fixture file) as a parameter — it does not hardcode
one project's organisms, taxids, or identifiers inside the general QC
script.

## Checklist when adding a new consequential check

1. Does this check assert "the correct classification of X is Y" (not just
   "X parses" or "X is present")? If yes, known-answer + mutation tests are
   required, not optional.
2. Is the known-answer data independently verifiable (from raw source data,
   a documented external reference, or a hand-computed expectation) rather
   than re-derived from the same code path being tested?
3. Does the mutation test reproduce the *specific* wrong outcome a plausible
   real bug would produce (not an arbitrary unrelated corruption)?
4. Is every project-specific fact (organism names, taxids, gap-fill IDs,
   expected distributions) parameterized from a fixture, not hardcoded into
   the shared check?
5. Would this check have caught the DX-36-shaped bug class (a classification
   check whose "expected" value was quietly copied from a buggy
   implementation's actual output)?
