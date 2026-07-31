"""Fetch the Wikidata models sweep into the raw landing zone (ADR 0012 §1).

    .venv/bin/python scripts/ingest_wikidata_models.py

Lands raw records only - no models, lines or generations are created and
nothing is reconciled. Safe to re-run: commits per batch, unchanged payloads
re-land as no-ops, so a crashed sweep resumes by running it again.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.ingest.wikidata import SparqlError, land_models


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        with SessionLocal() as session:
            result = land_models(session)
    except SparqlError as exc:
        print(f"\nFetch failed: {exc}", file=sys.stderr)
        return 1
    except LookupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print(
        f"\nFetched {result.fetched} model-shaped entit(ies) from Wikidata.\n"
        f"  {result.inserted} new raw record(s) landed\n"
        f"  {result.unchanged} unchanged (already stored with an identical payload)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
