# Schema traps

Postgres and SQLAlchemy behaviours this schema depends on, each of which is
non-obvious enough to have cost real time. Companion to
`carmanac/db/base.py` and `carmanac/db/models/`.

## The supersession dance

Fact tables keep one *live* row per (entity, field, source), enforced by a
partial unique index:

```sql
CREATE UNIQUE INDEX uq_field_provenance_live
    ON field_provenance (company_id, field_name, source_id)
    WHERE superseded_by IS NULL;
```

Superseded rows are exempt, so history accumulates freely while exactly one
current value exists.

The obvious way to supersede does not work:

1. Insert the new row → **rejected**, two live rows for the same slot.
2. Point the old row at the new one → impossible anyway, the new id does not
   exist yet.

The working order is three steps with a flush between each, so the partial
index sees each intermediate state:

1. **Retire the old row by pointing `superseded_by` at itself.** This frees
   the live slot without needing a successor to exist.
2. **Insert the successor.** The slot is now empty, so it is accepted.
3. **Repoint the old row at the successor.** History is now linked correctly.

`engine._supersede`. Discovered by the test suite, and
`tests/test_constraints.py` pins it.

## NULLS NOT DISTINCT is load-bearing

Postgres treats NULLs as never equal by default, so a unique constraint
silently does nothing for rows with a NULL in the key — exactly the sparse
records that need it most.

`configurations`' natural key is
`(catalogue_period_id, trim_name, market_region_id, drivetrain_id, body_style_id)`.
Trim, drivetrain and body style are all legitimately unknown. Under default
semantics, two rows for the same car with an unknown trim would both insert and
the constraint would quietly not fire.

`postgresql_nulls_not_distinct=True` (Postgres 15+) makes "unknown" compare
equal to "unknown", without forcing a fake `UNKNOWN` lookup row onto every
dimension table.

The same reasoning applies to:

- `catalogue_periods` — an open-ended period (`end_year` NULL) must still
  collide with its duplicate.
- The per-source assertion stores (`company_role_assignments`,
  `model_line_members`, `vehicle_derivations`) — sourceless seed rows count as
  one anonymous asserter rather than never colliding.
- `vehicle_derivations` twice over, since the common case (`derived_generation_id`
  NULL — Ruf conversions, Murphy bodies) would otherwise let a re-running
  reconciler insert the same claim endlessly.

## Inline CheckConstraints vanish from migrations

A `CheckConstraint` attached inline to a column renders correctly under
`metadata.create_all()`, so it works in tests and looks right in the model.

Alembic autogenerate only inspects `Table.constraints`. An inline column
constraint is therefore **silently dropped from generated migrations** and
never reaches the real database — a constraint that exists in the test schema
and not in production.

Hence `provenance_table_args()`: table-level, spread into every
`__table_args__` that uses `ProvenanceMixin`.

## onupdate does not fire on bulk paths

SQLAlchemy's `onupdate=func.now()` fires on ORM updates only. Bulk ingestion
(COPY, `INSERT ... ON CONFLICT`) bypasses the ORM entirely, so `updated_at`
would go stale exactly during the operations that change the most rows.

The rule therefore lives in the database as a `BEFORE UPDATE` trigger
(migration `a1c4e7b93f20`), which is what actually holds during ingestion. The
trigger skips no-op updates (`NEW IS DISTINCT FROM OLD`) so an idempotent
re-scrape that changes nothing does not bump the timestamp. `onupdate` stays as
a harmless duplicate so ORM objects see a fresh value within the same session.

## Constraint naming must be explicit

Without a `MetaData(naming_convention=...)`, Alembic autogenerate emits
server-assigned constraint names that differ between environments. Migration
diffs become unstable and hand-review becomes guesswork.

`NAMING_CONVENTION` in `carmanac/db/base.py`.

## Partial indexes for arc columns

Several columns are NULL for the majority of rows — `vehicle_derivations.derived_generation_id`
is NULL for all historical contract coachbuilding, and `field_provenance`'s
entity-arc columns are NULL for every row belonging to a different arc.

A query by value never needs the NULLs, so those indexes are partial
(`postgresql_where=... IS NOT NULL`). Note that where a *live-unique* partial
index already leads with a column, a separate full index is still needed for
history reads — "every claim ever made about the 964" has to see superseded
rows too.

## Trigram indexes for entity resolution

`companies.name`, `models.name`, `generations.name` and
`configurations.trim_name` all carry GIN trigram indexes. Entity resolution
matches incoming source names fuzzily ("BMW AG" → "BMW"), and a btree cannot
serve `similarity()`.

`generations.chassis_codes` is a text array with a GIN index for the same
reason in a different shape: "show me every E46" is `text[] @> ARRAY['E46']`,
which a btree also cannot serve.
