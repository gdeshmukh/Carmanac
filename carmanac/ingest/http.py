"""Shared polite HTTP transport for every ingest client.

Extracted from the Wikidata SPARQL client the day the second source (vPIC)
arrived and would otherwise have copy-pasted it. One place owns the three
politeness behaviours the charter's scraping rules require:

- **Honest identification** - the descriptive user agent with contact info.
- **Rate limiting** - a minimum interval between requests, enforced here
  rather than remembered at each call site.
- **Backoff on rejection** - 429 and 5xx retry with exponential backoff,
  honouring `Retry-After`; any other 4xx is our bug and fails immediately
  rather than hammering the endpoint with a request that will never succeed.

Per-source clients (SPARQL, vPIC REST) wrap this with their protocol shape;
this module knows nothing about queries or cars.
"""

from __future__ import annotations

import logging
import time

import httpx

from carmanac.config import settings

log = logging.getLogger(__name__)

# Retried with backoff. Everything else 4xx is our fault and fails fast.
RETRY_STATUS = {429, 500, 502, 503, 504}


class IngestHTTPError(RuntimeError):
    """A request failed in a way retrying will not fix (or retries ran out)."""


class PoliteClient:
    """Rate-limited, self-identifying HTTP transport with retry."""

    def __init__(
        self,
        user_agent: str | None = None,
        min_interval: float | None = None,
        timeout: float | None = None,
        max_retries: int = 4,
        headers: dict[str, str] | None = None,
    ) -> None:
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
            headers={"User-Agent": self.user_agent, **(headers or {})},
            follow_redirects=True,
        )

    def __enter__(self) -> PoliteClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

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

    def request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """One throttled, retried request. Returns the 200 response.

        Raises `IngestHTTPError` on a non-retryable status (with the body,
        because that is the actual bug) or when retries are exhausted.
        """
        last_error = "no attempts made"

        for attempt in range(self.max_retries):
            self._throttle()
            response: httpx.Response | None = None
            try:
                response = self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
                self._last_request_at = time.monotonic()

                if response.status_code == 200:
                    return response

                if response.status_code not in RETRY_STATUS:
                    raise IngestHTTPError(
                        f"{method} {url} rejected with HTTP {response.status_code}: "
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
                    "%s %s attempt %d/%d failed (%s); retrying in %.1fs",
                    method,
                    url,
                    attempt + 1,
                    self.max_retries,
                    last_error,
                    delay,
                )
                time.sleep(delay)

        raise IngestHTTPError(
            f"{method} {url} failed after {self.max_retries} attempts. Last error: {last_error}"
        )
