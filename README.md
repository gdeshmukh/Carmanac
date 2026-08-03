# Carmanac

A relational database of **every production passenger vehicle ever made, globally** — every make, model, generation, model year, and configuration, across all markets and eras.

Vehicle data is scattered across government APIs, manufacturer archives, enthusiast wikis, and scanned brochures, and no two sources agree. Carmanac ingests them continuously, reconciles the conflicts into one canonical record per configuration, and keeps a full audit trail of which source claimed what.

The scope is deliberately ambitious. That is the point.

---

## Status

**Phase 1 — three sources landed, six reconciliation passes live, first cars materialized.**

| | |
| --- | --- |
| Schema | 35 tables, 172 indexes, Postgres 17 + pgvector |
| Migrations | Alembic, head `ea565b7ea489` (16 revisions) |
| Sources | Wikidata (SPARQL), NHTSA vPIC (API), EPA fueleconomy.gov (bulk CSV) |
| Raw records | 78,691 landed, untransformed |
| Reconciler | 6 passes, deterministic and idempotent (v12) |
| Entities | 7,201 companies · 1,735 models · 151 generations · 18,751 catalogue periods · **23,523 configurations** |
| Provenance | 153,426 field-level assertions · 66,537 logged match decisions |
| Tests | 145, against a real Postgres |
| API / frontend | Not started |

Numbers from `scripts/status.py` on 2026-08-03. The database is 369 MB.

### What that actually means

There are real cars in here, reachable end to end from a government filing:

```
mercedes-benz / amg-gt / 2019 / "S Coupe" / RWD
  4000 cc · 8 cylinders · 18.0 mpg combined
  slug: mercedes-benz-amg-gt-2019-s-coupe-rwd
```

Every one of those spec values carries a `field_provenance` row naming EPA as the source and pointing back at the exact raw CSV record it came from.

### What is deliberately not there yet

- **Generation placement is NULL on every configuration.** Placement is evidence-gated (ADR 0014) — it gets written only when a source places *that individual car*, never inferred from the model year, because one model year routinely contains two generations at once. The 2019 AMG GT is exactly that case: C190 coupes beside X290 4-doors. The evidence pass is the next design turn.
- **Engine and transmission entities are empty.** EPA cannot name an engine (ADR 0015), so powertrain data lands as facts on the configuration and the entities wait for a source that actually names hardware.
- **Coverage is partial and the residue is queued, not hidden.** 134 of 247 vPIC makes are matched to companies; 42,920 of 49,995 EPA rows are attached; 477 of 14,524 swept Wikidata model entities correspond to a model. 4,188 open review flags carry the rest, each one a specific question with candidates attached.
- **No read API, no frontend.** `status.py` and SQL are the only query surfaces.

---

## How it works

```
Wikidata (SPARQL) ─┐                             ┌─► companies
NHTSA vPIC (API)  ─┼─► raw_scrape.raw_records ──►│   models ── model_lines
EPA bulk CSV      ─┘   (untransformed, hashed)   │   generations
                                ▲                │   catalogue_periods
                                │                └─► configurations
                          raw_record_id                    │
                                └────────────────── field_provenance
```

**Raw records land before anything interprets them.** Every fact carries a `raw_record_id` back to the exact scrape that produced it, so when the matcher improves, facts are re-derived from stored payloads instead of re-scraped. Re-reconciliation is the normal case, not the exception.

**Provenance attaches to facts, not identity.** A row in `companies` is identity — it exists or it doesn't. The claim "BMW was founded in 1916" is a *fact*, recorded per field in `field_provenance` with its source and timestamp. The entity column holds the reconciled winner. A source changing its mind is supersession; a source going quiet is a tombstone. Nothing is overwritten in place.

**Matching is precision-first and never fuzzy-automatic.** Each pass climbs a ladder: existing external id → curated registry of recorded human judgments → unique exact-normalized name match → a review flag with trigram candidates. Nothing auto-accepts on similarity. Under-matching is the cheap error — edit a policy list, re-run, the row appears — while a wrong merge has to be unwound from data that already references it.

**Every attempt is logged.** `match_decisions` records the rung, method, and outcome for all 66,537 processed records, and every flag close records *why*. That log is the labeled set a measured matcher will eventually be trained and evaluated against.

### Design decisions worth reading

The interesting parts of this project are the modelling problems, documented as ADRs in [`docs/decisions/`](docs/decisions/):

- [0002 — Entity/fact split and field-level provenance](docs/decisions/0002-entity-fact-split-and-field-provenance.md) — one `source_id` per row cannot express NHTSA→body, EPA→mpg, Wikidata→dimensions on the same car.
- [0004 — Raw record retention](docs/decisions/0004-raw-record-retention.md) — "never delete" is the wrong rule; retention is tiered by whether the data can be fetched again.
- [0005 — What counts as a make](docs/decisions/0005-what-counts-as-a-make.md) — Alpina, Ruf, Singer, and Zagato each break a different naive definition.
- [0006 — One `companies` table](docs/decisions/0006-companies-not-makes.md) — "make" is a role, not a table, because Alpina is both a manufacturer and a builder.
- [0007 — Reconciler policy and the first pass](docs/decisions/0007-reconciler-policy-and-first-pass.md) — strict admission with quarantine; the first version admitted anything with generic corporate classes and let in 2,175 seatbelt suppliers and glass-repair chains.
- [0011 — As-filed models, and why series are not entities](docs/decisions/0011-as-filed-models-and-series-lines.md) — "3 Series" is an aggregation over models, not a sixth level.
- [0013 — Name-form evidence ranks](docs/decisions/0013-name-form-evidence-ranks.md) — a label says what an entity *is*; aliases say what it is *also called*, and Wikidata files rebadges there. Ranking them apart recovered 90 true matches and caught one live miscategorization.
- [0014 — Year pass and EPA attach](docs/decisions/0014-year-pass-and-epa-attach.md) — the hierarchy is a goal per car, not a form every car must fill.
- [0015 — EPA powertrain facts, not entities](docs/decisions/0015-epa-powertrain-facts-not-entities.md) — EPA's `trany` field names zero gearboxes, so no bucket word gets to erase the difference between a single- and dual-clutch automated manual.

---

## Running it

Requires Docker and Python 3.11+.

```bash
# 1. database (Postgres 17 + pgvector; does not auto-start on boot)
docker compose -f infra/docker-compose.yml up -d

# 2. python environment
python -m venv .venv
.venv/bin/pip install -e '.[dev]'

# 3. schema
.venv/bin/alembic upgrade head

# 4. reference data the reconciler joins against
.venv/bin/python scripts/pipeline/seed_countries.py
```

Then check the live state at any time:

```bash
.venv/bin/python scripts/status.py
```

### Filling it

Each source is a fetch-and-land step followed by a reconciliation pass. Every step is idempotent — unchanged payloads land as no-ops, and a second reconciliation run over unchanged data settles to exact zero writes. Run them in this order; later passes depend on earlier ones having resolved identity.

```bash
P=.venv/bin/python

# companies, from Wikidata (~7,200 entities, seconds)
$P scripts/pipeline/ingest_wikidata_makes.py   && $P scripts/pipeline/reconcile_companies.py

# cross-source identity: vPIC makes matched to those companies
$P scripts/pipeline/ingest_vpic_makes.py       && $P scripts/pipeline/reconcile_vpic_makes.py

# nameplates under matched makes (~500 requests, several minutes)
$P scripts/pipeline/ingest_vpic_models.py      && $P scripts/pipeline/reconcile_vpic_models.py

# generations and model lines, from the Wikidata models sweep
$P scripts/pipeline/ingest_wikidata_models.py  && $P scripts/pipeline/reconcile_wikidata_models.py

# the year spine (one request per make/year — the full backfill runs ~3 hours)
$P scripts/pipeline/ingest_vpic_model_years.py && $P scripts/pipeline/reconcile_vpic_years.py

# configurations, from the EPA bulk CSV (one request, ~50k rows)
$P scripts/pipeline/ingest_epa_vehicles.py     && $P scripts/pipeline/reconcile_epa_vehicles.py
```

`scripts/pipeline/` holds these re-runnable steps. `scripts/decisions/` holds the applied-judgment scripts — the executable half of an ADR, run once against live data and kept as the record of what was done.

### Tests

The suite runs against a **real Postgres**, not a mock. It builds its own `carmanac_test` database from scratch through the full Alembic chain on every session, so every run is also a from-scratch migration test. The dev `carmanac` database is never touched.

```bash
.venv/bin/pytest                       # everything (needs the docker Postgres running)
.venv/bin/pytest -m "not integration"  # pure unit tests only, no database
```

---

## Layout

```
carmanac/
  db/models/        SQLAlchemy models — the source of schema truth
  ingest/           per-source fetch + land (wikidata/, vpic/, epa/)
  reconcile/        the engine, the per-source passes, and policy.py
alembic/versions/   migrations (hand-reviewed; some hand-written)
docs/decisions/     ADRs — the design rationale
scripts/pipeline/   re-runnable ingest and reconcile steps
scripts/decisions/  applied one-off judgments, kept as record
infra/              docker-compose + Postgres bootstrap
```

[`docs/charter.md`](docs/charter.md) holds the mission, architecture invariants, and conventions. [`PROGRESS.md`](PROGRESS.md) is the working log — current focus, open questions, and session notes, with older entries in [`docs/progress-archive/`](docs/progress-archive/).

> **Note:** `docs/schema.md` and `docs/schema_phase1.sql` predate ADRs 0002–0006 and are **stale**. The SQLAlchemy models in `carmanac/db/models/` are the source of schema truth; a rewrite or a generated-from-models DDL is owed.

## Stack

Postgres 17 + pgvector · SQLAlchemy 2.0 · Alembic · httpx · Python 3.11+ · ruff · pytest

Planned: FastAPI (read API), Next.js + Tailwind (frontend), Dagster (orchestration, once continuously-updating sources arrive), Scrapy (Tier 2/3 HTML sources).

## Scope

**In:** production passenger vehicles, all markets and eras, including defunct marques, JDM and Euro-only configurations, and coachbuilt or restomodded cars.

**Out for now:** commercial vehicles above class 3, motorcycles, pricing beyond original MSRP, user accounts.

## License

Copyright © 2026 Gaurav Deshmukh. Licensed under the **GNU AGPL-3.0** — see [LICENSE](LICENSE).

The network clause matters here: if you run a modified version of this as a public service, you have to publish your source. Reading the code, learning from it, and self-hosting it unchanged are all fine.

Note that the licence covers the *code*. Ingested data carries whatever terms its own source imposes — Wikidata is CC0, other sources vary.
