# How the reconciler works

The hardest part of the codebase, written down for the version of me that
comes back to it cold. ADR 0007 is the decision record; this is the mental
model.

## The one-sentence version

The reconciler is a **pure projection from raw records to entities**. It does
not accumulate state, edit rows in place, or remember what it did last time —
it reads whatever raw records are on disk right now and computes what the
database should look like as a result.

That single property is where nearly everything else follows from.

## Why projection rather than mutation

The obvious design is: fetch a record, update the matching company row, move
on. It fails on the second source.

Say Wikidata says BMW was founded in 1916 and some other source says 1917.
With in-place mutation, whichever pass ran last wins, the loser is gone, and
nothing records that there was ever a disagreement. Re-running does not help
because the losing value no longer exists to reconsider.

So instead, every source's claim is stored as its own row in
`field_provenance`:

| company | field | observed_value | source |
| --- | --- | --- | --- |
| BMW | founded_year | 1916 | Wikidata |
| BMW | founded_year | 1917 | Some Other Source |

Both survive. `companies.founded_year` then holds the **projected winner** —
a derived value, computed from the assertions by the resolution ladder
(tier → field affinity → recency → flag for review).

The payoff: improving the matcher means re-running over records already on
disk. No re-scraping, no dependence on Wikidata looking the same as it did
last month. `RECONCILER_VERSION` is stamped on every processed record, so
"which rows were computed by logic older than X" is a query.

## What a "pass" is

A pass is one source's records being projected onto one level of the
hierarchy. There are six, and they run in order because each depends on
identity the previous one established:

| Pass | Entry point | Produces |
| --- | --- | --- |
| companies | `engine.run_companies_pass` | `companies` from Wikidata |
| vPIC match | `matching.run_vpic_match_pass` | vPIC make ↔ company identity |
| vPIC models | `vpic_models_pass.run_vpic_models_pass` | `models` under matched makes |
| Wikidata models | `wikidata_models_pass.run_wikidata_models_pass` | lines, generations, enrichment |
| vPIC years | `vpic_years_pass.run_vpic_years_pass` | `catalogue_periods` |
| EPA attach | `epa_attach_pass.run_epa_attach_pass` | `configurations` + specs |

The first is source-agnostic: `engine.py` holds the machinery and a per-source
mapper in `reconcile/sources/` translates payloads into the `types.py`
contract. Adding a source at company level should be a new mapper plus policy
entries, not an engine change.

The later passes are their own modules because they each answer a different
structural question, not just a different payload shape.

## The identity ladder

Every pass answers "is this source record something we already hold?" by
climbing the same shape of ladder, cheapest and most certain first:

1. **Existing external id** — we have matched this record before. Refresh it.
2. **Curated registry** — a recorded human judgment in `policy.py`. This is
   how a reviewer's decision becomes permanent and re-appliable.
3. **Unique exact-normalized name match** — mechanical, high precision.
4. **Flag for review** with candidates attached.

Note what is *not* on the ladder: fuzzy auto-accept. Trigram similarity
generates candidates for a human at step 4 and never decides anything itself.
Under-matching is cheap — add a registry entry, re-run, the match appears.
Over-matching means unwinding entity rows that other data already references.

## Assertions, projection, and going quiet

Three distinct operations, easy to conflate:

**Assert.** The source says something. Write a `field_provenance` row. If this
source already has a live assertion for that field with a *different* value,
supersede it (see [schema-traps.md](schema-traps.md) for the three-step dance
that requires). Same value → do nothing at all, which is what makes re-runs
free.

**Project.** Compute the winner per field across all live assertions and write
it to the entity column. With one source this is trivially its single live
assertion; the full ladder becomes exercisable when a second source asserts on
the same field.

**Tombstone.** The source used to assert a field and now does not. That is
information — it is a retraction, dated — so it is recorded as a supersession
with a NULL observed value rather than by deleting the old row. The projected
column then goes NULL too.

One carve-out: `name` never projects NULL. The column is NOT NULL, and
identity keeps its last known name until some source asserts a new one.

## Determinism

Re-running must produce byte-identical results, or none of the above is
trustworthy. Three rules buy that:

1. **The reconciliation unit is the *current* record** per
   `(source, external_id)` — greatest `last_seen_at`, ties broken by greatest
   id. Raw records accumulate history; the pass only ever looks at the newest.
2. **Processing order is ascending external id**, numerically where the id is
   a QID (so Q9 before Q10 before Q100, not the lexical order). This doubles as
   the slug-collision tiebreak, so which of two colliding names keeps the clean
   slug does not depend on iteration order.
3. **Raw records are never modified.** Only `last_seen_at` is bumped, by the
   landing upsert.

The practical test: run any pass twice. The second run should report zero
assertions, zero creations, zero flags. If it does not, something is
non-deterministic and that is a bug, not noise.

## Admission: three verdicts, not two

Records are not simply accepted or rejected. `policy.classify` returns:

- **admit** — affirmative evidence this is a car company. Create/update.
- **deny** — affirmative evidence it is not (a dealership, a factory, a
  person). It waits in raw, unflagged, forever. No question is being asked.
- **quarantine** — insufficient evidence either way. It waits in raw *with an
  `admission_review` flag*, which is a question for a human.

Quarantine is the important one, and the polarity is deliberate: anything not
affirmatively a car company quarantines rather than admitting. The reverse
polarity was tried and let in 2,175 seatbelt suppliers and glass-repair
chains (see [reconciler-incidents.md](reconciler-incidents.md)).

Quarantine is not permanent. A quarantined entity that a *different* source
later matches is admitted on that corroboration — appearing in the US VIN
system is affirmative evidence from the source that defines it — and its flag
closes as `corroborated_by_vpic`. The queue is meant to shrink from evidence
first and human review second.

## The two bookkeeping tables

**`reconciliation_flags`** is the review queue: the open questions. Two shapes,
which the schema enforces:

- *record-scoped* (`admission_review`, `match_review`) — there may be no entity
  to attach to yet, so the flag hangs off the raw record.
- *entity-scoped* (`multi_value`, `implausible_value`) — the entity exists and
  something about it is suspect.

Two rules that took a bug each to learn. An open flag keys on the *external*
id, not the raw record id, so a changed payload does not re-ask a question
already open. And an open flag is the **current** question — its whole detail
refreshes whenever the computation changes, because a stale flag tells a
reviewer the wrong thing. Only closed flags are immutable history.

**`match_decisions`** is the labeled set: one row per attempted record,
recording which rung decided, by what method, with what outcome. Every flag
close also records *why*.

This exists because the charter gates Tier 2/3 sources on "matcher precision
measured on a labeled set", and that was uncomputable while auto-matches
recorded no method. The data is now being captured on every run. **Nothing
consumes it yet** — building the evaluation over it is the obvious next piece
of work.

## Where to start reading

`engine.py`, top to bottom. It is the smallest complete example: identity
ladder, assertions, projection, flags, and the pass loop, with no
level-specific complications. Everything else is that shape with a harder
structural question attached.
