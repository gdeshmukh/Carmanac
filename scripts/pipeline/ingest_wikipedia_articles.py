"""Fetch full nameplate articles from English Wikipedia (ADR 0017 §4).

    .venv/bin/python scripts/pipeline/ingest_wikipedia_articles.py [--refresh]

Targets are sitelinks on QIDs 1:1-attached to models, plus the curated
SECTION_ARTICLE_MODELS routings - ~430 pages, one polite request each. The
full wikitext lands as `article:<QID>` records; the per-generation sections
live beyond section 0, which is why the section-0 `infobox:` sweep cannot
serve this pass. Resumable: an interrupted run skips what already landed.
`--refresh` re-fetches everything; unchanged pages land as hash-rejected
no-ops.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.ingest.wikipedia import land_nameplate_articles


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    refresh = "--refresh" in sys.argv[1:]

    try:
        with SessionLocal() as session:
            result = land_nameplate_articles(session, already_landed=not refresh)
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
