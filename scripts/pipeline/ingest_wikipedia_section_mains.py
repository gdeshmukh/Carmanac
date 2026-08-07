"""Fetch the {{Main}} targets of undated section-born generations (ADR 0018 §3).

    .venv/bin/python scripts/pipeline/ingest_wikipedia_section_mains.py [--refresh]

Targets are the per-generation articles that minted sections defer to -
the shape where the nameplate page carries no section infobox and the dates
live one page over (~16 pages today, one polite request each). Section-0
wikitext lands as `section-main:<QID>#<ordinal>` records. Resumable: an
interrupted run skips what already landed. `--refresh` re-fetches
everything; unchanged pages land as hash-rejected no-ops.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.ingest.wikipedia import land_section_mains


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    refresh = "--refresh" in sys.argv[1:]

    try:
        with SessionLocal() as session:
            result = land_section_mains(session, already_landed=not refresh)
    except LookupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print(
        f"\nFetched {result.fetched} target page(s).\n"
        f"  {result.inserted} new raw record(s) landed\n"
        f"  {result.fetched - result.inserted} unchanged\n"
        f"  {result.skipped_missing} target(s) with no live article"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
