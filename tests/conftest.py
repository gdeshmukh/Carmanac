"""Test database lifecycle.

Integration tests run against a REAL Postgres - the same docker container as
dev - but never against the dev `carmanac` database. A dedicated
`carmanac_test` database is (re)built from scratch each test session by
running the actual Alembic migration chain, which makes every session a
migration test before the first real test runs.

The test database name is overridable via CARMANAC_TEST_DB_NAME so that
parallel checkouts (worktrees, CI shards) can run without clobbering each
other's databases on a shared Postgres.

Isolation model: TRUNCATE between tests, not transaction rollback - because
`land_makes` commits internally (it is a real ingestion entry point, and the
tests exercise the real code path). Lookup seed rows are recreated per test by
the fixtures that need them.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from carmanac.config import settings
from carmanac.db.models import Source

REPO_ROOT = Path(__file__).resolve().parent.parent

TEST_DB_NAME = os.environ.get("CARMANAC_TEST_DB_NAME", "carmanac_test")

# What infra/initdb/00_extensions.sql does on first container boot, replayed
# here because CREATE DATABASE does not copy extensions or schemas.
_BOOTSTRAP_SQL = (
    "CREATE EXTENSION IF NOT EXISTS vector",
    "CREATE EXTENSION IF NOT EXISTS pg_trgm",
    "CREATE SCHEMA IF NOT EXISTS raw_scrape",
)


def _swap_database(url: str, db_name: str) -> str:
    """Point a postgres URL at a different database on the same server."""
    base, _, _ = url.rpartition("/")
    return f"{base}/{db_name}"


def create_test_database(db_name: str) -> str:
    """Drop, recreate, bootstrap, and migrate a test database. Returns its URL.

    Dropping first makes every session start from nothing, so the Alembic
    chain is exercised from the baseline on every run - stale state from a
    crashed previous session cannot leak in.
    """
    admin = create_engine(
        _swap_database(settings.database_url, "postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{db_name}"'))
    admin.dispose()

    url = _swap_database(settings.database_url, db_name)
    bootstrap = create_engine(url, isolation_level="AUTOCOMMIT")
    with bootstrap.connect() as conn:
        for stmt in _BOOTSTRAP_SQL:
            conn.execute(text(stmt))
    bootstrap.dispose()

    run_alembic(url, "upgrade", "head")
    return url


def run_alembic(database_url: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Run alembic as a subprocess against a specific database.

    A subprocess rather than the command API because env.py builds its URL
    from `carmanac.config.settings` at import - a fresh process picks up the
    override cleanly, exactly the way CI and the shell do it.
    """
    result = subprocess.run(
        # The running interpreter's alembic, not whatever `alembic` is on
        # PATH - the two can be different installs with different deps.
        [sys.executable, "-m", "alembic", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "CARMANAC_DATABASE_URL": database_url},
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"alembic {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return result


@pytest.fixture(scope="session")
def test_db_url() -> str:
    """Session-scoped: the migrated test database's URL."""
    return create_test_database(TEST_DB_NAME)


@pytest.fixture(scope="session")
def engine(test_db_url: str) -> Iterator[Engine]:
    engine = create_engine(test_db_url, future=True)
    yield engine
    engine.dispose()


@pytest.fixture()
def db(engine: Engine) -> Iterator[Session]:
    """A session against a clean database state.

    Truncates AFTER each test rather than before, so a failing test leaves its
    state behind in `carmanac_test` for post-mortem inspection. The TRUNCATE
    lists every table tests write to, in one statement so CASCADE resolves the
    FK ordering; lookups seeded by migrations (market_regions, company_roles,
    derivation_types) are deliberately NOT truncated - they are part of the
    migrated schema, and tests may rely on them exactly like production code.
    """
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as session:
        yield session
        session.rollback()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                TRUNCATE raw_scrape.raw_records,
                         external_ids, field_provenance,
                         configuration_attributes, configuration_engines,
                         configuration_transmissions, company_role_assignments,
                         vehicle_derivations, media_attachments, media_assets,
                         configurations, model_years, generations, models,
                         engines, transmissions, companies, sources
                RESTART IDENTITY CASCADE
                """
            )
        )


@pytest.fixture()
def wikidata_source(db: Session) -> Source:
    """The `sources` row landing requires (get_source refuses to invent it)."""
    source = db.scalar(select(Source).where(Source.name == "Wikidata"))
    if source is None:
        source = Source(name="Wikidata", tier=1, base_url="https://www.wikidata.org")
        db.add(source)
        db.commit()
    return source
