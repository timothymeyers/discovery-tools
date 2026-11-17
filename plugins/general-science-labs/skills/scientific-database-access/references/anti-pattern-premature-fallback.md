# Anti-pattern: pre-scripted fallback before access is tested

## What this is

The most expensive, recurring mistake in scientific-database access work is
**designing the failure branch before the access attempt**: writing (or
reusing) an `unavailable_source` / "this database is gone" code path or
conclusion *before* a live, dated probe of the specific route in question has
actually been made.

This has been observed in project history as a pattern of pre-scripted
"unavailable_source" fallbacks being planned ahead of, rather than in
response to, an actual access test (referenced internally as Mission Control
events in the 2826–2828 range). The specific event IDs are project-internal
and not reproduced here; the durable lesson is the pattern itself, which is
easy to reproduce in any project:

1. An earlier task or memory records that a source failed (e.g. "SABIO-RK's
   REST API is dead").
2. A later task inherits that conclusion and **plans its fallback logic
   around it being permanently true** — without re-probing.
3. When the source turns out to have a working *documented successor route*
   (as SABIO-RK did — see `references/sources/sabio-rk.md`), the fallback
   logic silently activates anyway, producing an `unavailable_source` record
   for data that was actually retrievable, or worse, causes a real adapter to
   never be attempted at all.

## Why it happens

- Treating "the last thing I read said X" as equivalent in authority to "I
  just tested this and got X" (see the "Memory chronology matters more than
  retrieval score" lesson: a high-scoring but stale memory citation is not
  the same as a fresh probe).
- Confusing *one route's* failure (e.g. legacy REST) with *the source's*
  failure (the documented Export API was fine).
- Reusing prior code/config that already contains a hardcoded
  "source is unavailable" branch, rather than writing the probe first and
  letting its real result decide which branch executes.

## How to avoid it

1. **Order of operations is not negotiable:** enumerate routes → probe the
   specific route → record the result → *only then* decide whether a
   fallback is needed. Never write step 4 before step 2 has produced
   evidence.
2. Treat any inherited "this source is unavailable" claim as a hypothesis
   with a date attached, not a fact. Re-probe before it gates a real
   decision — it is almost always cheaper to re-check than to build on a
   stale conclusion.
3. When a fallback *is* genuinely needed (real, current, tested failure),
   record it with the full access-matrix fields (route, date, evidence,
   scope, supersedes) so the *next* task doesn't repeat the same premature
   judgment in the other direction (i.e. assuming a route that failed once
   will always fail).
4. Distinguish "the source returned zero results for my specific query" from
   "the source is unreachable" — these require different downstream records
   (`unavailable_source` is for the latter only; an empty-but-successful
   query is a different, non-access, missingness case).

## Related lessons carried into this skill

- A passing test suite does not catch this defect class — it will happily
  pass while asserting a stale unavailability conclusion. Only a live
  re-probe (or a dated, checked contract read) resolves it.
- Superseding a claim is not the same as deleting it: keep the old,
  now-corrected finding visible with its own date, and point the new finding
  at it via `supersedes` (see `references/access-matrix-template.md`).
