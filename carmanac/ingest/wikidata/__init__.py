"""Wikidata ingestion (Tier 1).

Wikidata is the first source deliberately: it is openly licensed (CC0, so no
ToS exposure while volume is small), it is queryable in bulk via SPARQL instead
of page-by-page crawling, and its QIDs are the universal join key that every
later source reconciles against.
"""

from carmanac.ingest.wikidata.client import SparqlClient, SparqlError
from carmanac.ingest.wikidata.land import LandResult, land_makes
from carmanac.ingest.wikidata.models import land_models
from carmanac.ingest.wikidata.queries import MAKES_QUERY, MODELS_DETAIL_QUERY, MODELS_QID_QUERY

__all__ = [
    "MAKES_QUERY",
    "MODELS_DETAIL_QUERY",
    "MODELS_QID_QUERY",
    "LandResult",
    "SparqlClient",
    "SparqlError",
    "land_makes",
    "land_models",
]
