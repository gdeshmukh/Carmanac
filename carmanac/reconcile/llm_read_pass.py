"""The LLM read pass (ADR 0017, amended 2026-09-17): land what a read
stated, once its quotes verify against the page it read.

One read record per page is current. Its generations reconcile to the
model's held generations by code, then by name, then by the key an earlier
read minted; the rest mint under the read's own key, `read:<QID>#<slug>`.
The read's span lands in provenance for every generation it names and
projects only onto a field no other source asserts - an infobox keeps what
it states. Every leaf the read places raises a review flag. A placement
another source holds is never overwritten, only flagged as contradicted.
A correction in `PLACEMENT_CORRECTIONS` outranks the read and raises no
flag; a flag a person resolved stays resolved while the read states the
same thing. What a newer read no longer states withdraws: the placement
supersedes to nothing, a minted generation keeps its identity and loses
its facts and its links. A read at another prompt version, or one with no
parseable answer, states nothing and changes nothing until a current read
lands.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    ExternalId,
    FieldProvenance,
    Generation,
    GenerationModelLink,
    Model,
    RawRecord,
    ReconciliationFlag,
)
from carmanac.ingest.landing import get_source
from carmanac.ingest.llm_read import candidate_leaves
from carmanac.reconcile import policy
from carmanac.reconcile.addressing import nonconforming_slug, slugify
from carmanac.reconcile.bookkeeping import DecisionLog, mark_reconciled, reviewed
from carmanac.reconcile.engine import assert_field_facts, current_records, supersede
from carmanac.reconcile.sources.llm_read import (
    PROMPT_VERSION,
    SOURCE_NAME,
    ReadGeneration,
    page_text,
    parse_answer,
    verify,
)

log = logging.getLogger(__name__)
PASS_NAME = "llm_read"
COVERAGE = ("start_year", "end_year", "chassis_codes")
FLAG_KIND = "llm_placement_review"


@dataclass
class LLMReadStats:
    records: int = 0
    unrouted: int = 0
    skipped: int = 0
    dropped: int = 0
    generations_matched: int = 0
    generations_minted: int = 0
    assertions_inserted: int = 0
    assertions_superseded: int = 0
    links_asserted: int = 0
    generations_retired: int = 0
    links_retired: int = 0
    placed: int = 0
    already_placed: int = 0
    contradicted: int = 0
    withdrawn: int = 0
    corrected: int = 0
    flags_opened: int = 0
    flags_dismissed: int = 0

    def summary(self) -> str:
        return (
            f"llm read pass done: records={self.records} unrouted={self.unrouted} "
            f"skipped={self.skipped} dropped={self.dropped} | "
            f"generations: matched={self.generations_matched} "
            f"minted={self.generations_minted} facts={self.assertions_inserted} "
            f"(superseded={self.assertions_superseded}) links={self.links_asserted} "
            f"retired={self.generations_retired}/{self.links_retired} | "
            f"leaves: placed={self.placed} already={self.already_placed} "
            f"contradicted={self.contradicted} withdrawn={self.withdrawn} "
            f"corrected={self.corrected} | flags={self.flags_opened} "
            f"(dismissed={self.flags_dismissed})"
        )


class LLMReadPass:
    def __init__(self, session: Session):
        self.session = session
        self.source = get_source(session, SOURCE_NAME)
        self.stats = LLMReadStats()
        self.decisions = DecisionLog(session, self.source.id, PASS_NAME)
        wikidata_id = get_source(session, "Wikidata").id
        self.model_by_qid: dict[str, int] = {
            qid: model_id
            for qid, model_id in session.execute(
                select(ExternalId.external_id, ExternalId.model_id).where(
                    ExternalId.source_id == wikidata_id,
                    ExternalId.model_id.isnot(None),
                    ExternalId.external_id.like("Q%"),
                )
            )
        }
        self.models: dict[int, Model] = {m.id: m for m in session.scalars(select(Model))}
        self.generations: dict[int, Generation] = {
            g.id: g for g in session.scalars(select(Generation))
        }
        self.generation_by_company_slug: dict[tuple[int, str], int] = {
            (g.company_id, g.slug): g.id for g in self.generations.values() if g.slug
        }
        self.links_by_model: dict[int, list[int]] = {}
        self.own_links: set[tuple[int, int]] = set()
        for generation_id, model_id, source_id in session.execute(
            select(
                GenerationModelLink.generation_id,
                GenerationModelLink.model_id,
                GenerationModelLink.source_id,
            )
            .where(GenerationModelLink.superseded_by.is_(None))
            .order_by(GenerationModelLink.generation_id)
        ):
            if generation_id not in self.links_by_model.setdefault(model_id, []):
                self.links_by_model[model_id].append(generation_id)
            if source_id == self.source.id:
                self.own_links.add((generation_id, model_id))
        self.read_keys: dict[str, int] = {
            key: generation_id
            for key, generation_id in session.execute(
                select(ExternalId.external_id, ExternalId.generation_id).where(
                    ExternalId.source_id == self.source.id,
                    ExternalId.external_id.like("read:%"),
                )
            )
        }
        placements = session.scalars(
            select(FieldProvenance).where(
                FieldProvenance.configuration_id.isnot(None),
                FieldProvenance.field_name == "generation_id",
                FieldProvenance.superseded_by.is_(None),
            )
        )
        self.live_placements: dict[int, FieldProvenance] = {}
        self.foreign_placements: set[int] = set()
        for row in placements:
            if row.source_id == self.source.id:
                self.live_placements[row.configuration_id] = row
            elif row.observed_value is not None:
                self.foreign_placements.add(row.configuration_id)
        # The newest flag per leaf, whatever its status: an open one is
        # updated, a resolved one is a person's word on that statement.
        self.flags: dict[int, ReconciliationFlag] = {}
        for flag in session.scalars(
            select(ReconciliationFlag)
            .where(
                ReconciliationFlag.kind == FLAG_KIND,
                ReconciliationFlag.source_id == self.source.id,
            )
            .order_by(ReconciliationFlag.id)
        ):
            self.flags[flag.configuration_id] = flag
        self.corrected: set[int] = set()
        self.resolved: set[int] = set()  # generations some current read states
        self.read_by_qid: dict[str, RawRecord] = {}  # the reads verified this run

    # --- what a person ruled ----------------------------------------------------

    def _apply_corrections(self) -> None:
        for address, slug in sorted(policy.PLACEMENT_CORRECTIONS.items()):
            company_slug, model_slug, year, configuration_slug = address.split("/")
            configuration = self.session.scalar(
                select(Configuration)
                .join(CataloguePeriod, Configuration.catalogue_period_id == CataloguePeriod.id)
                .join(Model, CataloguePeriod.model_id == Model.id)
                .join(Company, Model.company_id == Company.id)
                .where(
                    Company.slug == company_slug,
                    Model.slug == model_slug,
                    CataloguePeriod.start_year == int(year),
                    Configuration.slug == configuration_slug,
                )
            )
            if configuration is None:
                log.warning("PLACEMENT_CORRECTIONS[%r]: no such configuration", address)
                continue
            company_id = self.models[
                self.session.get(CataloguePeriod, configuration.catalogue_period_id).model_id
            ].company_id
            target = (
                None if slug is None else self.generation_by_company_slug.get((company_id, slug))
            )
            if slug is not None and target is None:
                log.warning("PLACEMENT_CORRECTIONS[%r]: no generation %r", address, slug)
                continue
            self.corrected.add(configuration.id)
            if self._assert_placement(configuration, target, f"correction:{slug or 'none'}", None):
                self.stats.corrected += 1
            self._dismiss_flag(configuration.id, "corrected")

    # --- the work ----------------------------------------------------------------

    def run(self) -> LLMReadStats:
        self._apply_corrections()
        for record in current_records(self.session, self.source.id):
            self.stats.records += 1
            self._process(record)
            mark_reconciled(self.session, record)
        self._retire_unstated()
        self.decisions.flush()
        self.session.commit()
        return self.stats

    def _process(self, record: RawRecord) -> None:
        payload = record.payload
        qid = payload.get("qid", "")
        model_id = self.model_by_qid.get(qid)
        page = self.session.get(RawRecord, payload.get("page_record_id") or 0)
        if model_id is None or page is None:
            self.stats.unrouted += 1
            self.decisions.record(record, "read_unrouted")
            return
        if payload.get("prompt_version") != PROMPT_VERSION:
            self.stats.skipped += 1
            self.decisions.record(record, "read_stale_prompt")
            return
        model = self.models[model_id]
        leaves = {leaf.id: leaf for leaf in candidate_leaves(self.session, model_id)}
        offered = {i: leaves[i] for i in payload.get("leaf_ids") or [] if i in leaves}
        verified = verify(
            parse_answer(payload.get("answer")),
            page_text(page.payload.get("wikitext", "")),
            offered,
        )
        if verified.malformed:
            self.stats.skipped += 1
            self.decisions.record(record, "read_malformed")
            return
        self.stats.dropped += len(verified.dropped)
        self.read_by_qid[qid] = record

        stated: dict[int, tuple[int, str, str]] = {}
        for read in verified.generations:
            generation = self._reconcile(model, read, qid, record)
            if generation is None:
                continue
            # Two entries resolving to one generation are one statement of
            # it; the first dates it, both may place leaves on it.
            if generation.id not in self.resolved:
                self.resolved.add(generation.id)
                self._facts(generation, read, record)
            self._assert_link(generation.id, model_id, record)
            for car in read.cars:
                for leaf_id, match in car.leaves:
                    stated.setdefault(leaf_id, (generation.id, match, car.quote))

        placed = contradicted = 0
        for leaf_id in sorted(stated):
            generation_id, match, quote = stated[leaf_id]
            outcome = self._place(
                self.session.get(Configuration, leaf_id), generation_id, match, quote, record
            )
            placed += outcome == "placed"
            contradicted += outcome == "contradicted"
        for configuration_id in sorted(leaves):
            if configuration_id not in stated and configuration_id not in self.corrected:
                self._withdraw(configuration_id, record)
        self.decisions.record(
            record,
            "read_applied" if verified.generations else "read_unverified",
            method=f"prompt:{payload.get('prompt_version')} {payload.get('llm')}",
            detail={
                "generations": [g.name for g in verified.generations],
                "placed": placed,
                "contradicted": contradicted,
                "dropped": verified.dropped[:50],
            },
        )

    def _retire_unstated(self) -> None:
        """A generation a read minted that no current read states, however
        it is now named, loses its facts and its links; it keeps its row and
        its key, so a later read finds it again."""
        for key, generation_id in sorted(self.read_keys.items()):
            record = self.read_by_qid.get(key[len("read:") :].partition("#")[0])
            if record is None or generation_id in self.resolved:
                continue
            live = self.session.scalar(
                select(func.count())
                .select_from(FieldProvenance)
                .where(
                    FieldProvenance.generation_id == generation_id,
                    FieldProvenance.source_id == self.source.id,
                    FieldProvenance.superseded_by.is_(None),
                    FieldProvenance.observed_value.isnot(None),
                )
            )
            if live:
                self._facts(self.generations[generation_id], None, record)
                self.stats.generations_retired += 1
            for link in self.session.scalars(
                select(GenerationModelLink).where(
                    GenerationModelLink.generation_id == generation_id,
                    GenerationModelLink.source_id == self.source.id,
                    GenerationModelLink.superseded_by.is_(None),
                )
            ):
                # Retirement, not correction: no successor claim exists.
                self.session.flush()
                link.superseded_by = link.id
                self.own_links.discard((generation_id, link.model_id))
                self.stats.links_retired += 1

    def _reconcile(
        self, model: Model, read: ReadGeneration, qid: str, record: RawRecord
    ) -> Generation | None:
        codes = {c.casefold() for c in read.codes}
        held = [self.generations[g] for g in self.links_by_model.get(model.id, [])]
        for generation in held:
            if codes & {c.casefold() for c in generation.chassis_codes or []}:
                self.stats.generations_matched += 1
                return generation
        for generation in held:
            if (generation.name or "").casefold() == read.name.casefold():
                self.stats.generations_matched += 1
                return generation
        slug = slugify(read.name)
        key = f"read:{qid}#{slug}"
        if key in self.read_keys:
            return self.generations[self.read_keys[key]]
        reason = nonconforming_slug(slug)
        occupant = self.generation_by_company_slug.get((model.company_id, slug))
        if reason is not None or occupant is not None:
            self.decisions.record_key(
                key,
                "read_generation_unmintable",
                raw_record_id=record.id,
                detail={"slug": slug, "reason": reason or "slug_taken", "occupant": occupant},
            )
            return None
        generation = Generation(company_id=model.company_id, slug=slug, name=read.name)
        self.session.add(generation)
        self.session.flush()
        self.session.add(
            ExternalId(generation_id=generation.id, source_id=self.source.id, external_id=key)
        )
        self.generations[generation.id] = generation
        self.generation_by_company_slug[(model.company_id, slug)] = generation.id
        self.read_keys[key] = generation.id
        self.stats.generations_minted += 1
        return generation

    def _facts(
        self, generation: Generation, read: ReadGeneration | None, record: RawRecord
    ) -> None:
        facts: dict[str, tuple[str, object]] = {}
        if read is not None:
            facts["start_year"] = (read.quote, read.start_year)
            facts["end_year"] = (read.quote, read.end_year)
            if read.codes:
                facts["chassis_codes"] = ("|".join(read.codes), list(read.codes))
        # Another source's live assertion keeps the column; the read's stays
        # in provenance as evidence.
        held_elsewhere = frozenset(
            self.session.scalars(
                select(FieldProvenance.field_name).where(
                    FieldProvenance.generation_id == generation.id,
                    FieldProvenance.source_id != self.source.id,
                    FieldProvenance.superseded_by.is_(None),
                    FieldProvenance.observed_value.isnot(None),
                    FieldProvenance.field_name.in_(COVERAGE),
                )
            )
        )
        inserted, superseded = assert_field_facts(
            self.session,
            arc_col="generation_id",
            entity=generation,
            coverage=COVERAGE,
            facts=facts,
            source_id=self.source.id,
            record=record,
            skip_projection=held_elsewhere,
        )
        self.stats.assertions_inserted += inserted
        self.stats.assertions_superseded += superseded

    def _assert_link(self, generation_id: int, model_id: int, record: RawRecord) -> None:
        if (generation_id, model_id) in self.own_links:
            return
        self.session.add(
            GenerationModelLink(
                generation_id=generation_id,
                model_id=model_id,
                source_id=self.source.id,
                raw_record_id=record.id,
                scraped_at=record.last_seen_at,
            )
        )
        self.own_links.add((generation_id, model_id))
        self.links_by_model.setdefault(model_id, []).append(generation_id)
        self.stats.links_asserted += 1

    def _assert_placement(
        self,
        configuration: Configuration,
        target: int | None,
        observed: str,
        record: RawRecord | None,
    ) -> bool:
        """Write or refresh this source's placement and set the column.
        Returns whether the column changed."""
        live = self.live_placements.get(configuration.id)
        changed = configuration.generation_id != target
        if live is None or live.observed_value != observed:
            values = {
                "configuration_id": configuration.id,
                "field_name": "generation_id",
                "observed_value": observed,
                "source_id": self.source.id,
                "raw_record_id": None if record is None else record.id,
            }
            if live is None:
                live = FieldProvenance(**values)
                self.session.add(live)
            else:
                live = supersede(self.session, live, values)
            self.live_placements[configuration.id] = live
        configuration.generation_id = target
        return changed

    def _place(
        self,
        configuration: Configuration,
        generation_id: int,
        match: str,
        quote: str,
        record: RawRecord,
    ) -> str:
        if configuration.id in self.corrected:
            return "corrected"
        slug = self.generations[generation_id].slug
        detail = {"generation": slug, "match": match, "quote": quote}
        if configuration.generation_id == generation_id:
            if configuration.id in self.foreign_placements:
                self.stats.already_placed += 1
                return "already"
            self._assert_placement(
                configuration, generation_id, f"generation:{generation_id} {match}", record
            )
            self._flag(configuration, detail, record)
            self.stats.already_placed += 1
            return "already"
        if configuration.generation_id is not None and configuration.id in self.foreign_placements:
            held = self.generations[configuration.generation_id].slug
            self._flag(configuration, {**detail, "contradicts": held}, record)
            self.stats.contradicted += 1
            return "contradicted"
        self._assert_placement(
            configuration, generation_id, f"generation:{generation_id} {match}", record
        )
        self._flag(configuration, detail, record)
        self.stats.placed += 1
        return "placed"

    def _withdraw(self, configuration_id: int, record: RawRecord) -> None:
        live = self.live_placements.get(configuration_id)
        if live is None or live.observed_value is None:
            return
        configuration = self.session.get(Configuration, configuration_id)
        target = (
            configuration.generation_id if configuration_id in self.foreign_placements else None
        )
        self._assert_placement(configuration, target, None, record)  # type: ignore[arg-type]
        self._dismiss_flag(configuration_id, "read_withdrawn")
        self.stats.withdrawn += 1

    def _flag(self, configuration: Configuration, detail: dict, record: RawRecord) -> None:
        flag = self.flags.get(configuration.id)
        if flag is not None and flag.status == "open":
            if flag.detail != detail:
                flag.detail = detail
            return
        if flag is not None and flag.status == "resolved" and reviewed(flag, detail):
            return
        flag = ReconciliationFlag(
            kind=FLAG_KIND,
            configuration_id=configuration.id,
            field_name="generation_id",
            detail=detail,
            source_id=self.source.id,
            raw_record_id=record.id,
        )
        self.session.add(flag)
        self.flags[configuration.id] = flag
        self.stats.flags_opened += 1

    def _dismiss_flag(self, configuration_id: int, resolution: str) -> None:
        flag = self.flags.get(configuration_id)
        if flag is not None and flag.status == "open":
            flag.status = "dismissed"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": resolution}
            self.stats.flags_dismissed += 1


def run_llm_read_pass(session: Session) -> LLMReadStats:
    stats = LLMReadPass(session).run()
    log.info(stats.summary())
    return stats


if __name__ == "__main__":
    from carmanac.runner import run

    run(run_llm_read_pass)
