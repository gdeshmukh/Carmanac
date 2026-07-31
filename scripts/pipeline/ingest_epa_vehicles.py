"""Download and land the EPA fueleconomy.gov bulk vehicle CSV.

One request, ~50k per-variant rows since 1984. Landing only; the EPA
write pass (model matching, configuration rows) gets its own ADR.
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.ingest.epa.bulk import land_vehicles

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        result = land_vehicles(session)
    print(f"Landed {result.fetched} vehicle rows ({result.inserted} new).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
