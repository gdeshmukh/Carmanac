"""Run the EPA attach pass (ADR 0014) over the landed vehicles.csv rows.

Idempotent and batch-committed. Bridges EPA makes via vPIC make names (plus
the curated EPA_MAKE_MATCHES registry), attaches models by the exact /
baseModel / word-boundary trim-parse ladder, and mints one configuration
per (period, trim, US market, drivetrain) group - columns written only on
unanimous agreement, external ids only for 1:1 groups, generation_id NULL
throughout (placement is a later evidence pass).
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.epa_attach_pass import run_epa_attach_pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        stats = run_epa_attach_pass(session)
    print(f"reconciler v{policy.RECONCILER_VERSION}: {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
