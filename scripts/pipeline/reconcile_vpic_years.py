"""Run the vPIC year pass (ADR 0014) over the landed model-year lists.

Idempotent and batch-committed. Creates `model_year` catalogue periods
(start = end) under matched models - pure time, no generation links; the
placement level is configuration-side and evidence-gated. Models under
unmatched makes wait, unflagged.
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.vpic_years_pass import run_vpic_years_pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        stats = run_vpic_years_pass(session)
    print(f"reconciler v{policy.RECONCILER_VERSION}: {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
