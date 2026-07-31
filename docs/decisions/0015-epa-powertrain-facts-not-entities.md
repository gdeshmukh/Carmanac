# ADR 0015 — EPA powertrain data: facts now, entities when a naming source lands

- Status: Proposed
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

- `trany` style maps to `configurations.transmission_type_id`, with one
  correction: EPA's `AM`/`AM-S` codes mean *automated manual* and cannot
  distinguish single-clutch from dual-clutch (the smart fortwo and the
  GT-R share a code). The current pass maps them to `automatic`, which
  is wrong for every DCT. A new `transmission_types` row
  `automated_manual` ("Automated manual (incl. dual-clutch)") receives
  them — truthful to what EPA asserts — and the 2,042 affected rows'
  configurations are refreshed under the corrected mapping (one
  decisions script, provenance superseded properly, settling re-runs).
- Gear count lands as EAV (`transmission_gear_count`, integer, registered
  in `attribute_definitions` first): CVTs have none and EV "1-spd" is a
  convention, so it fails no-value-for-real-cars honesty as a column.
- The `transmissions` table stays empty until a source that names
  gearboxes lands (Tier 2/3); `configuration_transmissions`' provenance
  columns are ready for that day.

### 2. Engine entities wait for the engine-naming source

The structural precedent is the generation question: entity identity
requires a source that *asserts the entity*, and for engines that source
is already identified — **vPIC's decode layer carries Engine Model,
Engine Manufacturer, Engine Configuration, and Engine Power** (live-probed
2026-07-31, the 144-variable inventory). It is the same "identity from
the asserting source, specs corroborated by EPA" split that ADR 0012 set
for generations. The engines-entity ADR follows the vPIC spec-depth
landing (the full-database download probe, already queued as its own
design turn). EPA's spec tuples then *corroborate and enrich* named
engines instead of pre-empting them.

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

- **No migration.** Two lookup-row additions and EAV registrations only.
- `RECONCILER_VERSION` bumps (the AM mapping is a behavior change); one
  decisions script refreshes the ~2,042 mis-typed configurations'
  `transmission_type_id` with proper supersession; double re-run settling
  verification throughout.
- The engines-entity ADR (with the vPIC decode landing as its evidence
  base) and the gear-count-as-column question are explicitly NOT this
  ADR.
- Recorded correction to ADR 0014's Consequences: it estimated "~49.5k
  configurations"; the implemented grouping (rows sharing a natural key
  merge) landed 23,523 — the estimate predated the group design, the
  count is correct.
