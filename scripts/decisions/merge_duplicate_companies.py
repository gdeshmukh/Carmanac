"""One-time collapse: fold already-materialized IDENTITY_MERGES members into
their canonical companies.

The engine handles merges correctly only for records it has not yet
materialized: its identity ladder checks `external_ids` before the merge
registry, so a member QID that already created its own company row keeps
resolving to that row forever. The 2026-07-29 batch of company/brand merges
targets exactly such rows - both sides of each pair were admitted and
materialized before the merge was curated - so the duplicates must be
collapsed once, here, after which re-runs stay converged.

Per member with its own company row:

- canonical materialized (the normal case): repoint every `external_ids` row
  from the member's company to the canonical's, delete the member company's
  derived rows (assertions, role assertions, company-scoped flags), delete
  the company row. Refuses any member whose company is referenced by real
  catalogue rows (models, engines, ...) - none should exist yet.
- canonical NOT materialized (Tesla: canonical Q478214 was quarantined while
  member Q124981765 became the company): keep the member's company ROW as
  the pair's company - attach the canonical QID to it and delete the
  member-written fact rows, so the canonical record rewrites name, summary
  and the rest on the next pass. Identity (id, slug) is preserved.

Everything deleted is derived; raw records are untouched (ADR 0004). Re-run
`scripts/pipeline/reconcile_companies.py` afterwards to let the canonical records
re-assert, then `scripts/pipeline/reconcile_vpic_makes.py` to convert the match flags
the duplicates were blocking. Runs dry by default; pass --execute to apply.
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from carmanac.db.models import (
    Company,
    CompanyRoleAssignment,
    Engine,
    ExternalId,
    FieldProvenance,
    MediaAttachment,
    Model,
    ReconciliationFlag,
    SlugAlias,
    Source,
    Transmission,
    VehicleDerivation,
)
from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.bookkeeping import hold_address_lock
from carmanac.reconcile.sources import wikidata

# Columns that would make a member company real catalogue data rather than a
# collapsible duplicate. A hit means a human must look before any merge.
GUARDS = (
    Model.company_id,
    Engine.manufacturer_company_id,
    Transmission.manufacturer_company_id,
    MediaAttachment.company_id,
    VehicleDerivation.company_id,
)


def _referenced(session: Session, company_id: int) -> list[str]:
    hits = []
    for column in GUARDS:
        n = session.scalar(
            select(func.count()).select_from(column.parent.class_).where(column == company_id)
        )
        if n:
            hits.append(f"{column.parent.class_.__tablename__}={n}")
    return hits


def _delete_derived(session: Session, company_id: int) -> None:
    for model in (FieldProvenance, CompanyRoleAssignment, ReconciliationFlag):
        session.execute(delete(model).where(model.company_id == company_id))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="apply changes (default: dry run)")
    args = parser.parse_args()

    with SessionLocal() as session:
        if args.execute:
            hold_address_lock(session)
        source = session.scalars(select(Source).where(Source.name == wikidata.SOURCE_NAME)).one()
        qid_to_company: dict[str, int] = dict(
            session.execute(
                select(ExternalId.external_id, ExternalId.company_id).where(
                    ExternalId.source_id == source.id, ExternalId.company_id.isnot(None)
                )
            ).all()
        )

        collapsed = adopted = 0
        for member_qid, canonical_qid in sorted(policy.IDENTITY_MERGES.items()):
            member_company_id = qid_to_company.get(member_qid)
            if member_company_id is None:
                continue  # not materialized; the engine's merge branch handles it
            canonical_company_id = qid_to_company.get(canonical_qid)
            if member_company_id == canonical_company_id:
                continue  # already collapsed

            member = session.get(Company, member_company_id)
            if canonical_company_id is None:
                # Keep the member's row as the pair's company; the canonical
                # record takes over fact-writing on the next pass.
                print(f"{member_qid} -> {canonical_qid}: adopt company '{member.slug}'")
                if args.execute:
                    session.add(
                        ExternalId(
                            company_id=member_company_id,
                            source_id=source.id,
                            external_id=canonical_qid,
                        )
                    )
                    _delete_derived(session, member_company_id)
                    session.flush()
                qid_to_company[canonical_qid] = member_company_id
                adopted += 1
                continue

            refs = _referenced(session, member_company_id)
            if refs:
                print(f"{member_qid}: REFUSED, company '{member.slug}' referenced: {refs}")
                continue
            canonical = session.get(Company, canonical_company_id)
            print(
                f"{member_qid} -> {canonical_qid}: collapse '{member.slug}' into '{canonical.slug}'"
            )
            if args.execute:
                _delete_derived(session, member_company_id)
                session.execute(
                    update(ExternalId)
                    .where(ExternalId.company_id == member_company_id)
                    .values(company_id=canonical_company_id)
                )
                # The merge sequence ADR 0019 §1 requires, before the delete:
                # forward the member's own address to the survivor, re-point
                # aliases that targeted the member, and re-scope its
                # per-company aliases (scope follows the merge-successor).
                # The address triggers and arc FKs refuse the delete until
                # all three have happened.
                session.add(
                    SlugAlias(
                        entity_kind="company",
                        slug=member.slug,
                        company_id=canonical_company_id,
                        reason="merge",
                    )
                )
                session.execute(
                    update(SlugAlias)
                    .where(SlugAlias.company_id == member_company_id)
                    .values(company_id=canonical_company_id)
                )
                session.execute(
                    update(SlugAlias)
                    .where(SlugAlias.scope_company_id == member_company_id)
                    .values(scope_company_id=canonical_company_id)
                )
                session.execute(delete(Company).where(Company.id == member_company_id))
                session.flush()
            qid_to_company[member_qid] = canonical_company_id
            collapsed += 1

        print(f"collapsed={collapsed} adopted={adopted}")
        if not args.execute:
            print("dry run - pass --execute to apply")
            return 0
        session.commit()
        print(
            "done; re-run scripts/pipeline/reconcile_companies.py then "
            "scripts/pipeline/reconcile_vpic_makes.py to converge"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
