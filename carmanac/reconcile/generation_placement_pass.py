"""The generation-placement pass (ADR 0017 §3): unique dated overlap.

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
candidate places - and only when EVERY generation linked to the model
carries a span: an undated sibling makes "unique among the dated" mean
nothing (the first live run placed a 1991 Celica convertible into
`celica-gt-four` because the GT-Four page was the only dated one), so
undated competitors hold the configuration in `waits_undated_competitor`
until their spans land. Placement cites the raw record whose span decided
it. Two or more candidates flag `generation_overlap` per (model, period)
cluster - the fallback when the year is the only evidence, never the goal
state. Zero is the normal state, logged, unflagged.

Body evidence is a candidate VETO, never an overlap tiebreaker (ADR 0017
§3/§4): a generation whose every asserted body contradicts the
configuration's door signal is not a candidate at all - a 4-door is never
a C190 candidate - and the veto strikes before both the containment test
and the undated count, so a contradicting undated sibling cannot hold a
placement hostage either. The configuration's signal comes from its EPA
raw record (VClass "Two Seaters"; the pv/lv door-volume fills) with
explicit body words in the trim string as fallback; the generation's
bodies come from its landed article's infobox at decision time. The
overlap flag remains only for rows carrying no body signal.

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
from carmanac.reconcile.sources.wikipedia_infobox import (
    Span,
    infobox_field,
    parse_infobox,
    parse_span,
)
from carmanac.reconcile.sources.wikipedia_sections import (
    BodySignal,
    epa_body_signal,
    generation_doors,
    parse_article,
    section_main_asserts,
    trim_body_signal,
)

log = logging.getLogger(__name__)

PASS_NAME = "generation_placement"

# The model-year-outruns-production allowance (ADR 0017 §3, accepted as the
# general rule; exceptions get dedicated decision passes, not a looser rule).
END_SLACK = 1


@dataclass
class GenerationPlacementStats:
    configurations: int = 0
    placed: int = 0
    already_placed: int = 0
    unplaced_no_candidate: int = 0
    undated_competitor: int = 0
    overlap_flagged: int = 0
    withdrawn: int = 0
    body_vetoed: int = 0
    flags_opened: int = 0
    flags_dismissed: int = 0

    def summary(self) -> str:
        return (
            f"configurations={self.configurations} placed={self.placed} "
            f"already={self.already_placed} no_candidate={self.unplaced_no_candidate} "
            f"undated_competitor={self.undated_competitor} "
            f"overlap={self.overlap_flagged} withdrawn={self.withdrawn} "
            f"body_vetoed={self.body_vetoed} | "
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

        self.model_year_spans: dict[int, tuple[Span, int]] = {}
        self.generation_doors: dict[int, frozenset[int]] = {}
        self._load_model_year_spans()
        self._load_section_evidence()
        self._load_span_provenance()
        self._load_body_signals()
        self._load_open_flags()
        self._load_live_placements()

    def _load_model_year_spans(self) -> None:
        """Per QID-attached generation: the infobox `model_years` span and
        `body_style` door counts, from its landed section-0 record or the
        full article's lead (which replaced it) - decision-time reads,
        ADR §3/§4."""
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
        landed: dict[str, RawRecord] = {}
        for record in self.session.scalars(
            select(RawRecord)
            .where(
                RawRecord.source_id == self.source.id,
                RawRecord.external_id.in_(
                    [f"infobox:{qid}" for qid in qid_by_generation]
                    + [f"article:{qid}" for qid in qid_by_generation]
                ),
            )
            .order_by(RawRecord.last_seen_at, RawRecord.id)
        ):
            # The full article supersedes the retired section-0 record.
            if record.external_id.startswith("article:") or record.payload["qid"] not in landed:
                landed[record.payload["qid"]] = record
        for qid, record in landed.items():
            generation_id = qid_by_generation[qid]
            wikitext = record.payload.get("wikitext", "")
            if record.external_id.startswith("article:"):
                wikitext = parse_article(record.payload["title"], wikitext).top_wikitext
            parsed = parse_infobox(record.payload["title"], wikitext)
            if parsed.model_years is not None:
                self.model_year_spans[generation_id] = (parsed.model_years, record.id)
            doors = generation_doors(wikitext)
            if doors:
                self.generation_doors[generation_id] = doors

    def _load_section_evidence(self) -> None:
        """Per section-born generation (`section:<QID>#<ordinal>` keys): the
        same decision-time reads, from its section of the landed article -
        `model_years` from the section's infobox, doors from its `body_style`
        with the article's top infobox as the nameplate-scope fallback. A
        fetched `{{Main}}` target (`section-main:` record, ADR 0018 §3)
        supplies both where the section itself has none, behind the same
        grain guards the sections pass applies."""
        by_qid: dict[str, dict[int, int]] = {}
        for external_id, generation_id in self.session.execute(
            select(ExternalId.external_id, ExternalId.generation_id).where(
                ExternalId.source_id == self.source.id,
                ExternalId.generation_id.isnot(None),
                ExternalId.external_id.like("section:%"),
            )
        ):
            qid, _, ordinal = external_id.removeprefix("section:").partition("#")
            by_qid.setdefault(qid, {})[int(ordinal)] = generation_id
        if not by_qid:
            return
        section_mains: dict[tuple[str, int], RawRecord] = {}
        for record in self.session.scalars(
            select(RawRecord)
            .where(
                RawRecord.source_id == self.source.id,
                RawRecord.external_id.like("section-main:%"),
            )
            .order_by(RawRecord.last_seen_at, RawRecord.id)
        ):
            qid, _, ordinal = record.external_id.removeprefix("section-main:").partition("#")
            section_mains[(qid, int(ordinal))] = record
        for record in self.session.scalars(
            select(RawRecord)
            .where(
                RawRecord.source_id == self.source.id,
                RawRecord.external_id.in_([f"article:{qid}" for qid in by_qid]),
            )
            .order_by(RawRecord.last_seen_at, RawRecord.id)
        ):
            qid = record.payload["qid"]
            article = parse_article(record.payload["title"], record.payload.get("wikitext", ""))
            sections = {s.ordinal: s for s in article.sections}
            for ordinal, generation_id in by_qid[qid].items():
                section = sections.get(ordinal)
                if section is None and ordinal != 0:
                    continue
                # Ordinal 0 is a lead-era generation: the article's lead is
                # its section.
                body = article.top_wikitext if section is None else section.body
                section_main = section_mains.get((qid, ordinal))
                target_wikitext = None
                target_record_id = None
                if section_main is not None and section_main_asserts(section_main.payload):
                    target_wikitext = section_main.payload.get("wikitext", "")
                    target_record_id = section_main.id
                raw = infobox_field(body, "model years") or infobox_field(body, "model_years")
                if raw is not None:
                    span, _reason = parse_span(raw)
                    if span is not None:
                        self.model_year_spans[generation_id] = (span, record.id)
                elif target_wikitext is not None:
                    traw = infobox_field(target_wikitext, "model years") or infobox_field(
                        target_wikitext, "model_years"
                    )
                    if traw is not None:
                        span, _reason = parse_span(traw)
                        if span is not None:
                            self.model_year_spans[generation_id] = (span, target_record_id)
                doors = generation_doors(body, target_wikitext, article.top_wikitext)
                if doors:
                    self.generation_doors[generation_id] = doors

    def _load_body_signals(self) -> None:
        """Per configuration: what its EPA raw record says about its doors
        (ADR 0017 §4). Configurations without an attached EPA record fall
        back to trim-string body words at decision time."""
        self.config_body: dict[int, BodySignal] = {}
        epa_id = self.session.scalar(select(Source.id).where(Source.name == "EPA fueleconomy.gov"))
        if epa_id is None:
            return
        payload = RawRecord.payload
        rows = self.session.execute(
            select(
                ExternalId.configuration_id,
                payload.op("->>")("VClass"),
                payload.op("->>")("pv2"),
                payload.op("->>")("pv4"),
                payload.op("->>")("lv2"),
                payload.op("->>")("lv4"),
            )
            .join(
                RawRecord,
                (RawRecord.external_id == ExternalId.external_id)
                & (RawRecord.source_id == ExternalId.source_id),
            )
            .where(
                ExternalId.source_id == epa_id,
                ExternalId.configuration_id.isnot(None),
            )
        ).all()
        for configuration_id, vclass, pv2, pv4, lv2, lv4 in rows:
            signal = epa_body_signal(vclass, pv2, pv4, lv2, lv4)
            if signal:
                self.config_body[configuration_id] = signal

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

    def _candidates(
        self, configuration: Configuration, model_id: int, period: CataloguePeriod, kind: str
    ) -> tuple[list[_Candidate], int, list[int]]:
        """(matching candidates, undated linked generations, body-vetoed
        generations). An undated competitor makes 'unique among the dated'
        mean nothing - the 1991 Celica convertible must not land in
        `celica-gt-four` just because the GT-Four page is the only one with
        a production span - so the caller treats undated > 0 as unplaceable,
        not as a smaller field. The body veto strikes FIRST: a generation
        whose every asserted body contradicts the configuration's door
        signal is not a candidate regardless of year, and not an undated
        competitor either."""
        signal = self.config_body.get(configuration.id) or trim_body_signal(configuration.trim_name)
        found: list[_Candidate] = []
        undated = 0
        vetoed: list[int] = []
        for generation_id in self.links.get(model_id, ()):
            if signal and signal.contradicts(self.generation_doors.get(generation_id, frozenset())):
                vetoed.append(generation_id)
                continue
            model_years = self.model_year_spans.get(generation_id)
            if kind == "model_year" and model_years is not None:
                span, record_id = model_years
                if span.contains(period.start_year, period.end_year):
                    found.append(_Candidate(generation_id, span, True, record_id))
                continue
            generation = self.generations[generation_id]
            if generation.start_year is None:
                undated += 1
                continue
            span = Span(generation.start_year, generation.end_year)
            if span.contains(period.start_year, period.end_year, end_slack=END_SLACK):
                found.append(
                    _Candidate(generation_id, span, False, self.span_records.get(generation_id))
                )
        return sorted(found, key=lambda c: c.generation_id), undated, sorted(vetoed)

    def _assert_placement(self, configuration: Configuration, candidate: _Candidate | None) -> None:
        """Write/refresh/withdraw the placement assertion + column. The pass
        is the sole placer, so the column follows the recomputed answer."""
        if candidate is None:
            observed = None
        else:
            # The generation is named by ROW, never by address. A slug is a
            # projection that may be recomposed at any time, and citing one
            # here made 1,369 placement assertions supersede themselves the
            # first time the grammar changed - an address rewriting history
            # it has no business touching. The placement itself is the FK.
            observed = (
                f"generation:{candidate.generation_id}"
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
            candidates, undated, vetoed = self._candidates(
                configuration, period.model_id, period, kind_by_id[period.period_kind_id]
            )
            vetoed_slugs = [self.generations[g].slug for g in vetoed]
            if vetoed:
                self.stats.body_vetoed += 1

            if len(candidates) == 1 and undated:
                # One dated match beside undated siblings is not uniqueness -
                # the honest state is unknowable-yet. Resolves mechanically as
                # the siblings gain spans.
                if configuration.generation_id is not None:
                    self.stats.withdrawn += 1
                self._assert_placement(configuration, None)
                self._dismiss_flag(configuration.id, "waiting_on_undated_competitors")
                self.stats.undated_competitor += 1
                self.decisions.record_key(
                    key,
                    "waits_undated_competitor",
                    detail={
                        "dated_match": self.generations[candidates[0].generation_id].slug,
                        "undated_siblings": undated,
                    },
                )
                continue

            if len(candidates) == 1:
                candidate = candidates[0]
                already = configuration.generation_id == candidate.generation_id
                self._assert_placement(configuration, candidate)
                self._dismiss_flag(configuration.id, "resolved_to_single_candidate")
                if already:
                    self.stats.already_placed += 1
                else:
                    self.stats.placed += 1
                detail = {"generation": self.generations[candidate.generation_id].slug}
                if vetoed_slugs:
                    detail["body_vetoed"] = vetoed_slugs
                self.decisions.record_key(
                    key,
                    "placed_dated_overlap",
                    raw_record_id=candidate.raw_record_id,
                    method="model_years_exact" if candidate.exact else "production_end_slack",
                    detail=detail,
                )
                continue

            if len(candidates) > 1:
                if configuration.generation_id is not None:
                    self.stats.withdrawn += 1
                self._assert_placement(configuration, None)
                detail = {
                    "period": f"{period.start_year}–{period.end_year}",
                    "undated_siblings": undated,
                    # The flag's standing precondition (ADR 0017 §3): rows
                    # with a body signal either discriminated or still
                    # genuinely overlap among compatible bodies.
                    "body_vetoed": vetoed_slugs,
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


if __name__ == "__main__":
    from carmanac.runner import run

    run(run_generation_placement_pass)
