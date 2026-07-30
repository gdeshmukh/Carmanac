# ADR 0011 — Models are as-filed leaf designations; series are lines, not entities

- Status: Proposed (drafted from Gaurav's 2026-07-30 review of the models
  pass; §1's class-shaped-name carve-out is the one interpretive addition
  to confirm)
- Date: 2026-07-30
- Depends on: ADR 0007 (reconciler contract), ADR 0010 (the models pass)

## Context

The models pass (ADR 0010) landed 1,735 rows and made vPIC's granularity
visible: it varies **by make and by era**, because the names are the
manufacturers' own regulatory filings. Toyota files nameplates (4Runner,
Camry). BMW files engine badges (318i, 330i, 328d) next to true nameplates
(M3, X5). Ferrari splits by body (308GTB coupé vs 308GTS Spider). Mercedes
files classes today (C-Class) and bare numbers in the '80s (560). Porsche
files one `911` across six decades.

The question this raised — is a vPIC ModelId a nameplate, a trim, or
something per-make? — was resolved by review: **they are all the leaf
version of the car**, and asking "which one is the *real* model" imposes a
taxonomy the manufacturers themselves do not share. If you ask for a 308,
the next question is "GTB or GTS?" — each deserves its own page.

Separately, the database holds one series-shaped row: the demo seed's
`3-series` (with its E46 → 2002 → 330i-us-sedan chain), created 2026-07-22
to prove the schema end to end, before any real ingestion existed.

## Decision

### 1. The model level holds leaf catalogue designations, as filed

A `models` row is the finest-grained designation the manufacturer itself
catalogues, exactly as a source asserts it. Granularity belongs to the
manufacturer, and the schema absorbs the variation instead of normalizing
it: **generations carry time** (911 → 992, 330i → E46), **configurations
carry engine/body/trim/market** (the 4Runner page shows several engines
side by side; the 328d page shows exactly one diesel, because that is the
model). No pass re-levels, splits, or normalizes a filing across makes;
per-make curated splitting stays forbidden (ADR 0007's no-fuzzy rule).

**The class-shaped-name carve-out.** Mercedes' `C-Class` row stays. It
looks series-shaped, but it is Mercedes' actual filing — the finest grain
the source asserts for that make — so it IS the leaf designation, and its
engine variants land as configurations (the 4Runner shape). Removing it
would orphan the make's entire US catalogue, and the next pass run would
lawfully recreate it from raw. "Series are not models" (§2) is a statement
about aggregation entities, never about filed names that happen to sound
broad.

### 2. Series and lines are not entities

"3 Series" is a **line**: real, browseable, and useful ("show me every
3 Series ever made"), but it is an aggregation *over* models, not a model.
No source's series-level entity may mint a `models` row — Wikidata's
"BMW 3 Series" entities included.

The browsing need is served later by a source-asserted **membership
relation** (Wikidata's "part of the series" is the obvious first assertion
of it), rendered as a view or page over the member models. A page does not
require an identity row. Designing that relation belongs to the Wikidata
models ADR, not this one.

### 3. The demo seed chain retires

The seed's entity chain — `3-series` → E46 → 2002 → 330i-us-sedan, plus
the demo engine (M54B30), transmission (Getrag 220), their join rows, EAV
attributes, provenance rows, and external ids — is synthetic scaffolding
whose schema-proving job ended when real ingestion landed, and its head
row now violates §2. It is deleted live by `scripts/retire_demo_seed.py`;
`scripts/seed_demo.py` is deleted with it (README updated).

What stays: the three simulated raw records (raw is not casually deleted,
and they are inert), and everything the seed created that is *reference*
rather than demo — lookup rows, the `sources` rows, the two
`attribute_definitions`.

### 4. Our identity is ours; external ids are correspondence, not identity

Entity identity is our own serial PK plus natural key (slug within
company, and so on) — it never derives from any source's identifier. An
`external_ids` row records that a source's entity IS one of ours,
one-to-one. When a source entity corresponds to **none or several** of our
rows — a series entity under §2, or a generation entity that fans across
several badge models — no `external_ids` row is written: its facts land on
our rows with raw-record provenance (`field_provenance.raw_record_id`),
and identity resolution for such records runs on natural keys. The
"where does the QID live" question dissolves: nowhere, when the
correspondence is not one-to-one.

### 5. Source-side duplicates are reconciliation work, not constraint work

Our constraints police OUR rows — slug unique per company, one live
assertion per (entity, field, source), one (source, external id) → one
entity. They cannot stop a source from holding four distinct entities all
labelled "BMW 3 Series"; each arrives under its own key and looks
legitimate one record at a time. Those resolve the way company-side
duplicates did: **curated identity merges and review flags, never
automatically** (ADR 0007 §5, the Bugatti/Tesla precedent). The
model-level merge registry arrives with the cross-source ladder.

## What this ADR does NOT do

- Pick the generation-asserting source. Open, and deliberately not
  Wikidata-by-default: Wikipedia's per-generation infoboxes (Tier 2) look
  stronger than Wikidata's patchy structure and get evaluated beside it in
  the fetch ADR.
- Design the line/membership relation (§2 defers it to the Wikidata models
  ADR).
- Change any as-filed row, or any ADR 0010 mechanics — the models pass
  never created series rows, so no pass code changes.

## Consequences

- `models` drops to 1,735 (pure vPIC); `generations`, `catalogue_periods`,
  `configurations` drop to 0. Every entity row in the database is now
  reconciler-produced. The zeros are honest: no source has asserted those
  levels yet.
- Near-duplicate leaf filings stay separate rows until curated merges or
  review resolve them (BMW `228`/`228i`, Ferrari `308GTS`/`308
  Convertible`, `Boxster`/`718 Boxster`) — §5's machinery, model-level.
- The year pass's remaining blocker is the generation question alone.
- `Model`'s docstring example updated (it used "3 Series").
