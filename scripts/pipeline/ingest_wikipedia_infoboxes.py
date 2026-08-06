"""Fetch generation-article infoboxes from English Wikipedia (ADR 0017 §1).

    .venv/bin/python scripts/pipeline/ingest_wikipedia_infoboxes.py [--refresh]

Targets are sitelinks on already-matched QIDs - generation-attached entities
plus the series-membership (line-case) ones. ~520 articles on the first run,
one polite request each. Resumable: an interrupted run skips what already
landed. `--refresh` re-fetches everything (revisions move); unchanged pages
land as hash-rejected no-ops.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.ingest.wikipedia import land_generation_infoboxes


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    refresh = "--refresh" in sys.argv[1:]

    try:
        with SessionLocal() as session:
            result = land_generation_infoboxes(session, already_landed=not refresh)
    except LookupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print(
        f"\nFetched {result.fetched} article(s).\n"
        f"  {result.inserted} new raw record(s) landed\n"
        f"  {result.fetched - result.inserted} unchanged\n"
        f"  {result.skipped_missing} sitelink(s) with no live article"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
