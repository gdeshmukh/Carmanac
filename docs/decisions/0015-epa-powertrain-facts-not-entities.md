# ADR 0015 — EPA powertrain data: facts now, entities when a naming source lands

- Status: Accepted (2026-07-31, as amended in deliberation — no
  `automated_manual` bucket: AM/AM-S assert nothing, preserving the
  single-clutch/dual-clutch distinction for a source that can see it;
  `sequential` added to the lookup, dormant; gear-count EAV dropped
  (gear count belongs to future transmission entities, not
  configurations); existing mis-typed configurations corrected)
- Date: 2026-07-31
- Depends on: ADR 0002 (entity/fact split), ADR 0011 §4 (external ids are
  1:1 correspondence), ADR 0014 §4 (deferred exactly this question)

## Context

The direction was "engine and transmission entities first." Before
designing, the landed EPA data was investigated four ways in parallel
(engId semantics, eng_dscr content, trany/trans_dscr content, schema
constraints) and every load-bearing claim was independently re-derived by
an adversarial verification pass. The findings, all verified:

- **`engId` is not an engine identifier.** 25.2% of rows carry the `0`
  sentinel, with an eleven-year dead zone (1998–2009) where it is ~100%
  zero. The 2009+ regime is a per-(year, make, model) row index — 92% of
  its groups mix different displacements and 89% mix makes (engId=1 spans
  48 makes, Bugatti to Tesla). The 1984–1997 regime is a real per-year
  engine code with genuine GM-style cross-badge sharing, but codes are
  reissued across years (engId=2420 is a 2.2L in one year, a 2.5L in
  another). No form of it can key an entity or safely enter
  `external_ids`.
- **`eng_dscr` is attributes, not identity**: 99% of filled values are
  flags (SIDI, FFS, TRBO, GUZZLER, CA-model); identity-like strings are
  1.1% of filled rows (a few Toyota codes, LM7, "Hellcat engine"). There
  is no "B58" anywhere in 49,995 rows — the Supra and Z4 carry identical
  specs, different engIds, and the only cross-make signal is the Supra's
  `mfrCode=BMX` (2009+ rows only). The Saab 9-2X / Subaru Impreza pair
  shares no key of any kind.
- **Transmission strings name no gearboxes.** 41 distinct `trany` values
  (style × speeds, parses 100% cleanly); `trans_dscr` is torque-converter
  flags; zero hits for any manufacturer or model name (ZF, Aisin, 8HP,
  PDK…) in the entire dataset. "Automatic 4-spd" covers 77 makes over 36
  years — many physically different units in one string.
- **Schema**: `engines`/`transmissions` identity is manufacturer + name —
  exactly what EPA never provides (`manufacturer_company_id` is nullable,
  so maker-less rows are *representable*, but the tables have no
  content-based natural key: `slug` is the only unique constraint, so an
  EPA pass would first have to invent the identity it cannot observe).

## Decision

**EPA cannot honestly mint powertrain entities, so it doesn't.** Minting
would create spec-bins, not engines: ~3,194 anonymous
(company, displ, cylinders, eng_dscr) clusters that split real shared
engines (Supra/Z4, Saab/Subaru become separate rows), merge distinct
same-spec engines, and hand every future naming source a cleanup job.
That is the miscategorization machine, one level down. Instead:

### 1. Transmissions are facts-only from EPA, indefinitely

- `trany` maps to `configurations.transmission_type_id` **only where EPA
  is unambiguous** (ruled in deliberation): plain automatics, shiftable
  S-codes, and the EV A-codes (all torque-converter-or-EV shapes in
  EPA's coding) → `automatic`; `variable gear ratios`/AV-codes → `cvt`;
  manuals → `manual`. **`AM`/`AM-S` codes assert nothing** — EPA cannot
  distinguish a single-clutch automated manual from a dual-clutch, the
  driving-dynamics difference is enormous, and no bucket word
  ("automated manual") may erase it. Those 2,042 rows' configurations
  carry NULL until a source that names the gearbox lands.
- The pass gains **sole-source column refresh**: on re-run, a column
  whose only live assertion is EPA's is recomputed, and a changed answer
  supersedes the old assertion properly (the "open question refreshes"
  principle applied to columns). This corrects the ~2,042 rows'
  configurations already written under the old AM→automatic mapping —
  and makes every future mapping fix self-applying.
- `sequential` joins the `transmission_types` lookup (dormant — EPA will
  likely never file one, but the type exists for sources that do).
- No gear-count EAV: gear count is a property of a transmission entity,
  and we hold none to tie it to.
- The `transmissions` table stays empty until a source that names
  gearboxes lands (Tier 2/3); `configuration_transmissions`' provenance
  columns are ready for that day.

### 2. Engine entities wait for the engine-naming source

The structural precedent is the generation question: entity identity
requires a source that *asserts the entity* — a source class, not one
source. vPIC's decode layer is the leading candidate (Engine Model,
Engine Manufacturer, Engine Configuration, Engine Power — live-probed
2026-07-31), behind its own probe-first design turn; Wikidata engine
entities and the Tier-3 marque wikis are the others, and none is assumed
sufficient in advance. Whatever lands first with real engine names gets
the engines-entity ADR; EPA's spec tuples then *corroborate and enrich*
named engines instead of pre-empting them.

Explicitly rejected: `engine:<engId>` external ids (not 1:1 in any era);
spec-cluster entities (above); using the car's company as the engine's
maker (EPA never asserts the maker — the Supra's engine is not Toyota's).

### 3. What EPA contributes configuration-side now

- **Aspiration** as EAV (`aspiration`: `turbo` from `tCharger`,
  `supercharged` from `sCharger`; twin-turbo is undecidable from EPA, so
  the weaker claim is asserted knowingly; naturally-aspirated is only an
  absence and is not asserted at all). 11,616 + 1,147 rows.
- **Flex-fuel** as EAV (`flex_fuel`, boolean) from `atvType`/FFV flags.
- **Fuel-type completion**: two `fuel_types` rows added (`cng` Natural
  Gas, `hydrogen` Hydrogen — data addition, no migration) so the 102
  rows currently unmapped get their column value.
- Registered in `attribute_definitions` before landing (charter rule);
  written by the attach pass with the same unanimity-per-group and
  provenance rules as ADR 0014 §4.

## Consequences

- **No schema migration.** One seed migration adds the three lookup rows
  (`transmission_types.sequential`, `fuel_types.cng`,
  `fuel_types.hydrogen`) and registers the two `attribute_definitions`
  keys — the de1fcf30fd16 precedent: rows a pass consumes are
  migration-seeded.
- `RECONCILER_VERSION` bumps (the AM mapping is a behavior change); the
  pass's sole-source column refresh applies the correction to the
  already-written configurations on its first re-run; double re-run
  settling verification throughout.
- The engines-entity ADR (whichever engine-naming source lands first)
  and any future gear-count question are explicitly NOT this ADR.
- Recorded correction to ADR 0014's Consequences: it estimated "~49.5k
  configurations"; the implemented grouping (rows sharing a natural key
  merge) landed 23,523 — the estimate predated the group design, the
  count is correct.
