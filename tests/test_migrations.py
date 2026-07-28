"""Migration-chain tests.

The conftest session fixture already proves `upgrade head` works from an
empty database - every integration run does. These tests add the two checks
that have each caught a real defect in this repo's history:

- the full downgrade/upgrade round trip (hand-written downgrades have broken
  before review more than once)
- `alembic check` - models vs migrations drift. This is the check that
  exposed the missing `include_schemas` in env.py, without which autogenerate
  wanted to re-create raw_scrape.* on every run.
"""

from __future__ import annotations

import pytest

from tests.conftest import TEST_DB_NAME, create_test_database, run_alembic

pytestmark = pytest.mark.integration


def test_full_downgrade_upgrade_round_trip():
    """Empty -> head -> base -> head, on a scratch database so the session
    database's state stays untouched for the other tests."""
    url = create_test_database(f"{TEST_DB_NAME}_roundtrip")
    run_alembic(url, "downgrade", "base")
    run_alembic(url, "upgrade", "head")


def test_models_and_migrations_agree(test_db_url):
    """`alembic check` exits nonzero if autogenerate would emit anything -
    i.e. if the SQLAlchemy models (the source of schema truth) and the
    migration chain have drifted apart."""
    result = run_alembic(test_db_url, "check")
    assert "No new upgrade operations detected" in result.stdout
