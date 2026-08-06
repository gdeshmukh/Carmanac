# ADR 0016 — Generations become company-anchored index entities

- Status: Accepted (2026-08-06)
- Date: 2026-08-06
- Depends on: ADR 0014 (placement is a nullable, evidence-gated fact on
  `configurations`), ADR 0012 (the model sweep that minted the existing
  generation rows), ADR 0011 §4 (external ids are 1:1 correspondence)
- **Revises a settled invariant** (flagged per the charter's rule):
  `generations` stops being a child of one model.
- Sibling: ADR 0017 owns the *evidence* side — where generation
  existence and time come from, and how placement is decided. This ADR
  is purely structural: what a generation IS in the schema.

## Context

Since ADR 0014, generations are not a load-bearing level of the spine —
the mandatory rails are companies → models → catalogue_periods →
configurations, and generation placement is an evidence-gated fact on
the configuration. What a generation actually is, is an
**enthusiast-facing index and display entity**: a name, chassis codes,
a time span, a page. Chassis codes are how enthusiasts address the
database — nearly an indexing system of their own.

The structure said otherwise. A generation row hung under exactly one
model (`generations.model_id` NOT NULL), but real generations cover many
as-filed models — one E46 spans 325i, 330i, M3. Under the old shape a
line-level generation either minted one row per member model (three E46
pages) or arbitrarily picked one parent. This is why the 453 line-case
generation entities from the ADR 0012 sweep sat unrepresentable: there
was no honest single parent to hang them under.

Measured before deciding (2026-08-06): 151 generation rows under 51
models; zero company-scope slug collisions among them, so the re-anchor
needed no renames.

## Decision

### 1. Generations are re-anchored to companies

- `generations.model_id` is replaced by `generations.company_id`
  (NOT NULL — the E46 is a BMW thing); uniqueness becomes
  `(company_id, slug)`.
- **Which models a generation covers is derived, not declared**: the
  generation page's model list is a query over placed configurations —
  the charter's "aggregation pages are queries over the spine" applied
  to the generation page itself.
- The migration (`c113fff36784`) re-parents the 151 existing rows
  (company reachable through their current model), hand-written with
  the refuse-don't-discard downgrade posture.

### 2. Model↔generation links are source-asserted facts

Dropping the parent FK removes the only thing that told any consumer
which generations belong to a model's history. That knowledge was always
a source assertion (the Wikidata models pass attached each generation
QID to a specific nameplate), so it becomes an explicit fact-bearing
association table, same species as `model_line_members`:

- `generation_model_links` — (`generation_id`, `model_id`,
  `source_id`, `raw_record_id`, `scraped_at`, `confidence_score`),
  live-unique per (generation, model, source). A row means "this source
  places this generation in this model's history." Links are evidence,
  never inference.
- The Wikidata models pass writes these links where it previously set
  the parent FK; the migration seeds the initial 151 as sourceless rows,
  and the pass's next run re-asserts them with full provenance,
  superseding the anonymous seeds (adoption, not accumulation).
- The line-case entities (453 waiting) become representable without a
  schema fight: a company-anchored generation whose links arrive when
  line membership tells us which as-filed models belong to the series.
  Building that membership stays its own design turn — but it is now
  only a data gap, not a schema deadlock.

## Consequences

- **One generation, one row, one page**, for the schema's whole future:
  chassis codes work as the enthusiast index they are ("show me every
  E46" is one GIN-indexed query), and the 453 line-case entities stop
  being structurally unrepresentable.
- Any pass that consumes "which generations might this configuration
  belong to" reads `generation_model_links` — the candidate gate
  ADR 0017's placement pass builds on.
- The charter's Schema Overview is amended in the same branch
  (generations FK → companies; links table added).
- Verified live: migration round-tripped, 151/151 seed links adopted
  with provenance on the first pass run, second run an exact no-op, all
  passes converge. Reconciler v13.
