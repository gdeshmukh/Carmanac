"""Collapse already-materialized IDENTITY_MERGES members into their
canonical companies.

The engine handles merges correctly only for records it has not yet
materialized: its identity ladder checks `external_ids` before the merge
registry, so a member QID that already created its own company row keeps
resolving to that row forever. A merge curated after both sides materialized
needs this collapse run once, after which re-runs stay converged.

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
- member holds the catalogue, canonical materialized but unreferenced (Ford:
  the brand artifact carries 143 models and the make match while Ford Motor
  Company holds nothing - ADR 0022 §4): keep the member's row, repoint the
  canonical's external ids to it, delete the canonical's derived rows and
  its company row. The canonical record asserts the facts on the next pass;
  the member's identity, slug and models are untouched.

Everything deleted is derived; raw records are untouched (ADR 0004). Re-run
the companies pass (`python -m carmanac.reconcile.engine`) afterwards to let
the canonical records re-assert, then the vPIC match pass
(`python -m carmanac.reconcile.matching`) to convert the match flags the
duplicates were blocking. Runs dry by default; pass --execute to apply.
"""

from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from carmanac.db.models import (
    Company,
    CompanyRelationship,
    CompanyRoleAssignment,
    Engine,
    ExternalId,
    FieldProvenance,
    Generation,
    MediaAttachment,
    Model,
    ModelLine,
    ReconciliationFlag,
    Source,
    Transmission,
    VehicleDerivation,
)
from carmanac.db.session import SessionLocal
from carmanac.reconcile import policy
from carmanac.reconcile.matching import normalize_name
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


def _catalogue_collisions(session: Session, from_id: int, into_id: int) -> list[str]:
    """Lines and generations under `from_id` whose natural key is already
    taken under `into_id` - the absorb path cannot move those."""
    line_names = {
        normalize_name(name)
        for name in session.scalars(select(ModelLine.name).where(ModelLine.company_id == into_id))
    }
    slugs = set(session.scalars(select(Generation.slug).where(Generation.company_id == into_id)))
    hits = [
        f"line {name!r}"
        for name in session.scalars(select(ModelLine.name).where(ModelLine.company_id == from_id))
        if normalize_name(name) in line_names
    ]
    hits += [
        f"generation {slug!r}"
        for slug in session.scalars(select(Generation.slug).where(Generation.company_id == from_id))
        if slug in slugs
    ]
    return hits


def _delete_derived(session: Session, company_id: int) -> None:
    for model in (FieldProvenance, CompanyRoleAssignment, ReconciliationFlag):
        session.execute(delete(model).where(model.company_id == company_id))
    # Parent eras are re-derived by the relations pass against the surviving
    # row; a row's own eras and the eras naming it as parent both go.
    session.execute(
        delete(CompanyRelationship).where(
            (CompanyRelationship.company_id == company_id)
            | (CompanyRelationship.parent_company_id == company_id)
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="apply changes (default: dry run)")
    args = parser.parse_args()

    with SessionLocal() as session:
        source = session.scalars(select(Source).where(Source.name == wikidata.SOURCE_NAME)).one()
        qid_to_company: dict[str, int] = dict(
            session.execute(
                select(ExternalId.external_id, ExternalId.company_id).where(
                    ExternalId.source_id == source.id, ExternalId.company_id.isnot(None)
                )
            ).all()
        )

        collapsed = adopted = absorbed = 0
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

            canonical = session.get(Company, canonical_company_id)
            refs = _referenced(session, member_company_id)
            if refs:
                canonical_refs = _referenced(session, canonical_company_id)
                collisions = _catalogue_collisions(session, canonical_company_id, member_company_id)
                if canonical_refs or collisions:
                    print(
                        f"{member_qid}: REFUSED, both referenced: "
                        f"'{member.slug}' {refs}, '{canonical.slug}' {canonical_refs + collisions}"
                    )
                    continue
                print(
                    f"{member_qid} -> {canonical_qid}: absorb '{canonical.slug}' into "
                    f"'{member.slug}' (keeps the catalogue: {refs})"
                )
                if args.execute:
                    _delete_derived(session, canonical_company_id)
                    # Lines and generations anchored on the legal entity are
                    # the enterprise's; they move with its identity.
                    for table in (ExternalId, ModelLine, Generation):
                        session.execute(
                            update(table)
                            .where(table.company_id == canonical_company_id)
                            .values(company_id=member_company_id)
                        )
                    session.execute(delete(Company).where(Company.id == canonical_company_id))
                    session.flush()
                qid_to_company[canonical_qid] = member_company_id
                absorbed += 1
                continue
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
                session.execute(delete(Company).where(Company.id == member_company_id))
                session.flush()
            qid_to_company[member_qid] = canonical_company_id
            collapsed += 1

        print(f"collapsed={collapsed} adopted={adopted} absorbed={absorbed}")
        if not args.execute:
            print("dry run - pass --execute to apply")
            return 0
        session.commit()
        print(
            "done; re-run the companies pass (python -m carmanac.reconcile.engine) "
            "then the vPIC match pass (python -m carmanac.reconcile.matching) to converge"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
