# ADR 0014 — The year pass and the EPA attach: periods, configurations, and the two bridges

- Status: Proposed
- Date: 2026-07-31
- Depends on: ADR 0009 (catalogue-period spine — §1 proposes a flagged
  revision), ADR 0011 (as-filed models), ADR 0012 (generations and lines;
  deferred the line-case instantiation here), ADR 0013 (name-form evidence
  ranks)

## Context

The feedstock is fully landed and waiting:

- **vPIC model years**: 2,018 `modelyears:` records, one per as-filed
  model, 100% coverage — sorted year lists, 1981→current+1.
- **EPA vehicles.csv**: 49,995 per-variant rows (1984–2027) carrying
  make, model, `baseModel`, year, transmission, drive, displacement,
  cylinders, fuel type — configuration-grain data.
- **Generations** (ADR 0012's pass): 151 rows covering **51 of 1,735
  models**; only **4 carry dates**. 453 line-case generation entities
  wait in raw for a time axis.

Bridge baselines, measured live 2026-07-31 (row-weighted, normalized
joins — the 2026-07-30 direction review's numbers, re-measured after the
v10/ADR 0013 refresh):

- EPA make → held company **via vPIC make names**: **99.0%** of rows
  (49,500/49,995). Via company names it would drop ~3,300 rows — EPA
  says "Audi", the company row says "Audi AG"; the vPIC make-name hop is
  load-bearing. The unbridged 1% (495 rows) is a short tail: Scion,
  Roush Performance, McLaren Automotive, J.K. Motors, American Motors
  Corporation, …
- EPA model string → as-filed model, exact: **27.8%**.
- EPA `baseModel` → as-filed model: **76.4%**.
- BMW (the badge-filing stress case): 2,684 rows; exact 720, baseModel
  660 — most of BMW needs the trim-parse rung.

**The blocker this ADR must resolve**: `catalogue_periods.generation_id`
is NOT NULL (ADR 0009), but the generation-asserting source has now
landed and delivered generation *identity* without generation *time* —
4 dated rows. Even a model with exactly one generation (23 models)
cannot legally receive its full year list under it: an undated E46-shaped
row plus a 1984–2027 year list says nothing about which years are E46's.
Strictly applied, the spine admits periods for **~4 of 2,018 models**.
Waiting further does not help: the next generation-time source
(Wikipedia infoboxes) is a separate ingestion effort, and the charter is
explicit that the cars are the mission — the year/variant data IS the
cars, and it is landed, asserted, and blocked behind a level no US
source speaks.

## Decision

### 1. Periods hang under models; the generation link becomes an evidence-gated fact (REVISES ADR 0009 — flagged)

This is a deliberate, flagged revision of an architecture invariant, not
a slip: **`catalogue_periods` gains `model_id` (NOT NULL, FK, indexed)
and `generation_id` becomes NULLABLE.** A period always knows its model
— that is exactly what vPIC asserts: (model, year), no more. The
period→generation link is written only when a source actually places
that period's span inside a generation (dated overlap today, infobox
spans later), with normal fact provenance. Configurations still FK
periods; nothing downstream changes.

What this preserves: no fabrication, in either direction — ADR 0009's
core. A period under a bare model records precisely the assertion made.
What it changes: the five-level FK chain becomes four levels with
generations as an **attachable enrichment level** rather than a
mandatory waypoint. Generation pages still render (over the periods
linked to them, and the chassis-code views of ADR 0012 §5); the
generation question for any period stays visibly open (NULL) instead of
invisibly wrong.

Rejected alternatives:

- **Strict spine** (write only under dated generations): ~4 models'
  periods exist; 2,014 models' landed year lists stay raw-only
  indefinitely. Starves the mission to protect a chain the sources
  don't speak.
- **Fabricated placeholder generations** (one per model): rejected twice
  already (ADR 0009, the 2026-07-29 open question) — a provenance lie
  that later sources would have to fight instead of fill.

### 2. The vPIC year pass

Per `modelyears:` record, per year: upsert a `model_year` period
(`start_year = end_year`, kind `model_year`) under the model, natural
key per ADR 0009. Assertions carry the record's provenance; re-runs are
no-ops (the settling discipline: a world-changing run settles on the
following run). Decision log: pass `vpic_years`, one decision per
record.

Generation attach, in the same pass, only where legal: a period whose
year falls inside a **dated** generation of its model links to it
(containment, ADR 0009's mixed-granularity rule); overlapping dated
generations flag rather than guess. Undated generations get nothing —
their dating is the line-case/infobox work, not this pass's.

The 453 line-case generation entities **stay deferred** (ADR 0012 §5
posture). This ADR decouples them from the year pass: periods no longer
wait on generation instantiation, and the E46-under-330i inference still
needs generation time that neither vPIC years nor 6%-sparse Wikidata
dates provide. They activate when a generation-dating source lands.

### 3. The EPA attach: two bridges, both laddered, never fuzzy

**Make bridge** (target ≥99%, measured 99.0%): normalized EPA make →
vPIC make name → matched company. The 1% tail resolves through a curated
`EPA_MAKE_MATCHES` registry (EPA make string → company), grown by
resolving flags — the Scion entry routes its 84 rows to the company
holding the as-filed Scion models (Toyota), the McLaren Automotive /
American Motors Corporation entries are spelling hops. Registry entries
are recorded human judgments, same species as `VPIC_MATCHES`.

**Model bridge**, per bridged row, descending (first rung that hits,
decision logged per rung, pass `epa_attach`):

1. **Exact**: normalized EPA model = as-filed model name (27.8%).
2. **baseModel**: normalized `baseModel` = as-filed model name (takes
   coverage to ~76%). Where `baseModel` instead names a **line** (BMW
   "3 Series"), it narrows the candidate set for rung 3 but attaches
   nothing itself — lines are not models (ADR 0011).
3. **Trim-parse for badge filings**: the EPA model string begins with an
   as-filed model name **on a word boundary** (today's Ranger/Range
   Rover lesson, applied from day one), longest match, within the
   bridged make — "328i xDrive Coupe" wears `328i`; the residue is the
   trim string. Mechanical prefix, not fuzzy.
4. **Residue**: `match_review` flag with candidates when near-misses
   exist under the make; else waits, logged. Coverage is reported per
   rung; the acceptance bar is honesty (every row accounted for in the
   decision log), not a forced percentage.

A model-bridge hit must also find its **period**: (model, EPA year) →
the year pass's `model_year` period. EPA years outside vPIC's list (the
1984–1993 tail vPIC doesn't cover, EPA rows to 2027) create the period
under the same rules — EPA asserts (model, year) exactly as vPIC does.

### 4. What the EPA pass writes

One **configuration** per EPA vehicle id under the resolved period:
US market, `trim_name` from the trim-parse residue (empty for exact
hits), drivetrain from `drive`. Core numeric specs (displacement,
cylinders, EPA economy figures) land as facts — columns where the ~20
universal core specs already have them, EAV otherwise, attributes
registered in `attribute_definitions` before landing (charter rule).
**No engine or transmission entities in v1**: `engId`/`eng_dscr`/`trany`
land as configuration facts; minting cross-referenced engine entities
from EPA strings is its own matching problem and its own ADR. The EPA
row's `id` writes to `external_ids` (`vehicle:<id>`, 1:1 per ADR 0011
§4).

### 5. Scale discipline

This is the first write pass at 50k scale: **batch-committed** (the
direction review's requirement) with per-batch progress, resumable, and
run first against a copy of the counts (dry mode prints per-rung
totals). Full settling verification: run twice, identical counts, zero
assertion churn; `status.py` gains periods/configurations coverage
lines.

## Consequences

- A migration: `catalogue_periods.model_id` NOT NULL FK (backfill from
  the existing generation links — all 0 rows today, so the backfill is
  trivial), `generation_id` NULLABLE, natural key updated to include the
  model. Hand-reviewed per the ADR 0009 lesson.
- ADR 0009's text gets a pointer to this revision; the charter's
  five-level invariant is amended to name generations an evidence-gated
  level between models and periods.
- `RECONCILER_VERSION` bumps; two new pass names in `match_decisions`.
- First configurations exist (~49.5k), which unblocks the configuration
  page work (F2) whenever Gaurav re-opens it — and makes the EAV
  benchmark question (charter risk) testable with real rows.
- The line-case generations and the engine/transmission entity passes
  are explicitly NOT this ADR; each gets its own when its evidence
  source exists.
