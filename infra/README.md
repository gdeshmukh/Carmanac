# Local database (`infra/`)

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
cd infra
docker compose up -d
```

First run downloads the image (~once) and initializes the database: it creates
the `carmanac` user and database, and runs `initdb/00_extensions.sql` to enable
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
| user | `carmanac` |
| password | `carmanac_dev_password` |
| database | `carmanac` |

Connection URL:

```
postgresql://carmanac:carmanac_dev_password@localhost:5432/carmanac
```

If you have `psql` installed locally:

```bash
psql postgresql://carmanac:carmanac_dev_password@localhost:5432/carmanac
```

No local `psql`? Use the one inside the container:

```bash
docker compose exec db psql -U carmanac -d carmanac
```

Quick sanity check once connected — confirm the extensions loaded:

```sql
\dx
-- should list: vector, pg_trgm (plus plpgsql)
```

## Stop it

```bash
docker compose down       # stops the container, KEEPS your data
```

Your data lives in a named Docker volume (`carmanac_pgdata`), so a plain `down` and
later `up` picks up exactly where you left off.

**Deleting the volume (`docker compose down -v`) destroys the entire database
permanently** — including the `raw_scrape` archive, which for Tier 3/4 sources
may hold the only copy that still exists anywhere (ADR 0004). Never run it
without a current backup:

```bash
../scripts/backup.sh      # dump, verify, rotate, upload off-machine
docker compose down -v    # only now is this survivable
```

## Backups

`scripts/backup.sh` (repo root) dumps the database from the running container,
verifies the dump is restorable, keeps a local rotation, and uploads to Google
Drive via `rclone`. Run it before anything destructive and before/after big
ingests. `--verify-restore` additionally restores into a scratch database —
run that variant occasionally; an unrestorable backup is not a backup.

## Troubleshooting

**Port 5432 already in use** — you (or another project) already run Postgres on
that port. In `docker-compose.yml`, change the ports line to `"5433:5432"` and
connect on `5433` instead. Only change the left number.

**Changed `initdb/` but nothing happened** — that folder only runs on a
*brand-new* database. To re-run it, reset with `docker compose down -v` — but
that deletes all data, so take a backup first (see Backups above).

**`healthy` never appears** — check `docker compose logs db` for an error,
usually a port conflict or not enough memory allocated to Docker.

## What this is NOT

No tables are created here. The five-level schema is owned by Alembic
migrations (next step). This folder only stands up the empty, extension-enabled
database that those migrations run against.
