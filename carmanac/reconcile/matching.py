"""The vPIC match pass (ADR 0008): cross-source identity and corroboration.

The first pass that connects two sources' views of the same company. For each
landed vPIC passenger make, the ladder is:

    1. curated registry (`policy.VPIC_MATCHES`)  - recorded human judgments
    2. unique exact-normalized-name match        - mechanical, high precision
    3. `match_review` flag with trigram-generated CANDIDATES - never a match

A confirmed match writes the `external_ids` row (vPIC MakeId -> company) and
asserts the `manufacturer` role from vPIC - appearing in the US VIN system is
ADR 0005's evidence from the source that defines it.

**Corroboration-admission**: a vPIC make matching a QUARANTINED Wikidata
entity is affirmative car evidence (ADR 0008's fourth way in). The entity's
company is created on the spot via the Wikidata engine internals - same
assertions, projection, and plausibility checks as a normal admission - its
open `admission_review` flags resolve as `corroborated_by_vpic`, and future
Wikidata passes keep it updated through the engine's external-ids check.

Absence of a match is silence, never demotion: a company vPIC has not heard
of is normal (pre-1981, never sold in the US).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import (
    Company,
    CompanyRoleAssignment,
    ExternalId,
    RawRecord,
    ReconciledRecord,
    ReconciliationFlag,
)
from carmanac.ingest.landing import get_source
from carmanac.ingest.vpic.land import VPIC_SOURCE_NAME
from carmanac.reconcile import policy
from carmanac.reconcile.engine import _current_records, _Pass
from carmanac.reconcile.sources import wikidata

log = logging.getLogger(__name__)


@dataclass
class MatchStats:
    processed: int = 0
    already_matched: int = 0
    matched_existing: int = 0
    corroborated_created: int = 0
    flagged: int = 0
    roles_asserted: int = 0
    admission_flags_resolved: int = 0
    match_flags_dismissed: int = 0

    def summary(self) -> str:
        return (
            f"processed={self.processed} already={self.already_matched} "
            f"matched={self.matched_existing} corroborated={self.corroborated_created} "
            f"flagged={self.flagged} | roles={self.roles_asserted} "
            f"admission_resolved={self.admission_flags_resolved} "
            f"match_flags_dismissed={self.match_flags_dismissed}"
        )


def normalize_name(name: str) -> str:
    """Casefold and strip everything but letters and digits: 'ASTON MARTIN'
    == 'Aston Martin' == 'aston-martin'. Mechanical on purpose - anything
    smarter is rung 3's job (candidates), never auto-accepted."""
    return "".join(ch for ch in name.casefold() if ch.isalnum())


class _MatchPass:
    def __init__(self, session: Session):
        self.session = session
        self.stats = MatchStats()
        self.vpic = get_source(session, VPIC_SOURCE_NAME)

        # The Wikidata engine pass provides identity plumbing (resolve/create,
        # assertions, projection, plausibility) plus the wikidata external-id
        # map and the open admission flags keyed by QID.
        self.wp = _Pass(session, wikidata)

        # vPIC MakeId -> company, rung 0 (already matched on a prior run).
        self.vpic_ids: dict[str, int] = dict(
            session.execute(
                select(ExternalId.external_id, ExternalId.company_id).where(
                    ExternalId.source_id == self.vpic.id,
                    ExternalId.company_id.isnot(None),
                )
            ).all()
        )

        # Rung 2 lookup: normalized company name -> company ids.
        self.by_name: dict[str, list[int]] = {}
        for company_id, name in session.execute(select(Company.id, Company.name)):
            self.by_name.setdefault(normalize_name(name), []).append(company_id)

        # Quarantined Wikidata entities by normalized label: current records
        # with no company, a usable label, and a QUARANTINE verdict.
        self.quarantined: dict[str, list[RawRecord]] = {}
        for record in _current_records(session, self.wp.source.id):
            if record.external_id in self.wp.external_ids:
                continue
            mapped = wikidata.map_record(record.payload)
            if mapped is None or mapped.name is None:
                continue
            if policy.classify(mapped.classes, mapped.external_id) == policy.QUARANTINE:
                self.quarantined.setdefault(normalize_name(mapped.name), []).append(record)

        # Open match_review flags by vPIC external id, for dedupe + dismissal.
        self.open_match_flags: dict[str, list[ReconciliationFlag]] = {}
        for flag, external_id in session.execute(
            select(ReconciliationFlag, RawRecord.external_id)
            .join(RawRecord, ReconciliationFlag.raw_record_id == RawRecord.id)
            .where(
                ReconciliationFlag.kind == "match_review",
                ReconciliationFlag.status == "open",
                RawRecord.source_id == self.vpic.id,
            )
        ):
            self.open_match_flags.setdefault(external_id, []).append(flag)

    # -- helpers ---------------------------------------------------------------

    def _trigram_candidates(self, name: str) -> list[dict]:
        rows = self.session.execute(
            text(
                """SELECT name, slug, round(similarity(name, :n)::numeric, 2) AS sim
                   FROM companies WHERE similarity(name, :n) > 0.3
                   ORDER BY sim DESC, slug LIMIT 5"""
            ),
            {"n": name},
        ).all()
        return [{"name": r.name, "slug": r.slug, "similarity": float(r.sim)} for r in rows]

    def _assert_role(self, company_id: int, record: RawRecord) -> None:
        live = self.session.scalars(
            select(CompanyRoleAssignment).where(
                CompanyRoleAssignment.company_id == company_id,
                CompanyRoleAssignment.company_role_id == self.wp.role_id,
                CompanyRoleAssignment.source_id == self.vpic.id,
                CompanyRoleAssignment.superseded_by.is_(None),
            )
        ).first()
        if live is None:
            self.session.add(
                CompanyRoleAssignment(
                    company_id=company_id,
                    company_role_id=self.wp.role_id,
                    source_id=self.vpic.id,
                    raw_record_id=record.id,
                    scraped_at=record.last_seen_at,
                )
            )
            self.stats.roles_asserted += 1

    def _attach(self, company_id: int, record: RawRecord) -> None:
        """A confirmed match: external id, role, and any stale flag dismissed."""
        self.session.add(
            ExternalId(
                company_id=company_id,
                source_id=self.vpic.id,
                external_id=record.external_id,
            )
        )
        self.session.flush()
        self.vpic_ids[record.external_id] = company_id
        self._assert_role(company_id, record)
        for flag in self.open_match_flags.pop(record.external_id, []):
            flag.status = "dismissed"
            flag.resolved_at = func.now()
            # The close reason feeds the labeled set (2026-07-30 review).
            flag.detail = {**(flag.detail or {}), "resolution": "record_resolves_to_company"}
            self.stats.match_flags_dismissed += 1

    def _corroborate_create(self, wd_record: RawRecord, vpic_record: RawRecord) -> int | None:
        """Admit a quarantined Wikidata entity on vPIC evidence: create its
        company through the engine internals (same assertions, projection and
        plausibility as a class-based admission), then resolve its admission
        flags as corroborated - a machine decision, marked as one."""
        mapped = wikidata.map_record(wd_record.payload)
        company = self.wp._resolve_company(mapped, wd_record)
        if company is None:
            return None
        current = self.wp._write_assertions(company, mapped, wd_record)
        self.wp._write_role(company, mapped, wd_record)
        self.wp._project(company, current)
        self.wp._check_plausibility(company, wd_record)

        for flag in self.wp.open_admission.pop(wd_record.external_id, []):
            flag.status = "resolved"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": "corroborated_by_vpic"}
            self.stats.admission_flags_resolved += 1
        self.wp.open_flags.discard(("record", wd_record.external_id, "admission_review"))

        self.stats.corroborated_created += 1
        return company.id

    def _flag(self, record: RawRecord, make_name: str, candidates: list[dict], reason: str) -> None:
        if record.external_id in self.open_match_flags:
            return
        flag = ReconciliationFlag(
            kind="match_review",
            raw_record_id=record.id,
            source_id=self.vpic.id,
            detail={"make_name": make_name, "reason": reason, "candidates": candidates},
        )
        self.session.add(flag)
        self.open_match_flags[record.external_id] = [flag]
        self.stats.flagged += 1

    def _mark(self, record: RawRecord) -> None:
        self.session.execute(
            pg_insert(ReconciledRecord)
            .values(raw_record_id=record.id, reconciler_version=policy.RECONCILER_VERSION)
            .on_conflict_do_update(
                index_elements=["raw_record_id"],
                set_={
                    "reconciled_at": func.now(),
                    "reconciler_version": policy.RECONCILER_VERSION,
                },
            )
        )

    # -- the pass --------------------------------------------------------------

    @staticmethod
    def _is_make_record(record: RawRecord) -> bool:
        """This pass reconciles MAKE records only, selected by the external
        id's KIND PREFIX.

        Payload shape is no longer sufficient, and the fix is load-bearing:
        a MODEL payload carries `make_id` and `make_name` too - deliberately,
        since that link is how a nameplate finds its make - so the original
        shape test read all 2,018 landed model records as makes and attached
        `model:<id>` external ids to *companies*. Caught by ADR 0010's tests
        before the next match run, so no live rows were affected. The kind
        prefix (the 2026-07-29 re-key) is the durable record-kind marker;
        payload shape is not, because record kinds legitimately share fields.
        """
        return (record.external_id or "").startswith("make:")

    def run(self) -> MatchStats:
        records = [
            r for r in _current_records(self.session, self.vpic.id) if self._is_make_record(r)
        ]
        records.sort(key=lambda r: int(r.payload["make_id"]))  # ascending MakeId
        log.info(
            "vPIC match pass: %d makes (reconciler v%s)", len(records), policy.RECONCILER_VERSION
        )

        for record in records:
            self.stats.processed += 1
            make_name = record.payload["make_name"]

            if record.external_id in self.vpic_ids:
                # Matched on a prior run; keep the role assertion current.
                self.stats.already_matched += 1
                self._assert_role(self.vpic_ids[record.external_id], record)
                self._mark(record)
                continue

            # The registry is keyed by the human-readable MakeId (what a
            # reviewer reads off a flag), not the namespaced external id.
            registry_qid = policy.VPIC_MATCHES.get(str(record.payload["make_id"]))
            if registry_qid is not None:
                company_id = self.wp.external_ids.get(registry_qid)
                if company_id is None:
                    # Registry points at a quarantined entity: corroborate it.
                    quarantined = [
                        r
                        for rs in self.quarantined.values()
                        for r in rs
                        if r.external_id == registry_qid
                    ]
                    company_id = (
                        self._corroborate_create(quarantined[0], record) if quarantined else None
                    )
                if company_id is None:
                    self._flag(record, make_name, [], f"registry QID {registry_qid} unresolvable")
                else:
                    self._attach(company_id, record)
                    self.stats.matched_existing += 1
                self._mark(record)
                continue

            norm = normalize_name(make_name)
            company_hits = self.by_name.get(norm, [])
            quarantine_hits = self.quarantined.get(norm, [])

            if len(company_hits) == 1 and not quarantine_hits:
                self._attach(company_hits[0], record)
                self.stats.matched_existing += 1
            elif len(quarantine_hits) == 1 and not company_hits:
                company_id = self._corroborate_create(quarantine_hits[0], record)
                if company_id is not None:
                    self._attach(company_id, record)
                else:
                    self._flag(record, make_name, [], "quarantined candidate unnameable")
            elif not company_hits and not quarantine_hits:
                self._flag(record, make_name, self._trigram_candidates(make_name), "no match")
            else:
                # Ambiguity is usually OUR duplicate identity (company/brand
                # splits - the Mazda x3 shape), so the reviewer needs enough
                # detail to pick a merge target, not bare ids.
                companies = {
                    c.id: c
                    for c in self.session.scalars(
                        select(Company).where(Company.id.in_(company_hits))
                    )
                }
                candidates = [
                    {
                        "kind": "company",
                        "slug": companies[cid].slug,
                        "name": companies[cid].name,
                        "summary": companies[cid].summary,
                    }
                    for cid in company_hits
                ] + [{"kind": "quarantined", "qid": r.external_id} for r in quarantine_hits]
                self._flag(record, make_name, candidates, "ambiguous")
            self._mark(record)

        return self.stats


def run_vpic_match_pass(session: Session) -> MatchStats:
    """Run the full match pass (ADR 0008). Commits on success."""
    stats = _MatchPass(session).run()
    session.commit()
    log.info("vPIC match pass done: %s", stats.summary())
    return stats
