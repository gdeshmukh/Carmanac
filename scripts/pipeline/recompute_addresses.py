"""Re-derive every page address from current data.

Runs last in the fill sequence, because addresses compose downward: a car's
address is built from its model's and its company's. Idempotent - a second
run writes nothing - and there is no dry-run gate, because an address is not
a commitment while nothing is published. Changing how addresses look means
editing the grammar in `carmanac/reconcile/addressing.py` and running this.
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.reconcile.addressing import recompute_addresses

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        stats = recompute_addresses(session)
        session.commit()
    print(stats.summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
