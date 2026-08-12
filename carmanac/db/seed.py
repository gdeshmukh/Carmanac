"""Seed the `countries` lookup with the full ISO 3166 set.

Reference data must exist before the ingestion that joins against it (the
`get_source` principle): the reconciler projects `country_id` by joining
Wikidata's P297 ISO 3166-1 alpha-2 code against `countries.code`, so every
code Wikidata can emit needs a row here first.

Historic countries (ISO 3166-3, via `pycountry.historic_countries`) are seeded
too, deliberately: a global every-car-ever database attributes marques to the
country that existed at the time - Trabant is East Germany (DD), Moskvitch is
the Soviet Union (SU), Yugo is Yugoslavia (YU) - and Wikidata records those
P297 codes on the former states.

Idempotent: existing codes are left untouched (ON CONFLICT DO NOTHING), so
re-running moves nothing downstream.
"""

from __future__ import annotations

import pycountry
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import Country


def seed_countries(session: Session) -> tuple[int, int]:
    """Insert every ISO 3166 country not already present. Returns (inserted, total)."""
    rows = [{"code": c.alpha_2, "name": c.name} for c in pycountry.countries]
    rows += [
        {"code": c.alpha_2, "name": c.name}
        for c in pycountry.historic_countries
        # A few historic alpha-2 codes were later reassigned to current
        # countries (e.g. SK: Sikkim -> Slovakia); the current holder wins.
        if c.alpha_2 not in {r["code"] for r in rows}
    ]

    stmt = (
        pg_insert(Country)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["code"])
        .returning(Country.id)
    )
    inserted = len(session.execute(stmt).all())
    session.commit()
    return inserted, len(rows)


if __name__ == "__main__":
    from carmanac.db.session import SessionLocal

    with SessionLocal() as session:
        inserted, total = seed_countries(session)
    print(f"countries: {inserted} inserted, {total - inserted} already present")
