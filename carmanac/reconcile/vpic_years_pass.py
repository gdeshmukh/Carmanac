"""The vPIC year pass (ADR 0014): the first catalogue periods.

Per current `modelyears:<ModelId>` record: resolve the model through the
`model:<ModelId>` external id, then ensure a `model_year` period
(`start_year = end_year = year`) exists under the model for every year in
the payload's list. A period is pure time under its model - exactly the
assertion vPIC makes, no more (ADR 0014 §1-2). No generation links: the
placement is configuration-level and evidence-gated, and this pass carries
no such evidence.

Periods are identity-level rows (ADR 0002: no provenance columns); their
existence is justified by the current raw record, and the decision log
records what each record produced. A record whose model is not held (the
~280 models under unmatched makes) waits, unflagged - the make's own open
question must not fan out (ADR 0010 §1's posture, one level down).

Batch-committed (ADR 0014 §5): the first write pass at tens-of-thousands
scale commits per chunk so an interrupted run keeps its progress; the
natural key makes the re-run converge instead of duplicate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import CataloguePeriod, ExternalId, MatchDecision, PeriodKind, RawRecord
from carmanac.ingest.landing import get_source
from carmanac.ingest.vpic.land import VPIC_SOURCE_NAME
from carmanac.reconcile import policy
from carmanac.reconcile.engine import _current_records

log = logging.getLogger(__name__)

PASS_NAME = "vpic_years"
_PREFIX = "modelyears:"
_COMMIT_CHUNK = 500
_DECISION_CHUNK = 500


@dataclass
class VpicYearsPassStats:
    """What one year pass did. Every counter is per-record or per-row."""

    processed: int = 0
    waits_unmatched_model: int = 0
    periods_created: int = 0
    periods_existing: int = 0

    def summary(self) -> str:
        return (
            f"processed={self.processed} "
            f"waits_unmatched_model={self.waits_unmatched_model} | "
            f"periods: created={self.periods_created} existing={self.periods_existing}"
        )


class _VpicYearsPass:
    """One run of the year pass. Holds the per-run caches; `run()` is the
    entry point. Deliberately not reusable across runs."""

    def __init__(self, session: Session):
        self.session = session
        self.stats = VpicYearsPassStats()
        self.source = get_source(session, VPIC_SOURCE_NAME)
        self.decisions: list[dict] = []

        kind = session.scalar(select(PeriodKind).where(PeriodKind.code == "model_year"))
        if kind is None:  # pragma: no cover - migration-seeded lookup
            raise RuntimeError("period_kinds seed row 'model_year' missing")
        self.model_year_kind_id: int = kind.id

        # `model:<id>` -> models.id, the same rung the models pass writes.
        self.model_by_external: dict[str, int] = {
            external_id: model_id
            for external_id, model_id in session.execute(
                select(ExternalId.external_id, ExternalId.model_id).where(
                    ExternalId.source_id == self.source.id,
                    ExternalId.external_id.like("model:%"),
                    ExternalId.model_id.isnot(None),
                )
            )
        }

        # (model_id, year) occupancy for the model_year kind - what makes the
        # re-run a no-op in memory instead of an IntegrityError per row.
        self.existing: set[tuple[int, int]] = {
            (model_id, start_year)
            for model_id, start_year in session.execute(
                select(CataloguePeriod.model_id, CataloguePeriod.start_year).where(
                    CataloguePeriod.period_kind_id == self.model_year_kind_id
                )
            )
        }

    def _decide(self, record: RawRecord, outcome: str, detail: dict | None = None) -> None:
        self.decisions.append(
            {
                "source_id": self.source.id,
                "pass_name": PASS_NAME,
                "external_id": record.external_id,
                "raw_record_id": record.id,
                "rung": "1",
                "method": "external_id",
                "outcome": outcome,
                "detail": detail,
                "reconciler_version": policy.RECONCILER_VERSION,
            }
        )

    def _flush_decisions(self) -> None:
        for start in range(0, len(self.decisions), _DECISION_CHUNK):
            chunk = self.decisions[start : start + _DECISION_CHUNK]
            stmt = pg_insert(MatchDecision).values(chunk)
            self.session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_match_decisions_source_id_pass_name_external_id",
                    set_={
                        "raw_record_id": stmt.excluded.raw_record_id,
                        "rung": stmt.excluded.rung,
                        "method": stmt.excluded.method,
                        "outcome": stmt.excluded.outcome,
                        "detail": stmt.excluded.detail,
                        "reconciler_version": stmt.excluded.reconciler_version,
                        "decided_at": func.now(),
                    },
                )
            )

    def run(self) -> VpicYearsPassStats:
        records = [
            r
            for r in _current_records(self.session, self.source.id)
            if r.external_id.startswith(_PREFIX)
        ]
        log.info(
            "vPIC year pass: %d modelyears records (reconciler v%s)",
            len(records),
            policy.RECONCILER_VERSION,
        )

        since_commit = 0
        for record in records:
            self.stats.processed += 1
            model_key = "model:" + record.external_id.removeprefix(_PREFIX)
            model_id = self.model_by_external.get(model_key)
            if model_id is None:
                self.stats.waits_unmatched_model += 1
                self._decide(record, "waits_unmatched_model")
                continue

            created = 0
            for year in record.payload.get("years") or []:
                if (model_id, year) in self.existing:
                    self.stats.periods_existing += 1
                    continue
                self.session.add(
                    CataloguePeriod(
                        model_id=model_id,
                        period_kind_id=self.model_year_kind_id,
                        start_year=year,
                        end_year=year,
                    )
                )
                self.existing.add((model_id, year))
                self.stats.periods_created += 1
                created += 1
            self._decide(
                record,
                "periods_written",
                {"years": len(record.payload.get("years") or []), "created": created},
            )

            since_commit += 1
            if since_commit >= _COMMIT_CHUNK:
                self.session.commit()
                since_commit = 0

        self._flush_decisions()
        self.session.commit()
        log.info("vPIC year pass done: %s", self.stats.summary())
        return self.stats


def run_vpic_years_pass(session: Session) -> VpicYearsPassStats:
    return _VpicYearsPass(session).run()
