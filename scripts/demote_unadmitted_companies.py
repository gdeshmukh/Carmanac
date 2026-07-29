"""One-time demotion: remove companies the tightened admission no longer passes.

Policy v2 (2026-07-29) requires affirmative car evidence to admit; v1 admitted
boilerplate-only class sets, letting in ~2,175 suppliers, dealerships and
service companies. This script re-classifies every Wikidata-created company
against the CURRENT policy and deletes the ones that no longer admit, along
with their derived rows (assertions, role assertions, external ids, flags).

Safe because everything deleted is derived data: the raw records are untouched
(ADR 0004 - these deletions are artifacts of our own admission bug), nothing
else references these companies yet (no models exist), and a re-run of the
reconciler quarantines the same records with review flags. Runs dry by
default; pass --execute to apply.
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, select

from carmanac.db.models import (
    Company,
    CompanyRoleAssignment,
    ExternalId,
    FieldProvenance,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.sources import wikidata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="apply deletions (default: dry run)")
    args = parser.parse_args()

    with SessionLocal() as session:
        source = session.scalars(select(Source).where(Source.name == wikidata.SOURCE_NAME)).one()

        # Current record per company-mapped external id, exactly as the engine
        # selects its reconciliation units.
        records = {
            r.external_id: r
            for r in session.scalars(
                select(RawRecord)
                .where(RawRecord.source_id == source.id, RawRecord.external_id.isnot(None))
                .order_by(RawRecord.external_id, RawRecord.last_seen_at.desc(), RawRecord.id.desc())
                .distinct(RawRecord.external_id)
            )
        }

        # Companies holding another source's external id are cross-source
        # admitted (ADR 0008): vPIC evidence outranks a quarantine verdict,
        # exactly as in the engine. Without this guard the script would sweep
        # the corroborated companies (Terrafugia et al.).
        cross_source_admitted: set[int] = set(
            session.scalars(
                select(ExternalId.company_id).where(
                    ExternalId.source_id != source.id, ExternalId.company_id.isnot(None)
                )
            )
        )

        demote: dict[int, str] = {}
        for external_id, company_id in session.execute(
            select(ExternalId.external_id, ExternalId.company_id).where(
                ExternalId.source_id == source.id, ExternalId.company_id.isnot(None)
            )
        ):
            if external_id in policy.IDENTITY_MERGES:
                continue  # merge members follow their canonical entity
            if external_id in policy.MERGE_CANONICALS:
                continue  # naming a canonical is itself the admission decision
            if company_id in cross_source_admitted:
                continue
            record = records.get(external_id)
            if record is None:
                continue
            mapped = wikidata.map_record(record.payload)
            if mapped is None:
                continue
            if policy.classify(mapped.classes, external_id) != policy.ADMIT:
                demote[company_id] = external_id

        print(f"companies to demote: {len(demote)}")
        for company in session.scalars(
            select(Company).where(Company.id.in_(demote)).order_by(Company.name).limit(15)
        ):
            print(f"  e.g. {company.name}")

        if not args.execute:
            print("dry run - pass --execute to apply")
            return 0

        ids = list(demote)
        for model in (FieldProvenance, CompanyRoleAssignment, ExternalId, ReconciliationFlag):
            session.execute(delete(model).where(model.company_id.in_(ids)))
        session.execute(delete(Company).where(Company.id.in_(ids)))
        session.commit()
        print(
            f"demoted {len(ids)} companies; re-run scripts/reconcile_companies.py "
            f"to quarantine their records under policy v{policy.RECONCILER_VERSION}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
