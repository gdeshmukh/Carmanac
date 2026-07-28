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
from carmanac.ingest.wikidata.coverage import KNOWN_MARQUES, missing_marques


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        with SessionLocal() as session:
            result = land_makes(session)
            missing = missing_marques(session)
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

    if missing:
        # Coverage losses are silent by nature - the rows simply are not
        # there - so a miss must be a loud, failing exit (coverage.py).
        print(
            f"\nCOVERAGE CHECK FAILED: {len(missing)} known marque(s) missing "
            f"from the landing zone:",
            file=sys.stderr,
        )
        for qid, name in missing:
            print(f"  {name} ({qid})", file=sys.stderr)
        print(
            "Triage: fix a fetch axis, pin the QID in queries.py, or move the "
            "entry to NOT_IN_WIKIDATA with a note.",
            file=sys.stderr,
        )
        return 2

    print(f"  coverage check: all {len(KNOWN_MARQUES)} known marques present")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
