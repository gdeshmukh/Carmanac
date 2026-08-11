"""The vPIC models pass (ADR 0010): the first `models` rows.

Nameplates under makes we already hold. For every landed vPIC passenger model
record, the pass is:

    1. the make must be MATCHED     - `make:<id>` -> company via external_ids
    2. identity ladder              - `model:<id>` -> the existing row, refresh
    3. otherwise create             - slug = slugify(model_name), per company
    4. slug collision under one company -> `match_review`, never auto-suffix

**Matched makes only** (§1): a model record under an unmatched make is marked
reconciled-seen and creates nothing, silently. The make's own `match_review`
flag already represents that open question, and one question must not fan out
into fifty model-shaped copies of itself. When the make converts, the next run
picks its models up mechanically.

**Collisions flag rather than suffix** (§2.3): two distinct ModelIds slugging
identically under one company are almost always the same nameplate seen twice
(SANTANA 13765 and LAND ROVER SANTANA 13766 both resolve to Santana Motor, so
its licensee-built `110" WB` arrives from both), and minting `110-wb-2` would
manufacture exactly the duplicate identity the company/brand merges just spent
a session cleaning up. The lower ModelId keeps the slug - ascending order is
the engine's own deterministic tiebreak - and the higher one waits for a human.

**`models.name` is a reconciled fact** (§3), asserted into `field_provenance`
under the model arc and projected onto the column, so Wikidata can arbitrate
casing and naming later through the normal §6 machinery. Nothing else is
asserted: no generations, no periods, no roles (§4). Only the nameplate level.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from carmanac.db.models import (
    ExternalId,
    FieldProvenance,
    Model,
    RawRecord,
    ReconciliationFlag,
)
from carmanac.ingest.landing import get_source
from carmanac.ingest.vpic.land import VPIC_SOURCE_NAME
from carmanac.reconcile import policy
from carmanac.reconcile.bookkeeping import mark_reconciled
from carmanac.reconcile.engine import current_records, slugify, supersede

log = logging.getLogger(__name__)

# The one field this pass asserts. A model record with no name never becomes a
# row at all, so - unlike the companies pass - there is no tombstone path here:
# vPIC cannot go quiet on `name` while still asserting the model exists.
COVERAGE: tuple[str, ...] = ("name",)


@dataclass
class VpicModelsPassStats:
    """What one models pass did. Every counter is per-record or per-row."""

    processed: int = 0
    skipped_unmatched_make: int = 0
    models_created: int = 0
    models_matched: int = 0
    assertions_inserted: int = 0
    assertions_superseded: int = 0
    flags_opened: int = 0
    flags_dismissed: int = 0

    def summary(self) -> str:
        return (
            f"processed={self.processed} "
            f"skipped_unmatched_make={self.skipped_unmatched_make} | "
            f"models_created={self.models_created} matched={self.models_matched} "
            f"assertions={self.assertions_inserted} "
            f"(superseded={self.assertions_superseded}) "
            f"flags={self.flags_opened} (dismissed={self.flags_dismissed})"
        )


class _VpicModelsPass:
    """One run of the models pass. Holds the per-run caches; `run()` is the
    entry point. Deliberately not reusable across runs."""

    def __init__(self, session: Session):
        self.session = session
        self.stats = VpicModelsPassStats()
        self.source = get_source(session, VPIC_SOURCE_NAME)

        # `make:<id>` -> company_id: the matched makes, and the gate on §1.
        # `model:<id>` -> models.id: the identity ladder's rung 1. Both live in
        # `external_ids` under this one source, told apart by the kind prefix.
        self.company_by_make: dict[str, int] = {}
        self.model_by_external: dict[str, int] = {}
        for external_id, company_id, model_id in self.session.execute(
            select(ExternalId.external_id, ExternalId.company_id, ExternalId.model_id).where(
                ExternalId.source_id == self.source.id
            )
        ):
            if company_id is not None and external_id.startswith("make:"):
                self.company_by_make[external_id] = company_id
            elif model_id is not None and external_id.startswith("model:"):
                self.model_by_external[external_id] = model_id

        # (company_id, slug) -> models.id, the natural key `uq_models_company_
        # id_slug` enforces. Occupancy is what makes a collision detectable
        # before the INSERT rather than as an IntegrityError.
        self.by_company_slug: dict[tuple[int, str], int] = {
            (company_id, slug): model_id
            for model_id, company_id, slug in self.session.execute(
                select(Model.id, Model.company_id, Model.slug)
            )
        }

        # Open match_review flags on this source's MODEL records, for dedupe
        # and dismissal. Keyed on the record's external id, not its raw id: a
        # changed payload lands a new raw row for the same model, and its still
        # open question must not be asked twice (the engine's keying, same
        # reason). Make-record flags are excluded by the kind prefix.
        self.open_flags: dict[str, list[ReconciliationFlag]] = {}
        for flag, external_id in self.session.execute(
            select(ReconciliationFlag, RawRecord.external_id)
            .join(RawRecord, ReconciliationFlag.raw_record_id == RawRecord.id)
            .where(
                ReconciliationFlag.kind == "match_review",
                ReconciliationFlag.status == "open",
                RawRecord.source_id == self.source.id,
                RawRecord.external_id.like("model:%"),
            )
        ):
            self.open_flags.setdefault(external_id, []).append(flag)

    # --- flags ---------------------------------------------------------------

    def _flag(self, record: RawRecord, reason: str, detail: dict) -> None:
        if record.external_id in self.open_flags:
            return
        flag = ReconciliationFlag(
            kind="match_review",
            raw_record_id=record.id,
            source_id=self.source.id,
            detail={"reason": reason, **detail},
        )
        self.session.add(flag)
        self.open_flags[record.external_id] = [flag]
        self.stats.flags_opened += 1

    def _dismiss_flags(self, record: RawRecord) -> None:
        """A record that now resolves to a row has outgrown its question - a
        human merged the nameplates, or the payload changed. The recorded
        reason is what keeps the close usable as a labeled-set example; a
        dismissal with no reason teaches nothing."""
        for flag in self.open_flags.pop(record.external_id, []):
            flag.status = "dismissed"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": "record_resolves_to_model"}
            self.stats.flags_dismissed += 1

    # --- identity (§2) -------------------------------------------------------

    def _resolve_model(self, record: RawRecord, company_id: int) -> Model | None:
        """The identity ladder. None means the record was flagged and creates
        nothing this run."""
        model_id = self.model_by_external.get(record.external_id)
        if model_id is not None:
            self.stats.models_matched += 1
            self._dismiss_flags(record)
            return self.session.get(Model, model_id)

        name = (record.payload.get("model_name") or "").strip()
        if not name:
            # Not expected from vPIC (a ModelId always carries a Model_Name),
            # but a nameless record cannot mint a nameplate: `models.name` is
            # NOT NULL and the slug derives from it.
            self._flag(record, "no_usable_label", {"model_id": record.payload.get("model_id")})
            return None

        slug = slugify(name)
        if not slug:
            self._flag(record, "needs_curated_slug", {"model_name": name})
            return None
        if slug in policy.RESERVED_ROUTE_SEGMENTS:
            # Models own the bare segment under /<company>/ - except
            # the literals other kinds live under (ADR 0019 §6).
            self._flag(record, "reserved_segment", {"model_name": name, "slug": slug})
            return None
        occupant_id = self.by_company_slug.get((company_id, slug))
        if occupant_id is not None:
            occupant = self.session.get(Model, occupant_id)
            self._flag(
                record,
                "slug_collision",
                {
                    "model_name": name,
                    "slug": slug,
                    "make_name": record.payload.get("make_name"),
                    "existing_model": {"id": occupant.id, "name": occupant.name},
                    # What the reviewer must decide: same nameplate (attach this
                    # ModelId to the existing row) or genuinely two (suffix one).
                    "resolution": "merge_or_suffix",
                },
            )
            return None

        model = Model(company_id=company_id, slug=slug, name=name)
        self.session.add(model)
        self.session.flush()
        self.by_company_slug[(company_id, slug)] = model.id
        self.session.add(
            ExternalId(
                model_id=model.id,
                source_id=self.source.id,
                external_id=record.external_id,
            )
        )
        self.session.flush()
        self.model_by_external[record.external_id] = model.id
        self.stats.models_created += 1
        self._dismiss_flags(record)
        return model

    # --- assertions + projection (§3) ----------------------------------------

    def _assert_name(self, model: Model, record: RawRecord) -> None:
        """Assert `name` into `field_provenance` under the model arc, then
        project it. With one source the winner is its single live assertion;
        §6's tier -> affinity -> recency ladder becomes exercisable when
        Wikidata models land."""
        name = record.payload["model_name"].strip()
        live = self.session.scalars(
            select(FieldProvenance).where(
                FieldProvenance.model_id == model.id,
                FieldProvenance.source_id == self.source.id,
                FieldProvenance.superseded_by.is_(None),
                FieldProvenance.field_name.in_(COVERAGE),
            )
        ).first()
        values = dict(
            model_id=model.id,
            field_name="name",
            observed_value=name,
            source_id=self.source.id,
            raw_record_id=record.id,
            scraped_at=record.last_seen_at,
        )
        if live is None:
            self.session.add(FieldProvenance(**values))
            self.stats.assertions_inserted += 1
        elif live.observed_value != name:
            supersede(self.session, live, values)
            self.stats.assertions_inserted += 1
            self.stats.assertions_superseded += 1
        # equal observed value -> no-op: idempotency.

        model.name = name

    # --- the pass ------------------------------------------------------------

    @staticmethod
    def _is_model_record(record: RawRecord) -> bool:
        """MODEL records only, selected by the external id's KIND PREFIX.

        Payload shape must NOT be used: `modelyears:<id>` payloads carry
        model_id, make_id and make_name too, and a shape test reads them as
        model records. Kinds share fields legitimately; only the namespaced id
        says what a record IS - the same reason the match pass keys on it.
        """
        return (record.external_id or "").startswith("model:")

    def run(self) -> VpicModelsPassStats:
        records = [
            r for r in current_records(self.session, self.source.id) if self._is_model_record(r)
        ]
        records.sort(key=lambda r: int(r.payload["model_id"]))  # ascending ModelId
        log.info(
            "vPIC models pass: %d model records (reconciler v%s)",
            len(records),
            policy.RECONCILER_VERSION,
        )

        for i, record in enumerate(records, 1):
            self.stats.processed += 1
            company_id = self.company_by_make.get(f"make:{record.payload['make_id']}")
            if company_id is None:
                # §1: the make is unmatched. Reconciled-seen, nothing created,
                # no flag - the make's own match_review flag is the question.
                self.stats.skipped_unmatched_make += 1
                mark_reconciled(self.session, record)
                continue

            model = self._resolve_model(record, company_id)
            if model is not None:
                self._assert_name(model, record)
            mark_reconciled(self.session, record)
            if i % 500 == 0:
                log.info("  ... %d/%d", i, len(records))

        return self.stats


def run_vpic_models_pass(session: Session) -> VpicModelsPassStats:
    """Run the full vPIC models pass (ADR 0010). Commits on success."""
    stats = _VpicModelsPass(session).run()
    session.commit()
    log.info("vPIC models pass done: %s", stats.summary())
    return stats
