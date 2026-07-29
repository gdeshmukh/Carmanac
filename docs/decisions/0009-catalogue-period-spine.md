# ADR 0009 — The catalogue-period spine: model years, production periods, phases

- Status: Proposed
- Date: 2026-07-29
- Depends on: ADR 0002 (provenance attaches to facts), ADR 0007 (reconciler
  contract)
- Resolves: foundation review F8 (first half — the model-year spine;
  the rating-standards half gets its own ADR before spec-level ingestion)

## Context

The five-level hierarchy makes `model_years` mandatory: every configuration
foreign-keys a `model_years` row, which carries a single `year`. That is a US
catalogue convention. US sources are natively per-model-year — vPIC's year
endpoints and EPA's fuel-economy tables both key on (make, model, model
year) — so for the US market the spine matches the sources exactly.

Most of the rest of the world does not sell cars that way (F8). European and
JDM records typically assert a **production period** — "built 1998–2005,
specs unchanged" — and the finest granularity enthusiasts actually use for
JDM cars is the **facelift phase** (zenki/chuki/kouki), not the year. A
Peugeot 306 Phase 2 or an S14 kouki has no natural model-year rows to attach
to.

Under the current schema the only way to land those configurations is to
fabricate one `model_years` row per calendar year of the period. That is
invention, not ingestion: no source asserted "a 2001 catalogue entry
exists", so the row would trace to a raw record that says nothing of the
kind — exactly the provenance violation ADR 0002/0003 exist to prevent. The
foundation review flagged this (F8) and configuration-level ingestion is
blocked on the decision.

## Decision

### 1. The spine generalizes from years to catalogue periods

The level between `generations` and `configurations` becomes a **catalogue
period**: a row asserting "this generation was catalogued/produced in this
form over [start_year, end_year]", with a `kind` naming which convention the
period follows:

- `model_year` — US-style single year; `start_year = end_year`. The
  dominant kind by row count once vPIC/EPA land.
- `production_period` — a source-asserted manufacturing/catalogue range
  (the Euro/JDM "built 1998–2005" record).
- `phase` — a facelift phase within a generation (zenki/kouki, Phase 1/2),
  when a source distinguishes it.

`start_year` is NOT NULL; `end_year` NULL means "still in production" (the
ongoing-period case; a NULL year would otherwise never collide, so
uniqueness below is NULLS NOT DISTINCT — the R3/R4 lesson). Kinds live in a
seeded lookup table (`period_kinds`), per the schema's closed-set rule (R8).

**The five-level invariant is preserved, amended in vocabulary**: every
configuration still keys to exactly one spine row under its generation —
the level is mandatory; what changes is that the spine stops lying about
markets that do not have model years.

### 2. Mixed granularity is normal, not a conflict

One generation may carry both kinds simultaneously: an E46 has US
`model_year` rows 1999–2005 (from vPIC/EPA) *and* a European
`production_period` 1998–2005 (from Tier 2/3 sources). US-market
configurations attach to year rows; Euro-market configurations attach to the
period row. The reconciler never converts one kind into another, and a
query like "2003 330i" resolves by containment
(`start_year <= 2003 AND 2003 <= coalesce(end_year, current)`).

Uniqueness is `(generation_id, period_kind_id, start_year, end_year)`
NULLS NOT DISTINCT. **Overlapping same-kind periods are a reconciliation
flag, never a constraint** — two sources bracketing a production run
differently is a normal conflict for review, and an exclusion constraint
would block the second source's assertion from landing at all.

### 3. Fabrication is rejected

No pass may mint per-year rows from a period (or vice versa). A spine row
exists only when a current raw record asserts that period shape. This keeps
every row explainable: the row is there because a specific source said so,
and `field_provenance` / the fact tables trace it.

### 4. Naming

The table renames `model_years` → `catalogue_periods`, and
`configurations.model_year_id` → `catalogue_period_id`. A table named
`model_years` holding 1998–2005 ranges misleads every future reader, and
this is the cheap moment: the table holds one seed row, no pages exist, and
the project has paid for exactly this kind of rename twice before (R9,
`variants` → `configurations`) on the grounds that load-bearing names get
more expensive to fix every week. The URL structure is unaffected (no route
exposes the level). If review prefers continuity over honesty here, the
fallback is keeping the name and documenting the semantics — the schema
shape is identical either way.

## Options considered

- **A. Fabricate per-year rows for period records.** Keeps the spine
  uniform and every query year-shaped. Rejected: invents unasserted facts
  (provenance violation), multiplies rows for markets with the least data,
  and misrepresents JDM phase reality (a kouki is not a set of years — the
  1996 and 1997 kouki S14s are one catalogue entry).
- **B. Generalize the spine to periods (chosen).** Stores what sources
  assert, preserves the mandatory level, costs mixed granularity in one
  table (handled by `kind` + containment queries).
- **C. Make the spine optional** (configurations attach directly to
  generations when no year is known). Rejected: breaks the invariant for
  real (two shapes of configuration key), and "no year known" is actually
  "period known" almost always — C throws away information B keeps.

## Consequences

- Migration (hand-reviewed, per convention): rename table + FK column, add
  `start_year`/`end_year`/`period_kind_id`, backfill the seed row as
  `model_year` (2002), seed `period_kinds`, replace the `(generation_id,
  year)` unique with the four-column NULLS NOT DISTINCT unique, re-point the
  `updated_at` trigger (the rename blind spot from ADR 0006's migration).
- vPIC/EPA ingestion is unblocked and unchanged in shape: US rows land as
  `model_year` periods.
- The reconciler gains one flag condition (same-kind overlap within a
  generation) when configuration-level passes arrive.
- Frontend year-picker UX must handle period rows (display "1998–2005" or
  the phase name); comparison queries use containment. No route changes.
