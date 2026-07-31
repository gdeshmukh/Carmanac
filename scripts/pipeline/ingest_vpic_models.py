"""Fetch-and-land vPIC passenger-vehicle models - the first model-level data.

Lands raw records only (one per vPIC ModelId, `model:<id>`), for every landed
make - matched or not, since models under unmatched makes are evidence the
match queue is waiting on. Turning them into `models` rows is the
reconciler's job, pending the models-pass ADR.

~500 requests at the polite rate: expect several minutes.
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.ingest.vpic.models import land_passenger_models

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        result = land_passenger_models(session)
    print(
        f"vPIC models: {result.fetched} fetched, {result.inserted} new, "
        f"{result.unchanged} unchanged"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
