"""Run the reconciler's companies pass over the landed Wikidata records.

Idempotent: an immediate re-run writes nothing new. Policy changes (admission
lists, mappings) are applied by editing `carmanac/reconcile/policy.py`,
bumping RECONCILER_VERSION, and re-running this script - re-reconciliation is
the normal case (ADR 0007).
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.engine import run_companies_pass
from carmanac.reconcile.sources import wikidata

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        stats = run_companies_pass(session, wikidata)
    print(f"reconciler v{policy.RECONCILER_VERSION}: {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
