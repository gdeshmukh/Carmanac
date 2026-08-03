"""Apply the Santana model-collision verdict (2026-07-30).

Both vPIC makes SANTANA (13765) and LAND ROVER SANTANA (13766) are one
company (Santana Motor, pinned in the approved no-match batch), and each
filed the same three licensee-built wheelbase models. Verdict: one page
per wheelbase - each LAND ROVER SANTANA ModelId is the SAME nameplate as
the row its SANTANA twin already created, so it attaches to that row
rather than minting a suffixed twin (ADR 0010 §2.3, ADR 0011 §5).

Per pair: insert the `model:<id>` external id onto the existing row and
resolve the open slug-collision flag with the verdict recorded. Nothing
is deleted; the flags stay as labeled decisions. Idempotent.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from carmanac.db.models import Company, ExternalId, Model, RawRecord, ReconciliationFlag, Source
from carmanac.db.session import SessionLocal

# higher (LAND ROVER SANTANA) ModelId -> the slug its SANTANA twin created.
MERGES: dict[str, str] = {
    "model:36864": "110-wb",
    "model:36866": "90-wb",
    "model:37552": "88-wb",
}

RESOLUTION = "same_nameplate_second_makeid"


def apply(session: Session) -> tuple[int, int]:
    vpic = session.scalars(select(Source).where(Source.name == "NHTSA vPIC")).one()
    santana = session.scalars(select(Company).where(Company.slug == "santana-motor")).one()

    attached = resolved = 0
    for external_id, slug in MERGES.items():
        model = session.scalars(
            select(Model).where(Model.company_id == santana.id, Model.slug == slug)
        ).one()

        exists = session.scalar(
            select(ExternalId.id).where(
                ExternalId.source_id == vpic.id, ExternalId.external_id == external_id
            )
        )
        if exists is None:
            session.add(ExternalId(model_id=model.id, source_id=vpic.id, external_id=external_id))
            attached += 1

        flags = session.scalars(
            select(ReconciliationFlag)
            .join(RawRecord, ReconciliationFlag.raw_record_id == RawRecord.id)
            .where(
                ReconciliationFlag.kind == "match_review",
                ReconciliationFlag.status == "open",
                RawRecord.source_id == vpic.id,
                RawRecord.external_id == external_id,
            )
        ).all()
        for flag in flags:
            flag.status = "resolved"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": RESOLUTION}
            resolved += 1

    return attached, resolved


def main() -> int:
    with SessionLocal() as session:
        attached, resolved = apply(session)
        session.commit()
    print(f"Attached {attached} external id(s); resolved {resolved} flag(s).")
    if attached == 0 and resolved == 0:
        print("Nothing to do - merges already applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
