"""Run one pipeline step from the command line.

Every ingest module and reconcile pass is directly runnable
(`python -m carmanac.reconcile.matching`); this owns the shared entry
behaviour - logging, the session, the summary line, clean exits - so each
module declares only which function to run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from carmanac.db.session import SessionLocal
from carmanac.ingest.http import IngestHTTPError


def run(step: Callable[..., Any], /, **kwargs: Any) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        with SessionLocal() as session:
            result = step(session, **kwargs)
            session.commit()
    except (IngestHTTPError, LookupError) as exc:
        raise SystemExit(f"{exc}") from exc
    print(result.summary())
