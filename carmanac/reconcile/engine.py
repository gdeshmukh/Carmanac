"""The reconciliation engine (ADR 0007 §1-§2, §5-§6).

Source-agnostic: everything hard and shared lives here - the identity ladder,
admission, the supersede dance, conflict resolution, projection, flags, and
state tracking. It never inspects a payload; a per-source mapper translates
each raw record into the `types.py` contract, and adding a source is a new
mapper plus policy registrations, not an engine change.

The pass is a deterministic, idempotent projection:

    raw_records  --resolve--> companies + external_ids
                 --assert---> field_provenance + company_role_assignments
                 --project--> entity columns (the winner per field)

Determinism preconditions (ADR 0007 §1, amendment): the reconciliation unit is
the CURRENT record per (source, external_id) - greatest `last_seen_at`, ties
by greatest id - processed in ascending external-id order (numeric where the
id is a QID), which doubles as the slug-collision tiebreak. Re-running over
unchanged records is a no-op; over changed records it supersedes, never
appends; raw records are never modified.

Curated identity merges (§5): member QIDs attach their `external_ids` rows to
the canonical entity's company, but only the CANONICAL record writes fact
assertions. Three era-records asserting `name` onto one company would other-
wise contend for the same (entity, field, source) live slot and every full
run would supersede in a cycle - churn, not convergence. The canonical
entity's record is the identity anchor; the members' payloads wait in raw
like everything else.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from types import ModuleType

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from carmanac.db.models import (
    Company,
    CompanyRole,
    CompanyRoleAssignment,
    Country,
    ExternalId,
    FieldProvenance,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.reconcile import policy
from carmanac.reconcile.bookkeeping import mark_reconciled
from carmanac.reconcile.types import Assertion, MappedRecord

log = logging.getLogger(__name__)

MANUFACTURER_ROLE_CODE = "manufacturer"

_QID_NUM = re.compile(r"^Q(\d+)$")


@dataclass
class PassStats:
    """What one companies pass did. Every counter is per-record or per-row."""

    processed: int = 0
    admitted: int = 0
    denied: int = 0
    quarantined: int = 0
    companies_created: int = 0
    assertions_inserted: int = 0
    assertions_superseded: int = 0
    tombstones: int = 0
    roles_asserted: int = 0
    flags_opened: int = 0
    flags_dismissed: int = 0
    merged_members: int = 0

    def summary(self) -> str:
        return (
            f"processed={self.processed} admitted={self.admitted} "
            f"denied={self.denied} quarantined={self.quarantined} | "
            f"companies_created={self.companies_created} "
            f"assertions={self.assertions_inserted} "
            f"(superseded={self.assertions_superseded}, tombstones={self.tombstones}) "
            f"roles={self.roles_asserted} "
            f"flags={self.flags_opened} (dismissed={self.flags_dismissed}) "
            f"merged_members={self.merged_members}"
        )


def slugify(name: str, external_id: str) -> str:
    """Deterministic display slug (ADR 0007 §7): ASCII-folded, lowercased,
    hyphenated. A name with no ASCII representation (e.g. fully CJK) falls
    back to the external id, which is stable and unique by construction."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return slug or external_id.lower()


def _sort_key(external_id: str) -> tuple[int, int | str]:
    """Ascending external-id order, numeric for QIDs: Q9 before Q10 before
    Q100. Lexical sorting would visit Q10 before Q9 and make the slug-collision
    tiebreak depend on string length - deterministic but surprising."""
    m = _QID_NUM.match(external_id)
    return (0, int(m.group(1))) if m else (1, external_id)


def current_records(session: Session, source_id: int, sweep: str | None = None) -> list[RawRecord]:
    """The reconciliation units: current record per external_id (§1).

    `sweep` partitions a source's records by the landing-stamped payload
    marker (ADR 0012 §1): the models sweep lands the same bare QIDs the
    makes sweep does, with a different payload shape, and without the
    partition whichever sweep landed an entity most recently would shadow
    the other pass's view of it. None - the default, and what every
    pre-marker payload has - selects records with NO marker.
    """
    marker = RawRecord.payload.op("->>")("sweep")
    subq = (
        select(RawRecord)
        .where(
            RawRecord.source_id == source_id,
            RawRecord.external_id.isnot(None),
            marker.is_(None) if sweep is None else marker == sweep,
        )
        .order_by(
            RawRecord.external_id,
            RawRecord.last_seen_at.desc(),
            RawRecord.id.desc(),
        )
        .distinct(RawRecord.external_id)
    )
    records = list(session.scalars(subq))
    records.sort(key=lambda r: _sort_key(r.external_id))
    return records


def supersede(
    session: Session,
    old: FieldProvenance,
    new_values: dict,
) -> FieldProvenance:
    """The three-step dance (§1): retire the old row by pointing
    `superseded_by` at ITSELF (which frees the live slot under
    `uq_field_provenance_live`), insert the successor, then repoint the old
    row at it. One flush per step, so the partial unique index sees each
    state. The obvious order (insert, then repoint) cannot work: the live
    index rejects the second row before the first can be retired."""
    old.superseded_by = old.id
    session.flush()
    successor = FieldProvenance(**new_values)
    session.add(successor)
    session.flush()
    old.superseded_by = successor.id
    session.flush()
    return successor


def assert_field_facts(
    session: Session,
    *,
    arc_col: str,
    entity,
    coverage: tuple[str, ...],
    facts: dict[str, tuple[str, object]],
    source_id: int,
    record,
    skip_projection: frozenset[str] = frozenset(),
) -> tuple[int, int]:
    """Upsert one record's assertions for one entity into `field_provenance`,
    tombstone coverage fields the source went quiet on, and project winners
    onto the entity columns. Returns (inserted, superseded).

    `skip_projection` is how a field-scoped precedence ruling gets mechanical
    teeth (ADR 0016 §4: infobox assertions outrank label-derived ones): the
    outranked pass still keeps its assertion current in `field_provenance` -
    the evidence trail must not thin out - but leaves the entity column to
    the source that outranks it.
    """
    inserted = superseded = 0
    live: dict[str, FieldProvenance] = {
        row.field_name: row
        for row in session.scalars(
            select(FieldProvenance).where(
                getattr(FieldProvenance, arc_col) == entity.id,
                FieldProvenance.source_id == source_id,
                FieldProvenance.superseded_by.is_(None),
                FieldProvenance.field_name.in_(coverage),
            )
        )
    }
    for field_name in coverage:
        fact = facts.get(field_name)
        row = live.get(field_name)
        if fact is None:
            if row is not None and row.observed_value is not None:
                supersede(
                    session,
                    row,
                    {
                        arc_col: entity.id,
                        "field_name": field_name,
                        "observed_value": None,
                        "source_id": source_id,
                        "raw_record_id": record.id,
                        "scraped_at": record.last_seen_at,
                    },
                )
                if field_name not in skip_projection:
                    setattr(entity, field_name, None)
            continue
        observed, projected = fact
        values = {
            arc_col: entity.id,
            "field_name": field_name,
            "observed_value": observed,
            "source_id": source_id,
            "raw_record_id": record.id,
            "scraped_at": record.last_seen_at,
        }
        if row is None:
            session.add(FieldProvenance(**values))
            inserted += 1
        elif row.observed_value != observed:
            supersede(session, row, values)
            inserted += 1
            superseded += 1
        if field_name not in skip_projection:
            setattr(entity, field_name, projected)
    return inserted, superseded


class CompaniesPass:
    """One run of the companies pass over a source's records.

    `run()` is the whole pass. `admit_entity()` / `apply_facts()` are the
    pieces other passes reuse when they admit an entity on their own evidence.
    Holds per-run caches, so it is not reusable across runs."""

    def __init__(self, session: Session, mapper: ModuleType):
        self.session = session
        self.mapper = mapper
        self.stats = PassStats()
        self.current_year = datetime.now(UTC).year

        self.source = session.scalars(select(Source).where(Source.name == mapper.SOURCE_NAME)).one()
        self.role_id = session.scalars(
            select(CompanyRole.id).where(CompanyRole.code == MANUFACTURER_ROLE_CODE)
        ).one()
        self.countries: dict[str, int] = dict(
            session.execute(select(Country.code, Country.id)).all()
        )
        # (source, external_id) -> company_id, the identity ladder's rung 1.
        self.external_ids: dict[str, int] = dict(
            session.execute(
                select(ExternalId.external_id, ExternalId.company_id).where(
                    ExternalId.source_id == self.source.id,
                    ExternalId.company_id.isnot(None),
                )
            ).all()
        )
        self.slugs: set[str] = set(session.scalars(select(Company.slug)))
        # Open-flag dedup keys: entity-scoped and record-scoped shapes.
        # Record-scoped flags key on EXTERNAL id, not raw_record id: a changed
        # payload lands a new raw row for the same entity, and its still-open
        # quarantine question must not be asked twice. The same keying lets an
        # admission dismiss the flag its entity outgrew.
        self.open_flags: set[tuple] = set()
        self.open_admission: dict[str, list[ReconciliationFlag]] = {}
        for flag, external_id in session.execute(
            select(ReconciliationFlag, RawRecord.external_id)
            .outerjoin(RawRecord, ReconciliationFlag.raw_record_id == RawRecord.id)
            .where(ReconciliationFlag.status == "open")
        ):
            if flag.kind == "admission_review":
                self.open_flags.add(("record", external_id, flag.kind))
                self.open_admission.setdefault(external_id, []).append(flag)
            else:
                self.open_flags.add(("company", flag.company_id, flag.kind, flag.field_name))

    # --- flags ---------------------------------------------------------------

    def _open_record_flag(self, record: RawRecord, detail: dict) -> None:
        key = ("record", record.external_id, "admission_review")
        if key in self.open_flags:
            return
        flag = ReconciliationFlag(
            kind="admission_review",
            raw_record_id=record.id,
            source_id=self.source.id,
            detail=detail,
        )
        self.session.add(flag)
        self.open_flags.add(key)
        self.open_admission.setdefault(record.external_id, []).append(flag)
        self.stats.flags_opened += 1

    def _dismiss_admission_flags(self, record: RawRecord) -> None:
        """An entity that admits has outgrown its quarantine question: the
        situation resolved itself (a payload or policy change), and the
        recorded reason keeps it usable as a labeled-set example."""
        for flag in self.open_admission.pop(record.external_id, []):
            flag.status = "dismissed"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": "entity_admitted"}
            self.stats.flags_dismissed += 1
        self.open_flags.discard(("record", record.external_id, "admission_review"))

    def _open_company_flag(
        self,
        company_id: int,
        kind: str,
        field_name: str | None,
        detail: dict | None,
        record: RawRecord,
    ) -> None:
        key = ("company", company_id, kind, field_name)
        if key in self.open_flags:
            return
        self.session.add(
            ReconciliationFlag(
                kind=kind,
                company_id=company_id,
                field_name=field_name,
                detail=detail,
                source_id=self.source.id,
                raw_record_id=record.id,
            )
        )
        self.open_flags.add(key)
        self.stats.flags_opened += 1

    # --- identity (§5) -------------------------------------------------------

    def _resolve_company(self, mapped: MappedRecord, record: RawRecord) -> Company | None:
        """The identity ladder: external-id hit -> that entity; miss -> create
        (admitted records only) and write the external_ids row in the same
        transaction. Merge members resolve through their canonical QID."""
        qid = mapped.external_id
        canonical = policy.IDENTITY_MERGES.get(qid, qid)

        company_id = self.external_ids.get(qid)
        if company_id is not None:
            return self.session.get(Company, company_id)

        if canonical != qid:
            # Member of a curated merge: attach identity to the canonical
            # company; never create from a member record.
            canonical_company_id = self.external_ids.get(canonical)
            if canonical_company_id is None:
                self._open_record_flag(
                    record,
                    {"reason": "merge_canonical_missing", "canonical": canonical},
                )
                return None
            self._attach_external_id(qid, canonical_company_id)
            self.stats.merged_members += 1
            return self.session.get(Company, canonical_company_id)

        if mapped.name is None:
            # Admitted by class but unnameable - a company row needs a name.
            self._open_record_flag(record, {"reason": "no_usable_label"})
            return None

        slug = slugify(mapped.name, qid)
        if slug in self.slugs:
            slug = f"{slug}-{qid.lower()}"  # §7: QID suffix on collision
        company = Company(slug=slug, name=mapped.name)
        self.session.add(company)
        self.session.flush()
        self.slugs.add(slug)
        self._attach_external_id(qid, company.id)
        self.stats.companies_created += 1
        return company

    def _attach_external_id(self, qid: str, company_id: int) -> None:
        self.session.add(
            ExternalId(company_id=company_id, source_id=self.source.id, external_id=qid)
        )
        self.session.flush()
        self.external_ids[qid] = company_id

    # --- assertions (§1) -----------------------------------------------------

    def _write_assertions(
        self, company: Company, mapped: MappedRecord, record: RawRecord
    ) -> dict[str, Assertion | None]:
        """Upsert this record's assertions into `field_provenance`; tombstone
        coverage fields the source went quiet on. Returns the current state
        per coverage field (None = tombstoned/silent) for projection."""
        live: dict[str, FieldProvenance] = {
            row.field_name: row
            for row in self.session.scalars(
                select(FieldProvenance).where(
                    FieldProvenance.company_id == company.id,
                    FieldProvenance.source_id == self.source.id,
                    FieldProvenance.superseded_by.is_(None),
                    FieldProvenance.field_name.in_(self.mapper.COVERAGE),
                )
            )
        }
        current: dict[str, Assertion | None] = dict.fromkeys(self.mapper.COVERAGE)

        for assertion in mapped.assertions:
            current[assertion.field_name] = assertion
            row = live.get(assertion.field_name)
            values = dict(
                company_id=company.id,
                field_name=assertion.field_name,
                observed_value=assertion.observed_value,
                source_id=self.source.id,
                raw_record_id=record.id,
                scraped_at=record.last_seen_at,
            )
            if row is None:
                self.session.add(FieldProvenance(**values))
                self.stats.assertions_inserted += 1
            elif row.observed_value != assertion.observed_value:
                supersede(self.session, row, values)
                self.stats.assertions_inserted += 1
                self.stats.assertions_superseded += 1
            # equal observed value -> no-op: idempotency.

        # Tombstones (§1): a field this mapper covers, previously asserted
        # live, absent from the current record -> supersede with a NULL
        # observed value ("this source went quiet here"), dating the
        # retraction. A live tombstone is not re-tombstoned.
        for field_name, row in live.items():
            if current[field_name] is None and row.observed_value is not None:
                supersede(
                    self.session,
                    row,
                    dict(
                        company_id=company.id,
                        field_name=field_name,
                        observed_value=None,
                        source_id=self.source.id,
                        raw_record_id=record.id,
                        scraped_at=record.last_seen_at,
                    ),
                )
                self.stats.tombstones += 1

        return current

    # --- roles (§4) ----------------------------------------------------------

    def _write_role(self, company: Company, mapped: MappedRecord, record: RawRecord) -> None:
        target_hits = sorted(mapped.classes & set(policy.TARGET_CLASSES))
        live = self.session.scalars(
            select(CompanyRoleAssignment).where(
                CompanyRoleAssignment.company_id == company.id,
                CompanyRoleAssignment.company_role_id == self.role_id,
                CompanyRoleAssignment.source_id == self.source.id,
                CompanyRoleAssignment.superseded_by.is_(None),
            )
        ).first()

        if target_hits and live is None:
            self.session.add(
                CompanyRoleAssignment(
                    company_id=company.id,
                    company_role_id=self.role_id,
                    source_id=self.source.id,
                    raw_record_id=record.id,
                    scraped_at=record.last_seen_at,
                )
            )
            self.stats.roles_asserted += 1
        elif not target_hits and live is not None:
            # The source no longer classes this entity as any target class:
            # retire this source's role assertion (per-source retraction is
            # supersession; the role holds if any OTHER source still says so).
            live.superseded_by = live.id
            self.session.flush()

    # --- projection (§6) -----------------------------------------------------

    def _project(self, company: Company, current: dict[str, Assertion | None]) -> None:
        """Project winners onto the entity columns.

        §6's full ladder (tier -> affinity -> recency -> flag) becomes
        exercisable at the second source; with one source the winner per field
        is its single live assertion, and a tombstoned field projects NULL.
        `name` never projects NULL - the column is NOT NULL, identity keeps
        its last known name until a source asserts a new one.
        """
        for field_name, assertion in current.items():
            if assertion is None:
                if field_name != "name":
                    setattr(company, field_name, None)
                continue
            value = assertion.value
            if field_name == "country_id":
                country_id = self.countries.get(str(value))
                if country_id is None:
                    log.warning(
                        "unknown ISO country code %r for %s - not projecting",
                        value,
                        company.slug,
                    )
                    continue
                value = country_id
            setattr(company, field_name, value)

    def _check_plausibility(self, company: Company, record: RawRecord) -> None:
        """Post-projection sanity rules (policy v3). A single wrong claim has
        no disagreement for multi_value to catch - AMG's lone "founded 1812" -
        so bounds and cross-field checks flag it here. The value stays
        projected (§6.4); the flag records the suspicion."""
        for field_name, reason in policy.plausibility_issues(
            company.founded_year, company.defunct_year, self.current_year
        ):
            self._open_company_flag(
                company.id,
                "implausible_value",
                field_name,
                {"value": getattr(company, field_name), "reason": reason},
                record,
            )

    # --- the public entry points ---------------------------------------------

    def apply_facts(self, company: Company, mapped: MappedRecord, record: RawRecord) -> None:
        """Assert this record's facts onto `company`, then project the winners.

        The four steps always travel together: an assertion nobody projects is
        invisible, and a projection nobody plausibility-checks is how AMG got a
        founding year of 1812.
        """
        current = self._write_assertions(company, mapped, record)
        self._write_role(company, mapped, record)
        self._project(company, current)
        self._check_plausibility(company, record)

    def admit_entity(self, record: RawRecord) -> Company | None:
        """Resolve `record` to a company and apply its facts. None if it cannot
        be resolved (unnameable, or a merge member whose canonical is missing).

        For passes that admit an entity on evidence this pass cannot see - the
        vPIC match pass corroborating a quarantined Wikidata entity - so that
        admission goes through exactly the same assertions, projection and
        plausibility checks as a class-based one.
        """
        mapped = self.mapper.map_record(record.payload)
        if mapped is None:
            return None
        company = self._resolve_company(mapped, record)
        if company is None:
            return None
        self.apply_facts(company, mapped, record)
        return company

    def resolve_admission_flags(self, external_id: str, resolution: str) -> int:
        """Close this entity's open admission questions with a stated reason.
        Returns how many were closed."""
        closed = 0
        for flag in self.open_admission.pop(external_id, []):
            flag.status = "resolved"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": resolution}
            closed += 1
        self.open_flags.discard(("record", external_id, "admission_review"))
        return closed

    # --- the pass ------------------------------------------------------------

    def run(self) -> PassStats:
        records = current_records(self.session, self.source.id)
        log.info(
            "companies pass: %d current records from %s (reconciler v%s)",
            len(records),
            self.source.name,
            policy.RECONCILER_VERSION,
        )

        for i, record in enumerate(records, 1):
            mapped = self.mapper.map_record(record.payload)
            if mapped is None:
                mark_reconciled(self.session, record)
                continue

            verdict = policy.classify(mapped.classes, mapped.external_id)
            # Cross-source admission (ADR 0008): an entity another source's
            # evidence already matched to a company - external_ids knows it -
            # is admitted regardless of its class verdict, so corroborated
            # companies keep receiving this source's assertions on re-runs.
            if verdict == policy.QUARANTINE and mapped.external_id in self.external_ids:
                verdict = policy.ADMIT
            # A curated merge is a human identity decision about both sides:
            # members must reach the identity ladder to attach to their
            # canonical, and a canonical with boilerplate-only classes
            # (Tesla, Inc.) is still a company we decided we hold.
            if verdict == policy.QUARANTINE and (
                mapped.external_id in policy.IDENTITY_MERGES
                or mapped.external_id in policy.MERGE_CANONICALS
            ):
                verdict = policy.ADMIT
            self.stats.processed += 1

            if verdict == policy.DENY:
                self.stats.denied += 1
            elif verdict == policy.QUARANTINE:
                self.stats.quarantined += 1
                self._open_record_flag(
                    record,
                    {
                        "reason": "unclassified_classes",
                        "classes": sorted(
                            c
                            for c in mapped.classes
                            if c not in policy.TARGET_CLASSES
                            and c not in policy.ALLOW_CLASSES
                            and c not in policy.DENY_CLASSES
                        ),
                        "all_classes": sorted(mapped.classes),
                        "label": mapped.name,
                    },
                )
            else:
                company = self._resolve_company(mapped, record)
                if company is None:
                    self.stats.quarantined += 1
                else:
                    self.stats.admitted += 1
                    self._dismiss_admission_flags(record)
                    is_merge_member = (
                        policy.IDENTITY_MERGES.get(mapped.external_id, mapped.external_id)
                        != mapped.external_id
                    )
                    if not is_merge_member:
                        self.apply_facts(company, mapped, record)
                        for req in mapped.flag_requests:
                            self._open_company_flag(
                                company.id, req.kind, req.field_name, req.detail, record
                            )

            mark_reconciled(self.session, record)
            if i % 1000 == 0:
                log.info("  ... %d/%d", i, len(records))

        return self.stats


def run_companies_pass(session: Session, mapper: ModuleType) -> PassStats:
    """Run the full companies pass for one source's mapper. Commits on success."""
    stats = CompaniesPass(session, mapper).run()
    session.commit()
    log.info("companies pass done: %s", stats.summary())
    return stats
