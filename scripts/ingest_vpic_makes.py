"""Fetch-and-land vPIC passenger-vehicle makes.

Lands raw records only (one per vPIC MakeId). Matching them to `companies` -
and the corroboration that auto-resolves quarantine flags - is the
reconciler's job, pending the vPIC matching ADR.
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.ingest.vpic.land import land_passenger_makes

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        result = land_passenger_makes(session)
    print(
        f"vPIC makes: {result.fetched} fetched, {result.inserted} new, {result.unchanged} unchanged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
