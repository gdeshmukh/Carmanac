"""Place configurations into generations by unique dated overlap (ADR 0017 §3).

    .venv/bin/python scripts/pipeline/reconcile_generation_placement.py

One candidate places with provenance; two or more flag `generation_overlap`
(the AMG GT case - the year alone must not choose); zero is the normal
waiting state. Idempotent, and the sole placer: recomputed answers supersede
old ones, including back to NULL.
"""

from __future__ import annotations

import logging
import sys

from carmanac.db.session import SessionLocal
from carmanac.reconcile.generation_placement_pass import run_generation_placement_pass


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    try:
        with SessionLocal() as session:
            stats = run_generation_placement_pass(session)
    except LookupError as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1
    print(f"\nGeneration placement pass complete.\n  {stats.summary()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
