"""The generation-placement pass (ADR 0016 §5): unique dated overlap.

For every configuration, the candidates are the generations linked to its
model (`generation_model_links` - source assertions, never inference) whose
placement span contains the configuration's catalogue period:

- The infobox `model_years` span, exact containment, when the period is
  `model_year`-kind and the generation's article asserts one - it is the
  same axis vPIC/EPA periods live on. Parsed from raw at decision time
  (deliberately not stored: no column for a US-specific reading).
- Otherwise the generation's production span (`start_year`/`end_year`
  columns) with END_SLACK extra years on the end: a US model year routinely
  outruns production by one calendar year, and the start gets no slack - a
  car cannot be catalogued before it is built.

Slack applies BEFORE the uniqueness test, so it can only ever add a flag,
never force a placement between two known generations. Exactly one
candidate places, with field-level provenance to the raw record whose span
decided it. Two or more flag `generation_overlap` per (model, period)
cluster - the 2019 AMG GT lands there by design. Zero is the normal state,
logged, unflagged.

The pass is the sole placer (ADR 0015's sole-source posture): when the
recomputed answer changes - evidence moved, or ambiguity appeared - the old
placement is superseded and the column follows, including back to NULL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from carmanac.db.models import (
    CataloguePeriod,
    Configuration,
    ExternalId,
    FieldProvenance,
    Generation,
    GenerationModelLink,
    PeriodKind,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import get_source
from carmanac.ingest.wikipedia import SOURCE_NAME
from carmanac.reconcile.bookkeeping import DecisionLog
from carmanac.reconcile.engine import supersede
from carmanac.reconcile.sources.wikipedia_infobox import Span, parse_infobox

log = logging.getLogger(__name__)

PASS_NAME = "generation_placement"

# The model-year-outruns-production allowance (ADR 0016 §5, accepted as the
# general rule; exceptions get dedicated decision passes, not a looser rule).
END_SLACK = 1


@dataclass
class GenerationPlacementStats:
    configurations: int = 0
    placed: int = 0
    already_placed: int = 0
    unplaced_no_candidate: int = 0
    overlap_flagged: int = 0
    withdrawn: int = 0
    flags_opened: int = 0
    flags_dismissed: int = 0

    def summary(self) -> str:
        return (
            f"configurations={self.configurations} placed={self.placed} "
            f"already={self.already_placed} no_candidate={self.unplaced_no_candidate} "
            f"overlap={self.overlap_flagged} withdrawn={self.withdrawn} | "
            f"flags={self.flags_opened} (dismissed={self.flags_dismissed})"
        )


@dataclass(frozen=True)
class _Candidate:
    generation_id: int
    span: Span
    exact: bool  # model_years containment (no slack) vs production+slack
    raw_record_id: int | None  # the record whose span decides


class _PlacementPass:
    def __init__(self, session: Session):
        self.session = session
        self.stats = GenerationPlacementStats()
        self.source = get_source(session, SOURCE_NAME)
        self.decisions = DecisionLog(session, self.source.id, PASS_NAME)

        self.generations: dict[int, Generation] = {
            g.id: g for g in session.scalars(select(Generation))
        }
        self.links: dict[int, list[int]] = {}
        for generation_id, model_id in session.execute(
            select(GenerationModelLink.generation_id, GenerationModelLink.model_id)
            .where(GenerationModelLink.superseded_by.is_(None))
            .distinct()
        ):
            self.links.setdefault(model_id, []).append(generation_id)

        self._load_model_year_spans()
        self._load_span_provenance()
        self._load_open_flags()
        self._load_live_placements()

    def _load_model_year_spans(self) -> None:
        """Per generation: the infobox `model_years` span, parsed from the
        raw record its QID's sitelink landed (decision-time read, ADR §4)."""
        self.model_year_spans: dict[int, tuple[Span, int]] = {}
        wikidata_id = self.session.scalar(select(Source.id).where(Source.name == "Wikidata"))
        qid_by_generation: dict[str, int] = {
            qid: generation_id
            for qid, generation_id in self.session.execute(
                select(ExternalId.external_id, ExternalId.generation_id).where(
                    ExternalId.source_id == wikidata_id,
                    ExternalId.generation_id.isnot(None),
                )
            )
        }
        if not qid_by_generation:
            return
        for record in self.session.scalars(
            select(RawRecord).where(
                RawRecord.source_id == self.source.id,
                RawRecord.external_id.in_([f"infobox:{qid}" for qid in qid_by_generation]),
            )
        ):
            generation_id = qid_by_generation[record.payload["qid"]]
            parsed = parse_infobox(record.payload["title"], record.payload.get("wikitext", ""))
            if parsed.model_years is not None:
                self.model_year_spans[generation_id] = (parsed.model_years, record.id)

    def _load_span_provenance(self) -> None:
        """Per generation: the raw record behind its live `start_year`
        assertion - what a production-span placement cites as its decider.
        The infobox source outranks (ADR §4), so prefer its row."""
        self.span_records: dict[int, int | None] = {}
        rows = self.session.execute(
            select(
                FieldProvenance.generation_id,
                FieldProvenance.source_id,
                FieldProvenance.raw_record_id,
            ).where(
                FieldProvenance.generation_id.isnot(None),
                FieldProvenance.field_name == "start_year",
                FieldProvenance.superseded_by.is_(None),
                FieldProvenance.observed_value.isnot(None),
            )
        ).all()
        for generation_id, source_id, raw_record_id in rows:
            if source_id == self.source.id or generation_id not in self.span_records:
                self.span_records[generation_id] = raw_record_id

    def _load_open_flags(self) -> None:
        self.open_flags: dict[int, ReconciliationFlag] = {
            flag.configuration_id: flag
            for flag in self.session.scalars(
                select(ReconciliationFlag).where(
                    ReconciliationFlag.kind == "generation_overlap",
                    ReconciliationFlag.status == "open",
                )
            )
        }

    def _load_live_placements(self) -> None:
        """This pass's live placement assertions, keyed by configuration -
        loaded once; per-row SELECTs would dominate a 23k-row run."""
        self.live_placements: dict[int, FieldProvenance] = {
            row.configuration_id: row
            for row in self.session.scalars(
                select(FieldProvenance).where(
                    FieldProvenance.configuration_id.isnot(None),
                    FieldProvenance.field_name == "generation_id",
                    FieldProvenance.source_id == self.source.id,
                    FieldProvenance.superseded_by.is_(None),
                )
            )
        }

    def _candidates(self, model_id: int, period: CataloguePeriod, kind: str) -> list[_Candidate]:
        found: list[_Candidate] = []
        for generation_id in self.links.get(model_id, ()):
            model_years = self.model_year_spans.get(generation_id)
            if kind == "model_year" and model_years is not None:
                span, record_id = model_years
                if span.contains(period.start_year, period.end_year):
                    found.append(_Candidate(generation_id, span, True, record_id))
                continue
            generation = self.generations[generation_id]
            if generation.start_year is None:
                continue
            span = Span(generation.start_year, generation.end_year)
            if span.contains(period.start_year, period.end_year, end_slack=END_SLACK):
                found.append(
                    _Candidate(generation_id, span, False, self.span_records.get(generation_id))
                )
        return sorted(found, key=lambda c: c.generation_id)

    def _assert_placement(self, configuration: Configuration, candidate: _Candidate | None) -> None:
        """Write/refresh/withdraw the placement assertion + column. The pass
        is the sole placer, so the column follows the recomputed answer."""
        observed = (
            None
            if candidate is None
            else f"{self.generations[candidate.generation_id].slug}"
            f"[{candidate.span.start}–{candidate.span.end or 'present'}]"
        )
        live = self.live_placements.get(configuration.id)
        target = None if candidate is None else candidate.generation_id
        record_id = None if candidate is None else candidate.raw_record_id
        if live is None and observed is None:
            return
        if live is not None and live.observed_value == observed:
            configuration.generation_id = target
            return
        values = {
            "configuration_id": configuration.id,
            "field_name": "generation_id",
            "observed_value": observed,
            "source_id": self.source.id,
            "raw_record_id": record_id,
        }
        if live is None:
            row = FieldProvenance(**values)
            self.session.add(row)
            self.live_placements[configuration.id] = row
        else:
            self.live_placements[configuration.id] = supersede(self.session, live, values)
        configuration.generation_id = target

    def _flag_overlap(
        self, configuration: Configuration, candidates: list[_Candidate], detail: dict
    ) -> None:
        flag = self.open_flags.get(configuration.id)
        if flag is not None:
            if flag.detail != detail:
                flag.detail = detail
            return
        self.session.add(
            ReconciliationFlag(
                kind="generation_overlap",
                configuration_id=configuration.id,
                field_name="generation_id",
                detail=detail,
                source_id=self.source.id,
                raw_record_id=candidates[0].raw_record_id,
            )
        )
        self.stats.flags_opened += 1

    def _dismiss_flag(self, configuration_id: int, resolution: str) -> None:
        flag = self.open_flags.pop(configuration_id, None)
        if flag is not None:
            flag.status = "dismissed"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": resolution}
            self.stats.flags_dismissed += 1

    def run(self) -> GenerationPlacementStats:
        kind_by_id: dict[int, str] = {
            k.id: k.code for k in self.session.scalars(select(PeriodKind))
        }
        rows = self.session.execute(
            select(Configuration, CataloguePeriod)
            .join(CataloguePeriod, Configuration.catalogue_period_id == CataloguePeriod.id)
            .order_by(Configuration.id)
        ).all()
        for configuration, period in rows:
            self.stats.configurations += 1
            key = f"configuration:{configuration.id}"
            candidates = self._candidates(
                period.model_id, period, kind_by_id[period.period_kind_id]
            )

            if len(candidates) == 1:
                candidate = candidates[0]
                already = configuration.generation_id == candidate.generation_id
                self._assert_placement(configuration, candidate)
                self._dismiss_flag(configuration.id, "resolved_to_single_candidate")
                if already:
                    self.stats.already_placed += 1
                else:
                    self.stats.placed += 1
                self.decisions.record_key(
                    key,
                    "placed_dated_overlap",
                    raw_record_id=candidate.raw_record_id,
                    method="model_years_exact" if candidate.exact else "production_end_slack",
                    detail={"generation": self.generations[candidate.generation_id].slug},
                )
                continue

            if len(candidates) > 1:
                if configuration.generation_id is not None:
                    self.stats.withdrawn += 1
                self._assert_placement(configuration, None)
                detail = {
                    "period": f"{period.start_year}–{period.end_year}",
                    "candidates": [
                        {
                            "generation": self.generations[c.generation_id].slug,
                            "span": f"{c.span.start}–{c.span.end or 'present'}",
                            "exact": c.exact,
                        }
                        for c in candidates
                    ],
                }
                self._flag_overlap(configuration, candidates, detail)
                self.stats.overlap_flagged += 1
                self.decisions.record_key(key, "flagged_generation_overlap", detail=detail)
                continue

            if configuration.generation_id is not None:
                self.stats.withdrawn += 1
            self._assert_placement(configuration, None)
            self._dismiss_flag(configuration.id, "candidates_no_longer_overlap")
            self.stats.unplaced_no_candidate += 1
            self.decisions.record_key(key, "waits_no_dated_generation")

        self.decisions.flush()
        self.session.commit()
        return self.stats


def run_generation_placement_pass(session: Session) -> GenerationPlacementStats:
    stats = _PlacementPass(session).run()
    log.info("generation placement pass done: %s", stats.summary())
    return stats
