"""Fetch and land vPIC model-year associations for every landed make.

One request per (make, year) at the polite rate - the full backfill over
all landed makes runs ~3 hours; future top-ups only need the newest year.
Commits per make, so an interrupted run resumes by re-running (already-
landed makes re-land as no-ops). Landing only; `model_year` catalogue
periods wait on the generation ADR.
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.ingest.vpic.years import land_model_years

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        result = land_model_years(session)
    print(f"Landed {result.fetched} model-year records ({result.inserted} new).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
