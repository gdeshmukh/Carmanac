"""Project landed corporate-structure claims into `company_relationships`
(ADR 0022 §§1-3).

Parent-organization claims on the child and subsidiary claims on the parent
assert the same era; owned-by claims are context and assert nothing. Both ends
resolve through `external_ids` and the merge registry; an end we do not hold
waits in raw, and a self-edge (both ends one row after a merge) is skipped.
Re-runs converge: eras this source no longer states are retired by
supersession, eras it still states are no-ops.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from carmanac.db.models import CompanyRelationship, ExternalId, RawRecord
from carmanac.ingest.landing import get_source
from carmanac.ingest.wikidata.relations import SOURCE_NAME, SWEEP_MARKER
from carmanac.reconcile import policy
from carmanac.reconcile.bookkeeping import DecisionLog, mark_reconciled
from carmanac.reconcile.engine import current_records

log = logging.getLogger(__name__)

PASS_NAME = "company_relations"
KIND = "parent_organization"

_YEAR = re.compile(r"^(-?\d{1,4})")


@dataclass
class CompanyRelationsStats:
    records: int = 0
    eras_asserted: int = 0
    eras_inserted: int = 0
    eras_retired: int = 0
    waits_parent_not_held: int = 0
    self_edges: int = 0
    implausible_eras: int = 0
    unattached: int = 0

    def summary(self) -> str:
        return (
            f"records={self.records} eras={self.eras_asserted} "
            f"(inserted={self.eras_inserted}, retired={self.eras_retired}) "
            f"waits_parent_not_held={self.waits_parent_not_held} "
            f"self_edges={self.self_edges} implausible_eras={self.implausible_eras} "
            f"unattached={self.unattached}"
        )


@dataclass(frozen=True)
class Era:
    company_id: int
    parent_company_id: int
    start_year: int | None
    end_year: int | None


def _year(literals: list[str], pick) -> int | None:
    years = [int(m.group(1)) for value in literals if (m := _YEAR.match(value))]
    return pick(years) if years else None


def _era(child: int, parent: int, claim: dict) -> Era:
    return Era(
        child, parent, _year(claim.get("starts", []), min), _year(claim.get("ends", []), max)
    )


def run_company_relations_pass(session: Session) -> CompanyRelationsStats:
    source = get_source(session, SOURCE_NAME)
    decisions = DecisionLog(session, source.id, PASS_NAME)
    stats = CompanyRelationsStats()

    company_by_qid: dict[str, int] = dict(
        session.execute(
            select(ExternalId.external_id, ExternalId.company_id).where(
                ExternalId.source_id == source.id, ExternalId.company_id.isnot(None)
            )
        ).all()
    )

    def resolve(qid: str) -> int | None:
        return company_by_qid.get(policy.IDENTITY_MERGES.get(qid, qid)) or company_by_qid.get(qid)

    # era -> the record whose provenance it carries: the lowest-QID record
    # stating it, so a claim both ends state is owned deterministically.
    wanted: dict[Era, RawRecord] = {}
    for record in current_records(session, source.id, sweep=SWEEP_MARKER):
        stats.records += 1
        own = resolve(record.external_id)
        if own is None:
            stats.unattached += 1
            decisions.record(record, "waits_unattached_qid")
            mark_reconciled(session, record)
            continue
        asserted: list[Era] = []
        not_held: list[str] = []
        implausible: list[str] = []
        self_edges = 0
        for edge in ("parents", "subsidiaries"):
            for claim in record.payload.get(edge, []):
                if claim.get("rank") == "deprecated":
                    continue
                other = resolve(claim["qid"])
                if other is None:
                    not_held.append(claim.get("label") or claim["qid"])
                    continue
                if other == own:
                    self_edges += 1
                    continue
                child, parent = (own, other) if edge == "parents" else (other, own)
                era = _era(child, parent, claim)
                if era.start_year and era.end_year and era.start_year > era.end_year:
                    implausible.append(claim.get("label") or claim["qid"])
                    continue
                wanted.setdefault(era, record)
                asserted.append(era)
        stats.waits_parent_not_held += len(not_held)
        stats.self_edges += self_edges
        stats.implausible_eras += len(implausible)
        if asserted or not_held or self_edges or implausible:
            decisions.record(
                record,
                "relations_asserted" if asserted else "waits_parent_not_held",
                method="structural_edge",
                detail={
                    "asserted": len(asserted),
                    "not_held": sorted(set(not_held)),
                    "self_edges": self_edges,
                    "implausible": sorted(set(implausible)),
                },
            )
        else:
            decisions.record(record, "no_structural_edges")
        mark_reconciled(session, record)

    live = {
        Era(row.company_id, row.parent_company_id, row.start_year, row.end_year): row
        for row in session.scalars(
            select(CompanyRelationship).where(
                CompanyRelationship.source_id == source.id,
                CompanyRelationship.kind == KIND,
                CompanyRelationship.superseded_by.is_(None),
            )
        )
    }
    # The same pair is often stated from both ends, one end carrying the
    # qualifiers and the other not. An undated era beside a dated one for the
    # same pair is that claim with its dates dropped, so it is not a second
    # era - only a pair with no dated claim at all keeps its undated row.
    dated_pairs = {
        (era.company_id, era.parent_company_id)
        for era in wanted
        if era.start_year is not None or era.end_year is not None
    }
    wanted = {
        era: record
        for era, record in wanted.items()
        if era.start_year is not None
        or era.end_year is not None
        or (era.company_id, era.parent_company_id) not in dated_pairs
    }
    stats.eras_asserted = len(wanted)
    for era, record in wanted.items():
        if era in live:
            continue
        session.add(
            CompanyRelationship(
                company_id=era.company_id,
                parent_company_id=era.parent_company_id,
                kind=KIND,
                start_year=era.start_year,
                end_year=era.end_year,
                source_id=source.id,
                raw_record_id=record.id,
                scraped_at=record.last_seen_at,
            )
        )
        stats.eras_inserted += 1
    for era, row in live.items():
        if era not in wanted:
            # Retired, not deleted: the source stopped stating this era.
            row.superseded_by = row.id
            stats.eras_retired += 1
    session.flush()
    decisions.flush()
    return stats


if __name__ == "__main__":
    from carmanac.runner import run

    run(run_company_relations_pass)
