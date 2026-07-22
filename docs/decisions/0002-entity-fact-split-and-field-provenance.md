# ADR 0002 — Entity/fact split and field-level provenance

- Status: Accepted
- Date: 2026-07-22
- Supersedes part of: the "provenance on every fact-bearing row" invariant in
  `CLAUDE.md` (see Consequences — the invariant is refined, not abandoned).

## Context

A schema review before the first scraper found the provenance model, as built,
could not survive multi-source ingestion. Two concrete defects:

1. **Supersession is structurally impossible on entity tables.** Every table
   had a `superseded_by` self-reference, per the stated invariant that "a fact
   is never destructively overwritten — a better fact supersedes the old one."
   But the entity tables also carry global unique keys (`makes.slug`,
   `models(make_id, slug)`, every `wikidata_qid`). A superseding row cannot
   coexist with the row it supersedes — the unique key rejects it. Verified
   live: inserting a second BMW row failed with
   `duplicate key value violates unique constraint "uq_makes_slug"`. The
   mechanism was present on nine tables and usable on none of them.

2. **Provenance was row-level, but facts are field-level.** `configurations`
   had a single `source_id` for the whole row. But `docs/schema.md` itself maps
   different fields to different Tier 1 sources — NHTSA for body style, EPA for
   mpg, Wikidata for dimensions. One `source_id` cannot express three sources
   contributing to one row. This breaks on the *second* source ingested, which
   is the immediate next step (Wikidata → NHTSA → EPA).

The root cause of (1) is a category error: a row in `makes` is an **entity**,
not a **fact**. "BMW exists" is identity; it is not a claim that gets
superseded. "BMW was founded in 1916" is a claim, and *that* is what needs
provenance and supersession — at the granularity of the individual field.

## Decision

Split the model into two layers.

**Identity layer** — `makes`, `models`, `generations`, `model_years`,
`configurations`, `engines`, `transmissions`. These hold identity and their
descriptive/spec columns as the **current best value**. They lose the row-level
provenance triple (`source_id`, `scraped_at`, `confidence_score`) and the
`superseded_by` self-reference. They are upserted by natural key; they are not
versioned. Their columns remain readable and fast to query — the hybrid-storage
invariant is intact.

**Provenance layer** — a new `field_provenance` sidecar records, for each
`(entity, field)`, every source's assertion: which `source_id`, which
`raw_record_id` (see ADR 0003), the `observed_value` that source claimed (as
text, for audit), a `confidence`, and `scraped_at`. Supersession lives here: a
newer assertion from the same source supersedes that source's older one via
`superseded_by`, and the retained history is the audit trail. Reconciliation
picks a winner across sources and writes it to the entity column; the column is
therefore a projection of the winning assertions, and can be rebuilt from
`field_provenance` at any time.

`field_provenance` uses an **exclusive-arc** shape: one nullable FK per entity
type plus a `CHECK` that exactly one is set. This keeps real referential
integrity (a polymorphic `entity_type/entity_id` pair would give that up, which
this project deliberately prizes) while remaining a single table.

The existing EAV table `configuration_attributes` already implements exactly
this pattern for sparse facts (value + provenance + working supersession via a
partial unique index). It is unchanged and is the model `field_provenance`
follows for core-column fields.

## Consequences

- **The `CLAUDE.md` invariant is refined, not dropped.** "Provenance on every
  fact" still holds — but provenance now attaches to *facts* (fields in
  `field_provenance`, rows in `configuration_attributes`, associations in the
  join tables), not to *identity rows*. Identity tables legitimately carry no
  provenance. `CLAUDE.md` should be updated to say so.
- Reads of core specs are unchanged — still plain columns.
- Writes gain a step: the reconciler records assertions in `field_provenance`
  and projects the winner onto the column. That logic does not exist yet and is
  the substance of the ingestion work.
- Migrating later to a full fact-based model (every value a row, columns as a
  materialized view) remains open and is now a smaller step, because the
  provenance history it would need already exists in `field_provenance`.
- `confidence` still needs a defined methodology (review item #6) before it
  carries weight; tracked separately.
