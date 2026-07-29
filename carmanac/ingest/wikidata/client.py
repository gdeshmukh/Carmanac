"""HTTP client for the Wikidata SPARQL endpoint.

SPARQL is the query language for Wikidata. You POST a query string and get back
JSON; the interesting part of the response is `results.bindings`, a list of rows
where each row maps a variable name (`?item`, `?itemLabel`) to a typed value.

This client deliberately knows nothing about cars. It sends a query and returns
the parsed response - what to ask for lives in `queries.py`, what to do with the
answer lives in `land.py`. The transport (honest user agent, rate limiting,
retry with backoff) lives in `carmanac.ingest.http.PoliteClient`, shared with
every other source's client.
"""

from __future__ import annotations

from typing import Any

from carmanac.config import settings
from carmanac.ingest.http import IngestHTTPError, PoliteClient


class SparqlError(IngestHTTPError):
    """A SPARQL request failed in a way retrying will not fix."""


class SparqlClient(PoliteClient):
    """A rate-limited, self-identifying SPARQL client.

    Usage:

        client = SparqlClient()
        data = client.query(MAKES_QUERY)
        rows = data["results"]["bindings"]
    """

    def __init__(
        self,
        endpoint: str | None = None,
        user_agent: str | None = None,
        min_interval: float | None = None,
        timeout: float | None = None,
        max_retries: int = 4,
    ) -> None:
        self.endpoint = endpoint or settings.wikidata_sparql_endpoint
        super().__init__(
            user_agent=user_agent,
            min_interval=min_interval,
            timeout=timeout,
            max_retries=max_retries,
            headers={"Accept": "application/sparql-results+json"},
        )

    def query(self, sparql: str) -> dict[str, Any]:
        """Run a SPARQL query and return the parsed JSON response.

        Raises `SparqlError` if the query is rejected, or if every retry is
        exhausted. POST, not GET: queries comfortably exceed practical URL
        length limits once they carry OPTIONAL blocks.
        """
        try:
            return self.request("POST", self.endpoint, data={"query": sparql}).json()
        except SparqlError:
            raise
        except IngestHTTPError as exc:
            raise SparqlError(str(exc)) from exc
