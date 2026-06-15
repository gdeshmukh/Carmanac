# Local database (`db/`)

Local Postgres + pgvector for development, via Docker. This is a disposable,
reproducible database — not production. Everyone who runs it gets an identical
setup.

## Prerequisites

Docker Desktop (Mac/Windows) or Docker Engine + the compose plugin (Linux).
Check it's working:

```bash
docker --version
docker compose version
```

## Start it

```bash
cd db
docker compose up -d
```

First run downloads the image (~once) and initializes the database: it creates
the `gvd` user and database, and runs `initdb/00_extensions.sql` to enable
`vector` and `pg_trgm` and create the `raw_scrape` schema.

Check it's healthy:

```bash
docker compose ps        # STATUS should say "healthy" after a few seconds
docker compose logs db   # see what it did on startup
```

## Connect

Credentials (local dev only):

| | |
|---|---|
| host | `localhost` |
| port | `5432` |
| user | `gvd` |
| password | `gvd_dev_password` |
| database | `gvd` |

Connection URL:

```
postgresql://gvd:gvd_dev_password@localhost:5432/gvd
```

If you have `psql` installed locally:

```bash
psql postgresql://gvd:gvd_dev_password@localhost:5432/gvd
```

No local `psql`? Use the one inside the container:

```bash
docker compose exec db psql -U gvd -d gvd
```

Quick sanity check once connected — confirm the extensions loaded:

```sql
\dx
-- should list: vector, pg_trgm (plus plpgsql)
```

## Stop it

```bash
docker compose down       # stops the container, KEEPS your data
docker compose down -v     # stops AND deletes all data (full reset)
```

Your data lives in a named Docker volume (`gvd_pgdata`), so a plain `down` and
later `up` picks up exactly where you left off.

## Troubleshooting

**Port 5432 already in use** — you (or another project) already run Postgres on
that port. In `docker-compose.yml`, change the ports line to `"5433:5432"` and
connect on `5433` instead. Only change the left number.

**Changed `initdb/` but nothing happened** — that folder only runs on a
*brand-new* database. To re-run it, reset: `docker compose down -v && docker
compose up -d`. (Safe right now; there's no real data yet.)

**`healthy` never appears** — check `docker compose logs db` for an error,
usually a port conflict or not enough memory allocated to Docker.

## What this is NOT

No tables are created here. The five-level schema is owned by Alembic
migrations (next step). This folder only stands up the empty, extension-enabled
database that those migrations run against.
