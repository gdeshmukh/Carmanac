"""Fetch every Wikidata automobile manufacturer into the raw landing zone.

    .venv/bin/python scripts/ingest_wikidata_makes.py

Lands raw records only - no `makes` rows are created and nothing is reconciled.
Safe to re-run: an unchanged payload is rejected by the hash constraint, so a
second run inserts nothing.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.ingest.wikidata import SparqlError, land_makes


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        with SessionLocal() as session:
            result = land_makes(session)
    except SparqlError as exc:
        print(f"\nFetch failed: {exc}", file=sys.stderr)
        return 1
    except LookupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print(
        f"\nFetched {result.fetched} manufacturer(s) from Wikidata.\n"
        f"  {result.inserted} new raw record(s) landed\n"
        f"  {result.unchanged} unchanged (already stored with an identical payload)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
