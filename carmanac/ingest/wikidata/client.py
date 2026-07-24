"""HTTP client for the Wikidata SPARQL endpoint.

SPARQL is the query language for Wikidata. You POST a query string and get back
JSON; the interesting part of the response is `results.bindings`, a list of rows
where each row maps a variable name (`?item`, `?itemLabel`) to a typed value.

This client deliberately knows nothing about cars. It sends a query and returns
the parsed response - what to ask for lives in `queries.py`, what to do with the
answer lives in `land.py`. Keeping the transport separate means the retry and
rate-limit behaviour is written once and reused by every future SPARQL ingest
(models, generations, engines).

Three politeness behaviours, all required by CLAUDE.md's scraping rules:

- **Honest identification.** Wikimedia's user-agent policy asks for a
  descriptive agent with contact info; anonymous or spoofed agents get blocked.
- **Rate limiting.** A minimum interval between requests, enforced here rather
  than remembered at each call site.
- **Backoff on rejection.** 429 (too many requests) and 5xx are retried with
  exponential backoff, honouring `Retry-After` when the server sends it. A 4xx
  that is not 429 is a bug in our query, so it fails immediately rather than
  hammering the endpoint with a request that will never succeed.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from carmanac.config import settings

log = logging.getLogger(__name__)

# Retried with backoff. Everything else 4xx is our fault and fails fast.
_RETRY_STATUS = {429, 500, 502, 503, 504}


class SparqlError(RuntimeError):
    """A SPARQL request failed in a way retrying will not fix."""


class SparqlClient:
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
        self.user_agent = user_agent or settings.user_agent
        self.min_interval = (
            min_interval if min_interval is not None else settings.request_min_interval_seconds
        )
        self.timeout = timeout if timeout is not None else settings.request_timeout_seconds
        self.max_retries = max_retries

        # monotonic(), not time(): immune to the wall clock being adjusted
        # mid-run, which would otherwise make the limiter wait forever or not
        # at all. Starts at None so the first request is not delayed.
        self._last_request_at: float | None = None

        self._client = httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/sparql-results+json",
            },
            follow_redirects=True,
        )

    # -- context manager so callers can guarantee the connection is closed ----

    def __enter__(self) -> SparqlClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # -- internals ------------------------------------------------------------

    def _throttle(self) -> None:
        """Block until `min_interval` has elapsed since the last request."""
        if self._last_request_at is None:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)

    @staticmethod
    def _retry_delay(response: httpx.Response | None, attempt: int) -> float:
        """Seconds to wait before retry `attempt` (0-based).

        Prefers the server's `Retry-After` when present - it knows better than
        our guess - and otherwise backs off exponentially: 2s, 4s, 8s, 16s.
        """
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return float(retry_after)
                except ValueError:
                    # Retry-After may also be an HTTP date. Not worth parsing;
                    # fall through to the exponential schedule.
                    pass
        return 2.0 ** (attempt + 1)

    # -- public API -----------------------------------------------------------

    def query(self, sparql: str) -> dict[str, Any]:
        """Run a SPARQL query and return the parsed JSON response.

        Raises `SparqlError` if the query is rejected, or if every retry is
        exhausted.
        """
        last_error: str = "no attempts made"

        for attempt in range(self.max_retries):
            self._throttle()
            response: httpx.Response | None = None
            try:
                # POST, not GET: queries comfortably exceed practical URL
                # length limits once they carry OPTIONAL blocks.
                response = self._client.post(self.endpoint, data={"query": sparql})
                self._last_request_at = time.monotonic()

                if response.status_code == 200:
                    return response.json()

                if response.status_code not in _RETRY_STATUS:
                    # A malformed query returns 400 with the parser error in the
                    # body - surface it, because that is the actual bug.
                    raise SparqlError(
                        f"SPARQL query rejected with HTTP {response.status_code}: "
                        f"{response.text[:500]}"
                    )

                last_error = f"HTTP {response.status_code}"

            except httpx.TimeoutException as exc:
                self._last_request_at = time.monotonic()
                last_error = f"timeout after {self.timeout}s ({exc!r})"
            except httpx.HTTPError as exc:
                self._last_request_at = time.monotonic()
                last_error = f"transport error ({exc!r})"

            if attempt < self.max_retries - 1:
                delay = self._retry_delay(response, attempt)
                log.warning(
                    "SPARQL attempt %d/%d failed (%s); retrying in %.1fs",
                    attempt + 1,
                    self.max_retries,
                    last_error,
                    delay,
                )
                time.sleep(delay)

        raise SparqlError(
            f"SPARQL query failed after {self.max_retries} attempts. Last error: {last_error}"
        )
