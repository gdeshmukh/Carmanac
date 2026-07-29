"""Run the vPIC match pass (ADR 0008) over the landed passenger makes.

Idempotent. Matches write `external_ids` + a vPIC-sourced manufacturer role;
matched quarantined Wikidata entities are admitted by corroboration and their
admission flags resolve as `corroborated_by_vpic`; unmatched makes open
`match_review` flags whose resolutions grow `policy.VPIC_MATCHES`.
"""

from __future__ import annotations

import logging

from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.matching import run_vpic_match_pass

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def main() -> int:
    with SessionLocal() as session:
        stats = run_vpic_match_pass(session)
    print(f"reconciler v{policy.RECONCILER_VERSION}: {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
