"""Application settings.

Values come from the environment (prefix `CARMANAC_`) or a local `.env` file, which
is gitignored. The default points at the local docker-compose database in
`db/` - safe to commit precisely because it is a throwaway local dev instance,
never production.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CARMANAC_", env_file=".env", extra="ignore")

    # psycopg3 driver. Credentials match db/docker-compose.yml.
    database_url: str = (
        "postgresql+psycopg://carmanac:carmanac_dev_password@localhost:5432/carmanac"
    )


settings = Settings()
