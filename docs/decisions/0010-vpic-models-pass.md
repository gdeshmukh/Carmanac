# ADR 0010 — The vPIC models pass: nameplates under matched makes

- Status: Accepted (2026-07-29, as written)
- Date: 2026-07-29
- Depends on: ADR 0007 (reconciler contract), ADR 0008 (vPIC make matching),
  ADR 0009 (catalogue periods — not consumed here, but the level below this
  one is now unblocked)

## Context

vPIC passenger models are landed: one raw record per ModelId
(`model:<id>`), carrying the model's name, its make's vPIC id, and its
passenger vehicle types. This is the first model-level data — the cars'
nameplates — and the first pass that populates `models`.

Two facts shape the design. First, **every vPIC model arrives pre-attached
to a make we may already know**: the payload's `make_id` resolves through
`external_ids` (`make:<id>` → company) for the 134 matched makes. There is
no cross-source model matching to do yet — vPIC is the first source to
assert models at all — so this pass faces none of ADR 0008's ambiguity
ladder. That changes when Wikidata models land; this ADR deliberately does
not design that matcher.

Second, **models under unmatched makes have no company to attach to.**
Creating companies from vPIC evidence alone is a decision Gaurav has
explicitly deferred (the parked no-match pool), so their models must wait
without blocking anything.

## Decision

### 1. Scope: models of MATCHED makes only

The pass processes model records whose `make_id` resolves to a company via
`external_ids`. Records under unmatched makes are marked reconciled-seen but
create nothing and open no flags — the make-level `match_review` flag
already represents that question, and one open question should not fan out
into fifty model-shaped copies of itself. When a make converts later, the
next pass picks its models up mechanically (re-reconciliation is the normal
case).

### 2. Creation: upsert by natural key, identity ladder first

Per model record, in ascending ModelId order:

1. `external_ids` hit (`model:<id>`) → the existing `models` row; refresh.
2. Otherwise create under the resolved company: `slug = slugify(model_name)`
   (unique per company — `uq_models_company_id_slug`), `name = model_name`
   as vPIC asserts it (mixed case: "Accord", "FCX Clarity"), write the
   `external_ids` row in the same transaction.
3. Slug collision under the same company (two distinct ModelIds slugging
   identically — "GT-R" vs "GT R") → the colliding record gets a
   `match_review` flag and no row; a human picks merge-or-suffix. Never
   auto-suffix: unlike company slugs, two same-slug models under one make
   are almost always the same nameplate seen twice, and minting `gt-r-2`
   would manufacture the exact duplicate-identity problem the company/brand
   merges just cleaned up.

### 3. Name is a reconciled fact like any other

`models.name` projects from a `field_provenance` assertion (model arc,
source vPIC), so when Wikidata models land with richer labels the normal
tier/affinity machinery arbitrates — `FIELD_AFFINITY` gains
`models.name → Wikidata` only when that source actually lands. No casing
policy is invented here: vPIC's mixed-case strings are stored as asserted.

### 4. What this pass does NOT do

- No generations, no catalogue periods, no configurations — nameplate level
  only. The year/config passes come after this one proves out, consuming
  ADR 0009's spine.
- No `vehicle_derivations`, no roles: nothing model-level asserts either.
- No deletion on disappearance: a ModelId vPIC stops returning follows the
  ADR 0007 amendment (`source_dropped` flag), not auto-retirement.

## Consequences

- `carmanac/reconcile/models_pass.py` (or an engine generalization — the
  engine is companies-specific today; whether to generalize `_Pass` or
  build a sibling is an implementation choice, not an ADR-level one).
- `reconciled_records` covers model records; `RECONCILER_VERSION` bumps.
- The models table gains its first rows (~130 makes × their nameplates).
- Model pages become demoable, which re-opens the F2 thin-read-surface
  sequencing question on much stronger footing (make → models → model).
