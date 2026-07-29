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

    # --- ingestion -----------------------------------------------------------
    # Settings, not hard-coded literals: the charter forbids source URLs in
    # business logic. The `sources` table holds what a source *is*
    # (wikidata.org, tier 1); these hold how we *reach* it, which is an
    # operational detail and overridable per environment.
    wikidata_sparql_endpoint: str = "https://query.wikidata.org/sparql"
    vpic_api_base: str = "https://vpic.nhtsa.dot.gov/api/vehicles"

    # Wikimedia's user-agent policy requires a descriptive agent with a way to
    # contact the operator, so they can reach us instead of silently blocking.
    # Charter rule: "identify the scraper bot honestly in user-agent strings".
    user_agent: str = (
        "CarmanacBot/0.1 (https://github.com/gdeshmukh/Carmanac; deshmukhgaurav523@gmail.com)"
    )

    # Seconds between requests to one endpoint. The makes ingest is a single
    # query so this barely bites, but models/generations will paginate and the
    # limiter is what keeps that polite by default rather than by remembering.
    request_min_interval_seconds: float = 1.0
    request_timeout_seconds: float = 120.0


settings = Settings()
