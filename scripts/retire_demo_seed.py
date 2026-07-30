"""Retire the demo seed's entity chain (ADR 0011 §3).

Deletes the synthetic 2002 BMW 330i scaffolding the old seed_demo.py
created: the `3-series` model, its E46 → 2002 → 330i-us-sedan chain, the
demo engine/transmission, and every row hanging off them (joins, EAV,
provenance, external ids, flags). One-off and idempotent — a second run
finds nothing.

What it deliberately does NOT touch:

- The BMW company row (real, reconciler-owned, vPIC-matched) and its
  role/provenance rows.
- The three simulated raw records (raw is not casually deleted; inert).
- Reference data the seed also created: lookups, `sources`,
  `attribute_definitions`.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    ConfigurationAttribute,
    ConfigurationEngine,
    ConfigurationTransmission,
    Engine,
    ExternalId,
    FieldProvenance,
    Generation,
    Model,
    ReconciliationFlag,
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


def main() -> int:
    with SessionLocal() as session:
        counts = retire(session)
        session.commit()
    deleted = {k: v for k, v in counts.items() if v}
    if deleted:
        print("Retired demo seed rows: " + ", ".join(f"{k}={v}" for k, v in deleted.items()))
    else:
        print("Nothing to retire - the demo seed chain is already gone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
