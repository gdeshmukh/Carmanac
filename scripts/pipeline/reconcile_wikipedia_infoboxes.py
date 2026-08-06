"""Assert generation time and chassis codes from landed infoboxes (ADR 0016 §4).

    .venv/bin/python scripts/pipeline/reconcile_wikipedia_infoboxes.py

Production spans -> `generations.start_year`/`end_year`; title parentheticals
-> `chassis_codes`. Unparseable spans flag rather than guess. Idempotent: a
re-run over unchanged records settles to zero writes.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.reconcile.wikipedia_infobox_pass import run_wikipedia_infobox_pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        with SessionLocal() as session:
            stats = run_wikipedia_infobox_pass(session)
    except LookupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    print(f"\nWikipedia infobox pass complete.\n  {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
