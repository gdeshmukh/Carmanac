"""Alembic migration environment.

The database URL comes from `carmanac.config.settings` (env var `CARMANAC_DATABASE_URL`,
or a local `.env`), never from `alembic.ini`. That way migrations and the
application can never drift onto different databases.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from carmanac.config import settings

# Importing the models package is what registers every table on Base.metadata.
# Without it autogenerate would compare the live database against an EMPTY
# metadata and cheerfully emit a migration that drops the whole schema.
from carmanac.db.models import Base

config = context.config

# Inject the real URL over the placeholder in alembic.ini. The `%` escape
# matters because ConfigParser treats it as interpolation syntax, and it is
# legal in a URL-encoded password.
config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting ('alembic upgrade head --sql')."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        # Reflect non-default schemas too (we use `raw_scrape`). Without this,
        # autogenerate cannot see raw_scrape.* and would wrongly try to
        # re-create those tables on every run.
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Connect and run migrations against the live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # Detect column type and server-default changes, not just
            # added/dropped tables and columns. Off by default in Alembic.
            compare_type=True,
            compare_server_default=True,
            # Reflect non-default schemas too (we use `raw_scrape`). Without
            # this, autogenerate cannot see raw_scrape.* and would wrongly try
            # to re-create those tables on every run.
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
