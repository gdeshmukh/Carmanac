"""HTTP client for the NHTSA vPIC REST API.

Plain GET endpoints returning JSON envelopes of the shape:

    {"Count": n, "Message": ..., "SearchCriteria": ..., "Results": [...]}

This client knows the envelope, not the endpoints - which paths to call and
what to do with `Results` lives in `land.py`. Transport (honest user agent,
rate limiting, retry with backoff) is `carmanac.ingest.http.PoliteClient`,
shared with the SPARQL client.
"""

from __future__ import annotations

from typing import Any

from carmanac.config import settings
from carmanac.ingest.http import IngestHTTPError, PoliteClient


class VpicError(IngestHTTPError):
    """A vPIC request failed in a way retrying will not fix."""


class VpicClient(PoliteClient):
    """Rate-limited vPIC API client.

    Usage:

        client = VpicClient()
        makes = client.get_results("GetMakesForVehicleType/car")
    """

    def __init__(
        self,
        base_url: str | None = None,
        user_agent: str | None = None,
        min_interval: float | None = None,
        timeout: float | None = None,
        max_retries: int = 4,
    ) -> None:
        self.base_url = (base_url or settings.vpic_api_base).rstrip("/")
        super().__init__(
            user_agent=user_agent,
            min_interval=min_interval,
            timeout=timeout,
            max_retries=max_retries,
            headers={"Accept": "application/json"},
        )

    def get_results(self, path: str, **params: str) -> list[dict[str, Any]]:
        """GET an endpoint and return its `Results` list.

        Raises `VpicError` on transport failure or a malformed envelope - a
        response without `Results` means the API contract changed, which is
        worth failing loudly over rather than landing an empty run.
        """
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = self.request("GET", url, params={"format": "json", **params})
        except VpicError:
            raise
        except IngestHTTPError as exc:
            raise VpicError(str(exc)) from exc

        body = response.json()
        results = body.get("Results")
        if not isinstance(results, list):
            raise VpicError(f"{url}: response has no Results list (keys: {sorted(body)})")
        return results
