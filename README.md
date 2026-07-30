# Carmanac

A relational database of **every production passenger vehicle ever made, globally** — every make, model, generation, model year, and configuration, across all markets and eras.

Vehicle data is scattered across government APIs, manufacturer archives, enthusiast wikis, and scanned brochures, and no two sources agree. Carmanac ingests them continuously, reconciles the conflicts into one canonical record per configuration, and keeps a full audit trail of which source claimed what.

The scope is deliberately ambitious. That is the point.

---

## Status

**Phase 1 — schema live, first source landing.**

| | |
| --- | --- |
| Schema | 27 tables, 121 indexes, Postgres 17 + pgvector |
| Migrations | Alembic, head `5cbf6be81036` |
| Data landed | 7,222 companies and marques from Wikidata, raw |
| Reconciler | Not built — next up |
| API / frontend | Not started |

Ingestion currently **lands raw source records only**. Nothing is reconciled into entities yet, so there is no queryable vehicle catalogue — just one demo vehicle from the seed script. See [`PROGRESS.md`](PROGRESS.md) for the living state.

---

## How it works

```
Wikidata (SPARQL)  ─┐
NHTSA vPIC         ─┼─►  raw_scrape.raw_records  ─►  reconciler  ─►  companies
EPA fueleconomy    ─┘    (untransformed, hashed)      (next)          models
                                                                      generations
                                                                      model_years
                                                                      configurations
```

**Raw records land before anything interprets them.** Every fact carries a `raw_record_id` back to the exact scrape that produced it, so when the matcher improves, facts can be re-derived from stored payloads instead of re-scraping.

**Provenance attaches to facts, not identity.** A row in `companies` is identity — it exists or it doesn't. The claim "BMW was founded in 1916" is a *fact*, recorded per field in `field_provenance` along with which source said so and when. The entity column holds the reconciled winner.

**Conflicts resolve by source tier**, then by field affinity — EPA owns fuel economy, NHTSA owns body and safety, Wikidata owns identity — then get flagged for review.

### Design decisions worth reading

The interesting parts of this project are the modelling problems, documented as ADRs:

- [0002 — Entity/fact split and field-level provenance](docs/decisions/0002-entity-fact-split-and-field-provenance.md) — one `source_id` per row cannot express NHTSA→body, EPA→mpg, Wikidata→dimensions on the same car.
- [0003 — Raw landing zone and external IDs](docs/decisions/0003-raw-landing-zone-and-external-ids.md) — why a `wikidata_qid` column doesn't generalise to a fourth source.
- [0004 — Raw record retention](docs/decisions/0004-raw-record-retention.md) — "never delete" is the wrong rule; retention is tiered by whether the data can be fetched again.
- [0005 — What counts as a make](docs/decisions/0005-what-counts-as-a-make.md) — Alpina, Ruf, Singer, and Zagato each break a different naive definition.
- [0006 — One `companies` table](docs/decisions/0006-companies-not-makes.md) — "make" is a role, not a table, because Alpina is both a manufacturer and a builder.

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
```

To pull real data:

```bash
.venv/bin/python scripts/ingest_wikidata_makes.py
```

Fetches every automobile manufacturer and car brand from Wikidata (~7,200 entities, a few seconds) into the raw landing zone. Safe to re-run — unchanged payloads are rejected by a content hash, and genuinely changed ones land beside the old version as history.

---

## Layout

```
carmanac/
  db/models/        SQLAlchemy models — the source of schema truth
  ingest/wikidata/  SPARQL client, queries, and landing logic
alembic/versions/   migrations (hand-reviewed; some hand-written)
docs/decisions/     ADRs
infra/              docker-compose + Postgres bootstrap
scripts/            seed and ingest entry points
```

[`docs/charter.md`](docs/charter.md) holds the mission, architecture invariants, and conventions; `PROGRESS.md` is the working log, including the pre-reconciler schema review (R1–R12).

## Stack

Postgres 17 + pgvector · SQLAlchemy 2.0 · Alembic · httpx · Python 3.11+

Planned: FastAPI (read API), Next.js + Tailwind (frontend), Dagster (orchestration, once there is more than one pipeline to order), Scrapy (Tier 2/3 HTML sources).

## Scope

**In:** production passenger vehicles, all markets and eras, including defunct marques, JDM and Euro-only configurations, and coachbuilt or restomodded cars.

**Out for now:** commercial vehicles above class 3, motorcycles, pricing beyond original MSRP, user accounts.

## License

Copyright © 2026 Gaurav Deshmukh. Licensed under the **GNU AGPL-3.0** — see [LICENSE](LICENSE).

The network clause matters here: if you run a modified version of this as a public service, you have to publish your source. Reading the code, learning from it, and self-hosting it unchanged are all fine.

Note that the licence covers the *code*. Ingested data carries whatever terms its own source imposes — Wikidata is CC0, other sources vary.
