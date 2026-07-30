"""Print the live project state a session starts from.

PROGRESS.md's head carries intent (what's in flight, what's next, what's
open); the NUMBERS it used to carry go stale the moment work happens. This
script is the mechanical half of the session-start check: run it, diff its
output against the head's claims, and any drift is visible in seconds
instead of a hand-run query per claim. Read-only.

Usage:  .venv/bin/python scripts/status.py
"""

from __future__ import annotations

import subprocess

from sqlalchemy import text

from carmanac.db.session import SessionLocal


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=False
    ).stdout.strip()


def main() -> int:
    print(f"branch   : {_git('rev-parse', '--abbrev-ref', 'HEAD')}")
    print(f"last     : {_git('log', '--oneline', '-1')}")

    with SessionLocal() as s:
        head = s.execute(text("SELECT version_num FROM alembic_version")).scalar()
        print(f"alembic  : {head}")

        for label, sql in (
            ("companies", "SELECT count(*) FROM companies"),
            ("models", "SELECT count(*) FROM models"),
            ("generations", "SELECT count(*) FROM generations"),
            ("catalogue_periods", "SELECT count(*) FROM catalogue_periods"),
            ("configurations", "SELECT count(*) FROM configurations"),
            ("external_ids", "SELECT count(*) FROM external_ids"),
            ("assertions (field_provenance)", "SELECT count(*) FROM field_provenance"),
        ):
            print(f"{label:<30}: {s.execute(text(sql)).scalar()}")

        print("raw records by source/kind:")
        for name, kind, n in s.execute(
            text(
                """
                SELECT so.name,
                       CASE WHEN rr.external_id LIKE 'make:%' THEN 'makes'
                            WHEN rr.external_id LIKE 'model:%' THEN 'models'
                            WHEN rr.external_id LIKE 'modelyears:%' THEN 'model-years'
                            WHEN rr.external_id LIKE 'vehicle:%' THEN 'vehicles'
                            WHEN rr.external_id LIKE 'Q%' THEN 'entities'
                            ELSE 'other' END,
                       count(*)
                FROM raw_scrape.raw_records rr JOIN sources so ON so.id = rr.source_id
                GROUP BY 1, 2 ORDER BY 1, 2
                """
            )
        ):
            print(f"  {name:<22} {kind:<10}: {n}")

        print("open flags by kind:")
        # match_review is split by the flagged record's kind: the make queue
        # (unmatched vPIC makes) and the model queue (slug collisions) are
        # different questions with different resolutions, and one number
        # covering both hides whichever grew.
        for kind, n in s.execute(
            text(
                """
                SELECT f.kind || CASE WHEN f.kind = 'match_review'
                                      THEN ' (' || split_part(rr.external_id, ':', 1) || ')'
                                      ELSE '' END,
                       count(*)
                FROM reconciliation_flags f
                LEFT JOIN raw_scrape.raw_records rr ON rr.id = f.raw_record_id
                WHERE f.status = 'open' GROUP BY 1 ORDER BY 1
                """
            )
        ):
            print(f"  {kind:<22}: {n}")

        matched, landed = s.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM external_ids ei JOIN sources so ON so.id = ei.source_id
                   WHERE so.name = 'NHTSA vPIC' AND ei.external_id LIKE 'make:%'),
                  (SELECT count(*) FROM raw_scrape.raw_records rr
                   JOIN sources so ON so.id = rr.source_id
                   WHERE so.name = 'NHTSA vPIC' AND rr.external_id LIKE 'make:%')
                """
            )
        ).one()
        print(f"vPIC makes matched            : {matched}/{landed}")

        reconciled, landed_models = s.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM external_ids ei JOIN sources so ON so.id = ei.source_id
                   WHERE so.name = 'NHTSA vPIC' AND ei.external_id LIKE 'model:%'),
                  (SELECT count(*) FROM raw_scrape.raw_records rr
                   JOIN sources so ON so.id = rr.source_id
                   WHERE so.name = 'NHTSA vPIC' AND rr.external_id LIKE 'model:%')
                """
            )
        ).one()
        print(f"vPIC models reconciled        : {reconciled}/{landed_models}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
