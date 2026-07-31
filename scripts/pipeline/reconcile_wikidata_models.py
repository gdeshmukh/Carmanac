"""Run the Wikidata models-sweep pass (ADR 0012) over landed sweep records.

    .venv/bin/python scripts/pipeline/reconcile_wikidata_models.py

Match and enrich only - no model creation (the global expansion is tabled,
ADR 0012 §3). Idempotent: a re-run over unchanged records is a no-op.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.reconcile.wikidata_models_pass import run_wikidata_models_pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    try:
        with SessionLocal() as session:
            stats = run_wikidata_models_pass(session)
    except LookupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    print(f"\nWikidata models pass complete.\n  {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
