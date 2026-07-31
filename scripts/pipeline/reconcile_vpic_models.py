"""Run the vPIC models pass (ADR 0010) over the landed passenger models.

Idempotent. Creates `models` rows under MATCHED makes only, keyed by
`model:<ModelId>` in `external_ids`, with `name` asserted through
`field_provenance`; slug collisions under one company open `match_review`
flags whose resolution is merge-or-suffix, never an auto-suffix.
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.vpic_models_pass import run_vpic_models_pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        stats = run_vpic_models_pass(session)
    print(f"reconciler v{policy.RECONCILER_VERSION}: {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
