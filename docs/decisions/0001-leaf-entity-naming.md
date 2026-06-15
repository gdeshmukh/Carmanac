# ADR 0001 — Leaf entity named `configurations`, not `variants`

- Status: Accepted
- Date: 2026-06-15

## Context

The five-level entity hierarchy
(`makes → models → generations → model_years → variants`) was an Architecture
Invariant in `CLAUDE.md`, with the leaf level named `variants`. In design
review the name proved misleading:

- "Variant" is vague ("a variant *of* what?") and in common car-enthusiast
  usage loosely means a trim or a body style.
- This invited confusion with the **generation** level — e.g. "F80" reads like
  a searchable shortcut, and "variant" sounded like it should be that shortcut,
  when in fact the searchable shortcut is the generation (`generations`,
  carrying `chassis_codes`).
- The leaf's actual job is to be the fully-pinned combination
  (`model_year × trim × market × drivetrain`) — the only level at which a
  single spec value is unambiguous, which is why all ~20 core spec columns and
  the EAV/engine/transmission joins hang off it.

## Decision

Rename the leaf entity `variants` → `configurations`. `configuration` more
honestly describes "every dimension pinned, specs unambiguous" and does not
collide mentally with the generation-level shortcut.

Renamed accordingly:

- table `variants` → `configurations`
- `variant_attributes` → `configuration_attributes`
- `variant_engines` → `configuration_engines`
- `variant_transmissions` → `configuration_transmissions`
- all `variant_id` FK columns → `configuration_id`
- index prefixes `idx_variants_*` → `idx_configurations_*`,
  `uq_variant_attribute_live` → `uq_configuration_attribute_live`

This supersedes the leaf name in the `CLAUDE.md` Architecture Invariants and
the `/variants/...` entries in the URL/page-structure map, which should become
`/configurations/...` (or a chosen public-facing slug) when the frontend
routes are implemented. **`CLAUDE.md` still needs this edit applied in the
repo** — see Consequences.

## Consequences

- Done now, while no ingestion or application code references the leaf, the
  rename is a clean find-and-replace. Deferring it past Phase 1 ingestion would
  be substantially more painful.
- `docs/schema_phase1.sql` and `docs/schema.md` are updated and the DDL
  re-validated (58/58 statements parse against the Postgres dialect).
- **Action required:** update `CLAUDE.md` — the Architecture Invariants leaf
  name, the Schema Overview bullets, the "What Claude Should Never Do" EAV
  references, and the URL/page-structure map (`/variants/...`). This repo copy
  of `CLAUDE.md` is read-only in the current session; apply the edit in the
  repo directly.
- The public URL slug for the leaf is a separate open question (it need not
  literally be `configurations`); left to the slug-strategy ADR.
