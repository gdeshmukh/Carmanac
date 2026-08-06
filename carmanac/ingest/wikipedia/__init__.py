"""English-Wikipedia ingestion (ADR 0017).

Articles are reached through sitelinks recorded on already-matched QIDs -
identity is inherited from Wikidata, never inferred by name matching, which
is why this Tier 2 source does not cross the charter's matcher-precision
gate.
"""

from carmanac.ingest.wikipedia.infoboxes import (
    SOURCE_NAME,
    land_generation_infoboxes,
)

__all__ = ["SOURCE_NAME", "land_generation_infoboxes"]
