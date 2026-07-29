"""One-time re-key: vPIC external ids gain a `make:` kind prefix.

vPIC's MakeId and ModelId are separate integer namespaces, but `external_ids`
is unique on `(source_id, external_id)` — with bare integers, MakeId 440
(Aston Martin) and ModelId 440 (whatever model draws it) would collide the
day the models fetch lands. Decided 2026-07-29 (Gaurav): kind-prefix both —
`make:440` now, `model:<id>` from the models fetch's first run — while the
change costs one small script instead of a data migration.

Re-keys the vPIC rows in `raw_scrape.raw_records` and `external_ids` whose
external id is still a bare integer. The seed demo's synthetic vPIC record
(`vpic-2002-bmw-330i`) is untouched by the pattern. Idempotent: already-
prefixed rows don't match. Runs dry by default; pass --execute to apply.
"""

from __future__ import annotations

import argparse

from sqlalchemy import func, select, update

from carmanac.db.models import ExternalId, RawRecord, Source
from carmanac.db.session import SessionLocal
from carmanac.ingest.vpic.land import VPIC_SOURCE_NAME

BARE_INT = r"^[0-9]+$"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="apply changes (default: dry run)")
    args = parser.parse_args()

    with SessionLocal() as session:
        source = session.scalars(select(Source).where(Source.name == VPIC_SOURCE_NAME)).one()

        n_raw = session.scalar(
            select(func.count())
            .select_from(RawRecord)
            .where(RawRecord.source_id == source.id, RawRecord.external_id.regexp_match(BARE_INT))
        )
        n_ids = session.scalar(
            select(func.count())
            .select_from(ExternalId)
            .where(ExternalId.source_id == source.id, ExternalId.external_id.regexp_match(BARE_INT))
        )
        print(f"to re-key: {n_raw} raw records, {n_ids} external_ids rows")

        if not args.execute:
            print("dry run - pass --execute to apply")
            return 0

        session.execute(
            update(RawRecord)
            .where(RawRecord.source_id == source.id, RawRecord.external_id.regexp_match(BARE_INT))
            .values(external_id="make:" + RawRecord.external_id)
        )
        session.execute(
            update(ExternalId)
            .where(ExternalId.source_id == source.id, ExternalId.external_id.regexp_match(BARE_INT))
            .values(external_id="make:" + ExternalId.external_id)
        )
        session.commit()
        print(f"re-keyed {n_raw} raw records and {n_ids} external_ids rows to make:<MakeId>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
