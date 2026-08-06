# ADR 0009 — What the 4th level holds: model years, production periods, phases

- Status: Accepted (2026-07-29, with review notes below). **Revised by
  ADR 0014 (2026-07-31)**: periods re-parent from generations to models
  (pure time under the model — what the year sources assert), and
  generation placement becomes an evidence-gated nullable fact on
  `configurations`. The period kinds, natural-key, no-fabrication, and
  mixed-granularity rules here all stand unchanged.
- Date: 2026-07-29 (rewritten same day for clarity after first review)

Review notes (2026-07-29): (1) aggregation above the 4th level is
untouched — the generation page stays the one shared page (the E46 M3 page
lists all US model years AND Euro periods beneath it); granularity is
additive below, never a replacement for the shared views. (2) Chassis-code
families like the AMG GT's C190 (coupé) / R190 (roadster) are one
generation carrying several codes — `generations.chassis_codes` (an array,
GIN-indexed per review R5) already models this; body-variant splits live on
configurations via `body_styles`.
- Depends on: ADR 0002 (provenance attaches to facts), ADR 0007 (reconciler
  contract)
- Resolves: foundation review F8 (first half — the model-year question;
  the rating-standards half gets its own ADR before spec-level ingestion)

## What does NOT change

Three things this ADR deliberately keeps, stated first because the rest is
easier to read knowing they are safe:

1. **The five-level hierarchy stays exactly as designed.** Company → model →
   generation → *4th level* → configuration. Every configuration still
   belongs to exactly one 4th-level row, which belongs to a generation. The
   level is mandatory, as it always was. This ADR only changes what a
   4th-level row is allowed to SAY.
2. **US cars keep one page per model year.** A 2019 Mercedes-AMG GT S and a
   2020 Mercedes-AMG GT S remain two configurations with two slugs and two
   pages — even if nothing changed between them. That is correct and stays.
3. **One slug per configuration, one page per configuration.** Unchanged.

## The problem

The 4th level (`model_years`) currently requires a single calendar year on
every row. That works perfectly for the US and breaks everywhere else, for
one reason: **the "model year" is a US regulatory and marketing system, not
a fact about cars in general.**

In the US, every car sold is legally stamped with a model year (it is
encoded in the VIN, EPA re-certifies fuel economy per model year, dealers
sell "the 2019s"). So for US-market cars, our Tier 1 sources hand us
year-labeled data: vPIC and EPA literally key their records on
(make, model, model year). The 2019 AMG GT S exists as a distinct thing in
the source data, which is exactly why it deserves — and gets — its own page.

Most other markets never adopted that system. A BMW 330i sold in Germany in
2003 was not a "model year 2003" car — there is no such label anywhere in
German registration, marketing, or enthusiast usage. It was simply an E46
330i built in March 2003. Changes were not batched into yearly editions;
they arrived mid-production ("from 09/2001, the facelift headlights"). What
European and Japanese sources assert instead is:

- a **production period**: "330i (E46), built 1998–2005" — brochures, type
  approvals, enthusiast wikis all speak this way;
- or a **phase**: Japanese sources split a generation into zenki/chuki/kouki
  (early/middle/late), and French/Italian sources into Phase 1 / Phase 2.
  An S14 Silvia kouki is one catalogue entry covering 1996–1998, and no
  Japanese source will ever tell us anything about "the 1997 Silvia"
  specifically.

So the honest situation is: **US sources assert years; most other sources
assert periods or phases.** Today's schema can only store years.

To land a Euro/JDM configuration today we would have to fabricate rows —
turn "built 1998–2005" into eight year-rows the source never asserted. That
collides with the provenance rule the whole schema is built on (every fact
traces to a raw record that actually said it; ADR 0002/0003): the fabricated
"2001" row traces to a record that says nothing about 2001. It also
multiplies pages that would all be word-for-word identical, with no source
ever able to fill in a difference between them — which is the opposite of
the US case, where the per-year pages are backed by per-year source records
(and where a real difference, a mid-cycle refresh or an emissions change,
can always show up).

## The decision

The 4th level becomes a **catalogue period**: a span of years plus a `kind`
saying which convention the row follows.

| kind | shape | example | who asserts it |
|---|---|---|---|
| `model_year` | start = end | 2019 | vPIC, EPA — every US-market record |
| `production_period` | a range; end open if still built | 1998–2005 | Euro/JDM type approvals, brochures, wikis |
| `phase` | a named sub-range of a generation | S14 kouki, 306 Phase 2 | JDM/Euro enthusiast + market sources |

Concretely, `model_years` gains `start_year` (NOT NULL), `end_year` (NULL =
still in production), and `period_kind_id` (seeded lookup, per the
closed-set rule R8). A US model year is stored as start = end = 2019 — the
current single-year rows are the special case of the general shape, so
nothing about US handling is redesigned.

**Walkthrough, US car (nothing changes):** vPIC/EPA assert the 2019
Mercedes-AMG GT S. We store: Mercedes-AMG → AMG GT → C190 → catalogue
period (`model_year`, 2019–2019) → configuration "GT S, US market". Its own
slug, its own page. The 2020 row lands the same way from the 2020 source
records.

**Walkthrough, Euro car (newly possible):** a German source asserts "330i,
E46 sedan, built 1998–2005". We store: BMW → 3 Series → E46 → catalogue
period (`production_period`, 1998–2005) → configuration "330i, DE market".
One entry, one slug, one page — which is also exactly how a German
enthusiast would look for it. Nobody searches "the 2002 German 330i";
they search "E46 330i pre-facelift", which is the `phase` kind when a
source distinguishes it.

**Both at once is normal.** The same E46 generation carries US `model_year`
rows 1999–2005 (from vPIC/EPA) *and* the European `production_period`
1998–2005 row (from Euro sources), side by side. US configurations hang off
the year rows; Euro configurations hang off the period row. The reconciler
never converts one kind into the other. A question like "show me 2003
330is" resolves by containment: every period row where
`start_year <= 2003 <= coalesce(end_year, now)` matches, so both the US
MY2003 configuration and the German period configuration appear.

**Uniqueness and conflicts.** Unique on `(generation_id, period_kind_id,
start_year, end_year)` NULLS NOT DISTINCT (an open-ended NULL end must
still collide — the R3/R4 lesson). Two sources bracketing the same
production run differently (1998–2005 vs 1999–2005) is a normal
reconciliation conflict: flag for review, never a constraint violation,
so the second source's assertion can always land.

**No fabrication, either direction.** No pass may mint year rows from a
period or a period row from years. Every 4th-level row exists because a
current raw record asserted that exact shape. This is the provenance rule
doing its job, nothing more.

### Naming

The table renames `model_years` → `catalogue_periods`, and
`configurations.model_year_id` → `catalogue_period_id`. A table named
`model_years` holding "1998–2005" rows misleads every future reader, and
this is the cheap moment: one seed row, no pages, and the project has paid
for exactly this rename twice (R9, ADR 0001) on the grounds that
load-bearing names get more expensive weekly. URLs are unaffected (no route
exposes this level). If review prefers the familiar name, the fallback is
keeping `model_years` and documenting the wider semantics — the shape is
identical either way; only honesty of the name differs.

## Options considered

- **A. Fabricate per-year rows for period records.** Uniform year-shaped
  spine, and superficially matches the US intuition that every car has
  years it was sold in. Rejected: the rows would assert facts no source
  ever stated (provenance violation), the resulting pages would be
  indistinguishable copies no source could ever differentiate, and it
  erases the distinctions other markets DO make (a kouki is one thing, not
  three years). Where per-year facts genuinely exist — the US — we already
  get per-year rows from the sources themselves.
- **B. Generalize the 4th level to periods (chosen).** Stores exactly what
  each market's sources assert; keeps the mandatory five-level structure;
  US flow unchanged. Cost: mixed granularity within a generation, handled
  by `kind` + containment queries.
- **C. Make the 4th level optional** (configurations attach straight to
  generations when no year is known). Rejected: this is the option that
  actually breaks the five-level hierarchy — two different shapes of
  configuration — and "no year known" is almost always "period known",
  which C throws away instead of storing.

## Consequences

- Migration (hand-written, reviewed): rename table + FK column, add
  `start_year`/`end_year`/`period_kind_id`, backfill the seed row
  (2002 → 2002–2002 `model_year`), seed `period_kinds`, swap the unique
  constraint, re-point the `updated_at` trigger (the rename blind spot from
  ADR 0006's migration).
- vPIC year-level and EPA ingestion unblock with no shape change: US rows
  land as `model_year` periods.
- The reconciler gains one flag condition (same-kind overlap within a
  generation) when configuration-level passes arrive.
- Frontend: year pickers must render period rows as ranges or phase names
  ("1998–2005", "kouki"); comparison and year-filter queries use
  containment. No route changes.
