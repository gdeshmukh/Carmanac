"""Mint generations from nameplate articles' per-generation sections (ADR 0017 §4).

    .venv/bin/python scripts/pipeline/reconcile_wikipedia_sections.py

Sections keyed `section:<QID>#<ordinal>`; links asserted to the routed model
only. All-or-nothing per article: anything that cannot be safely minted or
reconciled flags `section_generation_review` and waits. Idempotent: a re-run
over unchanged records settles to zero writes.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.reconcile.wikipedia_sections_pass import run_wikipedia_sections_pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        with SessionLocal() as session:
            stats = run_wikipedia_sections_pass(session)
    except LookupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    print(f"\nWikipedia sections pass complete.\n  {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
