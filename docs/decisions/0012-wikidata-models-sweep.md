# ADR 0012 — The Wikidata models sweep: the ladder, lines, and generations

- Status: Proposed
- Date: 2026-07-30
- Depends on: ADR 0007 (reconciler contract), ADR 0008 (match-pass
  precedent), ADR 0010 (the models pass), ADR 0011 (as-filed models;
  series are lines; external ids are 1:1 correspondence)

## Context

The as-filed models exist (1,735, vPIC), their year lists are landing, and
EPA's per-variant rows are landed. The missing middle is **generations** —
nothing asserts them yet, and every catalogue period needs one — plus the
**line relation** ADR 0011 §2 deferred here. Wikidata is the only landed
source that speaks at model level globally.

Surveyed live before designing (2026-07-30):

- **13,733** `car model` entities, **385** `automobile model series`,
  **489** `vehicle model` — the sweep's population.
- **83%** carry P176 (manufacturer), pointing at 1,130 distinct makers —
  the attach point to our companies.
- Structure is inconsistent: P179 series membership on only **7%**;
  follows/followed-by chains on **37%** (the richest generation signal);
  dates are sparse (inception on 6%, discontinued on 2%) — so Wikidata
  contributes generation *identity* while vPIC/EPA contribute the *time*.
- **Class does not encode level.** One 3-Series lineage holds a plain
  `car model` (the E21-shaped entity), a series-classed generation (the
  E36-shaped one), the series proper, and a classless stub — four entities
  labelled "BMW 3 Series". Meanwhile **"BMW 330i" does not exist**, and
  Mercedes' C-Class entity is series-classed yet corresponds one-to-one
  with our as-filed model.
- The platform property recorded in earlier notes (P4243) returns zero
  uses — the pid must be re-verified before the platforms ADR.

## Decision

### 1. One maximal sweep, bare QIDs, a sweep marker

Fetch the union of the three model-shaped classes, paginated, pulling per
entity: all P31 classes, label + aliases + description (en), P176, P179,
P361, P155/P156, every date property (P571, P576, P729, P730, P2669), and
the enwiki sitelink title (the pointer the Wikipedia-infobox wave will
need). Same canonicalization as the makes sweep (sorted GROUP_CONCAT).

Raw `external_id` stays the **bare QID** — QIDs are globally unique, so
the vPIC-style kind prefix solves a collision that cannot happen here.
Kind *selection* cannot rely on payload shape (the vPIC lesson) or on
classes (company records legitimately carry model classes — that is why
they DENY there), so the landing stamps a `sweep: models` marker in the
payload, the same fetch-metadata species as the vPIC `vehicle_types`
merge.

### 2. Level is decided per make, against the as-filed models

The pass resolves each entity through its P176 company (via the external-
id map), then descends:

1. **Existing external id** → refresh (re-runs).
2. **P176 unresolvable** (maker we don't hold, or absent) → the entity
   waits, unflagged — ADR 0010 §1's posture: models of unheld makers are
   one question, not thousands of copies. A P176 pointing at a *held but
   wrong-level* company (Mercedes-Benz Group vs the marque) surfaces as a
   candidates flag when the name rungs disagree with it.
3. **Exact-normalized name/alias match** against that company's as-filed
   models — including the **make-prefix-stripped** form ("Toyota 4Runner"
   ↔ `4Runner`; still mechanical, never fuzzy). A unique hit is a model
   correspondence: write the QID onto the model row, assert facts. This
   rung is what makes level per-make: C-Class matches Mercedes' as-filed
   row and is a *model*; "BMW 3 Series" matches nothing of BMW's and
   falls through.
4. **Line evidence** — P179 members pointing at it, or series class with
   chain-linked children → a **line** (§4), never a model row.
5. **Generation evidence** — chained to (or P179-member of) a matched
   model or a line → **generation structure** (§5).
6. **Otherwise** → `match_review` flag with candidates only when the
   entity had a resolvable company and near-miss candidates; else it
   waits in raw.

### 3. Wikidata may CREATE models under held companies — the global expansion

The mission is every production car globally; vPIC only sees the US.
An entity that resolves to a held company (rung 3 found no as-filed
match, rungs 4–5 found no line/generation evidence) and is model-shaped
**creates** an as-asserted model under that company — identity ladder
first, slug collisions flag rather than suffix, exactly ADR 0010's
mechanics with Wikidata as the filing source. This is the single biggest
consequence of this ADR: `models` grows from 1,735 toward the ~11k
entities with manufacturers — the Euro/JDM/historical nameplates vPIC
never met. Under-admission stays the cheap error: a wrongly-waiting
entity is one policy edit + re-run away.

`FIELD_AFFINITY` for `models.name` registers to the **filing source
per row** in effect: vPIC-created models keep vPIC names (Wikidata labels
prefix the make — "Toyota 4Runner" must not rename `4Runner`), and
Wikidata asserts `summary` everywhere it matches. Wikidata-created models
take their (prefix-stripped) label as name.

### 4. Lines are grouping rows, not entities

Two new tables: `model_lines` (id, company_id, slug, name — a grouping
key with a display name) and `model_line_members` (line_id, model_id,
plus the standard fact provenance columns). Lines hold **no external
ids** (ADR 0011 §4: the series QID stays on the raw record; memberships
carry `raw_record_id` provenance), are FK'd by nothing else in the
hierarchy, and render as browse views ("/makes/bmw/3-series" is a page
over members, not an entity). Membership arrives from P179 and from
rung-3 matches of member-shaped entities; it is a fact like any other —
superseded, flagged, never unique-constrained across sources.

### 5. Generations: identity from Wikidata, time from vPIC

- **Direct case**: a generation-shaped entity attached to a *matched
  model* creates a `generations` row under it — name from the label,
  chassis codes extracted from label/alias parentheticals ("(E46)");
  ambiguous extractions flag rather than guess. Span years only when the
  entity asserts dates; span-less generations are legal (identity now,
  time later).
- **Line case** (the BMW shape): generation entities of a *line* land as
  line-level structure only. Instantiating them under each member model
  (E46 under `330i` by year overlap) is **deferred to the year-pass
  ADR**, where vPIC's year lists exist to overlap against — that
  inference needs the time axis this sweep doesn't carry, and doing it
  with sparse Wikidata dates would guess. Chains (P155/P156) order
  generations even where dates are absent.

### 6. What this ADR does NOT do

- No catalogue periods, no configurations — the year-pass ADR consumes
  the generations this one creates.
- No Wikipedia scraping; the sweep stores the sitelink pointer only.
- No fuzzy matching anywhere; every ambiguity is a flag with candidates.
- No platforms (pid unverified — zero P4243 uses; re-probe first).

## Consequences

- A migration for `model_lines` / `model_line_members` (provenance
  columns per the charter's fact-table rule).
- `RECONCILER_VERSION` bumps; the sweep lands ~14.6k raw records
  (bare-QID, `sweep: models`); the pass grows `models` toward the global
  set and creates the first `generations` and lines.
- The duplicate-entity disease (four "BMW 3 Series") meets the same cure
  as companies: curated `IDENTITY_MERGES`, model-level registry, grown by
  resolving flags.
- The year-pass ADR unblocks the day this lands: periods hang under
  §5's generations, and the line-case instantiation happens there with
  vPIC years in hand.
