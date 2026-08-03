"""Retire the demo seed, artifacts included (ADR 0011 §3).

Two phases, both idempotent - a second run finds nothing:

1. The entity chain the old seed_demo.py created: the `3-series` model,
   its E46 → 2002 → 330i-us-sedan chain, the demo engine/transmission,
   and every row hanging off them (joins, EAV, provenance, external ids,
   flags).
2. The raw-side artifacts: the three fabricated `/demo` raw records
   (ADR 0004's own-artifacts clause - fabrications are not evidence and
   are deletable at any tier), the superseded company-arc provenance
   history that attributed demo content to real sources, the seed's
   raw-less Wikidata role row on BMW, and the demo record's
   reconciliation bookkeeping. Deletion is the right verb here, not
   supersession: supersession records a source changing its mind, and no
   source ever asserted these.

After a run that deletes the role row, re-run the companies pass
(`scripts/pipeline/reconcile_companies.py`) so the Wikidata manufacturer role on
BMW is re-asserted from the real Q26678 record.

What stays: the BMW company row and its Q26678 mapping (substantively
real - the reconciler upserts by natural key and adopted them), and the
reference data the seed also created (lookups, `sources`,
`attribute_definitions`).
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    CompanyRole,
    CompanyRoleAssignment,
    Configuration,
    ConfigurationAttribute,
    ConfigurationEngine,
    ConfigurationTransmission,
    Engine,
    ExternalId,
    FieldProvenance,
    Generation,
    Model,
    RawRecord,
    ReconciledRecord,
    ReconciliationFlag,
    Source,
    Transmission,
)
from carmanac.db.session import SessionLocal


def _delete(session: Session, stmt) -> int:
    return session.execute(stmt).rowcount


def retire(session: Session) -> dict[str, int]:
    """Delete the demo chain. Returns row counts per table, zeros if gone."""
    counts: dict[str, int] = {}

    bmw_id = session.scalar(select(Company.id).where(Company.slug == "bmw"))
    model_id = session.scalar(
        select(Model.id).where(Model.company_id == bmw_id, Model.slug == "3-series")
    )
    gen_ids = list(session.scalars(select(Generation.id).where(Generation.model_id == model_id)))
    period_ids = list(
        session.scalars(
            select(CataloguePeriod.id).where(CataloguePeriod.generation_id.in_(gen_ids))
        )
    )
    cfg_ids = list(
        session.scalars(
            select(Configuration.id).where(Configuration.catalogue_period_id.in_(period_ids))
        )
    )
    engine_id = session.scalar(select(Engine.id).where(Engine.slug == "bmw-m54b30"))
    trans_id = session.scalar(select(Transmission.id).where(Transmission.slug == "getrag-220"))

    counts["configuration_attributes"] = _delete(
        session,
        delete(ConfigurationAttribute).where(ConfigurationAttribute.configuration_id.in_(cfg_ids)),
    )
    counts["configuration_engines"] = _delete(
        session,
        delete(ConfigurationEngine).where(ConfigurationEngine.configuration_id.in_(cfg_ids)),
    )
    counts["configuration_transmissions"] = _delete(
        session,
        delete(ConfigurationTransmission).where(
            ConfigurationTransmission.configuration_id.in_(cfg_ids)
        ),
    )

    # Everything the exclusive arc can point at, in one filter shape shared
    # by provenance, external ids, and flags.
    def arc(table):
        clauses = [
            table.configuration_id.in_(cfg_ids),
            table.catalogue_period_id.in_(period_ids),
            table.generation_id.in_(gen_ids),
        ]
        if model_id is not None:
            clauses.append(table.model_id == model_id)
        if engine_id is not None:
            clauses.append(table.engine_id == engine_id)
        if trans_id is not None:
            clauses.append(table.transmission_id == trans_id)
        clauses_or = clauses[0]
        for clause in clauses[1:]:
            clauses_or = clauses_or | clause
        return clauses_or

    counts["field_provenance"] = _delete(
        session, delete(FieldProvenance).where(arc(FieldProvenance))
    )
    counts["external_ids"] = _delete(session, delete(ExternalId).where(arc(ExternalId)))
    counts["reconciliation_flags"] = _delete(
        session, delete(ReconciliationFlag).where(arc(ReconciliationFlag))
    )

    counts["configurations"] = _delete(
        session, delete(Configuration).where(Configuration.id.in_(cfg_ids))
    )
    counts["catalogue_periods"] = _delete(
        session, delete(CataloguePeriod).where(CataloguePeriod.id.in_(period_ids))
    )
    counts["generations"] = _delete(session, delete(Generation).where(Generation.id.in_(gen_ids)))
    if model_id is not None:
        counts["models"] = _delete(session, delete(Model).where(Model.id == model_id))
    if engine_id is not None:
        counts["engines"] = _delete(session, delete(Engine).where(Engine.id == engine_id))
    if trans_id is not None:
        counts["transmissions"] = _delete(
            session, delete(Transmission).where(Transmission.id == trans_id)
        )

    return counts


def retire_raw_artifacts(session: Session) -> dict[str, int]:
    """Phase 2: the fabricated raw records and their false-attribution rows."""
    counts: dict[str, int] = {}

    demo_ids = list(session.scalars(select(RawRecord.id).where(RawRecord.url.like("%/demo"))))
    if demo_ids:
        counts["field_provenance_history"] = _delete(
            session, delete(FieldProvenance).where(FieldProvenance.raw_record_id.in_(demo_ids))
        )
        counts["reconciliation_flags"] = _delete(
            session,
            delete(ReconciliationFlag).where(ReconciliationFlag.raw_record_id.in_(demo_ids)),
        )
        counts["reconciled_records"] = _delete(
            session, delete(ReconciledRecord).where(ReconciledRecord.raw_record_id.in_(demo_ids))
        )
        counts["raw_records"] = _delete(
            session, delete(RawRecord).where(RawRecord.id.in_(demo_ids))
        )

    # The seed's role row: live, sourced "Wikidata", but pointing at no raw
    # record - real passes always carry one. Identified precisely so the
    # pass-created replacement (which has a raw_record_id) never matches.
    bmw_id = session.scalar(select(Company.id).where(Company.slug == "bmw"))
    wikidata_id = session.scalar(select(Source.id).where(Source.name == "Wikidata"))
    manufacturer_id = session.scalar(
        select(CompanyRole.id).where(CompanyRole.code == "manufacturer")
    )
    counts["company_role_assignments"] = _delete(
        session,
        delete(CompanyRoleAssignment).where(
            CompanyRoleAssignment.company_id == bmw_id,
            CompanyRoleAssignment.company_role_id == manufacturer_id,
            CompanyRoleAssignment.source_id == wikidata_id,
            CompanyRoleAssignment.raw_record_id.is_(None),
        ),
    )
    return counts


def main() -> int:
    with SessionLocal() as session:
        counts = retire(session)
        artifact_counts = retire_raw_artifacts(session)
        session.commit()
    deleted = {k: v for k, v in counts.items() if v}
    artifacts = {k: v for k, v in artifact_counts.items() if v}
    if deleted:
        print("Retired demo seed rows: " + ", ".join(f"{k}={v}" for k, v in deleted.items()))
    if artifacts:
        print("Retired raw artifacts:  " + ", ".join(f"{k}={v}" for k, v in artifacts.items()))
    if artifacts.get("company_role_assignments"):
        print("Re-run scripts/pipeline/reconcile_companies.py to re-assert BMW's Wikidata role.")
    if not deleted and not artifacts:
        print("Nothing to retire - the demo seed and its artifacts are already gone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
