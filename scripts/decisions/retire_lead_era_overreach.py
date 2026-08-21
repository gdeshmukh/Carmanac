"""Retire lead-era generations minted over articles that describe several eras.

The lead-era mint (ADR 0017 amendment) read "no parseable generation
sections" as "one era". For articles whose era structure the heading
grammar cannot read - `E28 M5 (1984-1988)`, `Rodeo 4 (1970-1981)` - that
produced one generation spanning a nameplate's whole life. The pass now
vetoes those mints; this removes the rows the defect already created.

Dry-run by default; `--execute` applies. Placed configurations are released
back to the placement pass, which re-decides them on its next run.
"""

from __future__ import annotations

import sys

from sqlalchemy import select, text

from carmanac.db.models import (
    Configuration,
    ExternalId,
    FieldProvenance,
    Generation,
    GenerationModelLink,
    GenerationSpecs,
    RawRecord,
    Source,
)
from carmanac.db.session import SessionLocal
from carmanac.reconcile.sources.wikipedia_sections import looks_multi_era


def census(session) -> list[tuple[Generation, str, int]]:
    """(generation, article title, placed configurations) per overreaching row."""
    source_id = session.scalar(select(Source.id).where(Source.name == "Wikipedia (English)"))
    out = []
    for generation, key in session.execute(
        select(Generation, ExternalId.external_id)
        .join(ExternalId, ExternalId.generation_id == Generation.id)
        .where(
            ExternalId.source_id == source_id,
            ExternalId.external_id.like("section:%#0"),
        )
        .order_by(Generation.slug)
    ):
        qid = key.removeprefix("section:").partition("#")[0]
        record = session.scalar(
            select(RawRecord).where(
                RawRecord.source_id == source_id,
                RawRecord.external_id == f"article:{qid}",
            )
        )
        if record is None or not looks_multi_era(record.payload.get("wikitext", "")):
            continue
        placed = session.scalar(
            select(text("count(*)"))
            .select_from(Configuration)
            .where(Configuration.generation_id == generation.id)
        )
        out.append((generation, record.payload.get("title", ""), placed or 0))
    return out


def main() -> None:
    execute = "--execute" in sys.argv[1:]
    with SessionLocal() as session:
        rows = census(session)
        print(f"{len(rows)} lead-era generations span articles with several eras\n")
        print(f"{'generation':<24}{'span':<14}{'placed':>7}  article")
        for generation, title, placed in rows:
            span = f"{generation.start_year or ''}-{generation.end_year or ''}"
            print(f"{(generation.name or '?'):<24}{span:<14}{placed:>7}  {title}")
        if not execute:
            print("\ndry run; pass --execute to retire these rows")
            return

        ids = [g.id for g, _t, _p in rows]
        if not ids:
            return
        released = (
            session.execute(
                text(
                    "UPDATE configurations SET generation_id = NULL"
                    " WHERE generation_id = ANY(:ids) RETURNING id"
                ),
                {"ids": ids},
            )
            .scalars()
            .all()
        )
        # The placement pass's own claim about each released car, so it
        # re-decides from scratch rather than reading a stale winner. Scoped
        # to these cars: a configuration unplaced for other reasons keeps its
        # provenance chain.
        if released:
            session.execute(
                text(
                    "DELETE FROM field_provenance WHERE field_name = 'generation_id'"
                    " AND configuration_id = ANY(:cids)"
                ),
                {"cids": list(released)},
            )
        for model in (FieldProvenance, GenerationSpecs, GenerationModelLink, ExternalId):
            session.execute(
                text(f"DELETE FROM {model.__tablename__} WHERE generation_id = ANY(:ids)"),
                {"ids": ids},
            )
        session.execute(text("DELETE FROM generations WHERE id = ANY(:ids)"), {"ids": ids})
        session.commit()
        print(f"\nretired {len(ids)} generations; released {len(released)} configurations")


if __name__ == "__main__":
    main()
