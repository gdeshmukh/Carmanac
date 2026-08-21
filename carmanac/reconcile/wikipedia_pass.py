"""The Wikipedia pass (ADR 0017): generation time and existence from
nameplate articles.

Consumes landed `article:<QID>` records (with `section-main:` evidence),
one pass for what two used to split. A generation-attached QID's article
dates that generation from its lead infobox (§2; the lead IS section 0, so
the retired `infobox:` records' job rides the full article now). A
model-attached QID's article works at nameplate grain: its per-generation
sections mint generations under that model's company (§4), keyed
`section:<QID>#<ordinal>` in `external_ids`. The QID routes by 1:1
attachment or the curated `SECTION_ARTICLE_MODELS` registry; identity is
inherited, and sections are structural parsing inside that scope, not name
matching.

The guardrails, all from the ADR:

- **All-or-nothing per article.** Duplicate or non-contiguous ordinals, or
  a slug collision, flag `section_generation_review` and mint nothing:
  a partially-minted article leaves an unlinked competitor the placement
  guards cannot see.
- **Existing inventory reconciles, never duplicates.** Where the model
  already has linked generations from elsewhere, every section must resolve
  to one of them (a `{{Main}}` target matching an attached QID's sitelink
  title, or a unique chassis-code intersection) or be provably distinct
  (codes on both sides, disjoint). Anything unresolvable flags the article.
  Reconciled sections corroborate the link and assert no facts - the
  generation's own article is the richer source.
- **Redirected articles assert nothing** (the §2 rule): facts previously
  asserted from this article tombstone back to NULL.
- **Heading years are detail, never spans.** Spans come only from the
  section's own infobox production field, through the same flag-never-guess
  parser as §2; a start year with a fabricated open end would contain every
  later period.

Links written here are evidence - the article IS the routed model's
nameplate page (or a curated judgment says the filing's catalogue contains
these cars); section parsing never invents a link to any other model.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from carmanac.db.models import (
    CataloguePeriod,
    Company,
    Configuration,
    ConfigurationEngine,
    ConfigurationTransmission,
    Engine,
    ExternalId,
    FieldProvenance,
    FuelType,
    Generation,
    GenerationModelLink,
    GenerationSpecs,
    MatchDecision,
    Model,
    ModelSpecs,
    RawRecord,
    ReconciliationFlag,
    Source,
    Transmission,
)
from carmanac.ingest.landing import get_source
from carmanac.ingest.wikipedia import SOURCE_NAME
from carmanac.reconcile import policy
from carmanac.reconcile.addressing import nonconforming_slug, slugify
from carmanac.reconcile.bookkeeping import (
    DecisionLog,
    mark_reconciled,
)
from carmanac.reconcile.engine import (
    assert_field_facts,
    current_records,
)
from carmanac.reconcile.matching import normalize_name
from carmanac.reconcile.sources.wikidata_models import extract_chassis_codes, strip_prefix
from carmanac.reconcile.sources.wikipedia_infobox import (
    SPEC_COVERAGE,
    infobox_field,
    parse_infobox,
    parse_span,
    parse_specs,
    same_subject,
    title_code_tokens,
)
from carmanac.reconcile.sources.wikipedia_sections import (
    ORDINAL_WORDS,
    GenerationSection,
    door_counts,
    looks_multi_era,
    parse_article,
    section_main_asserts,
)
from carmanac.reconcile.sources.wikipedia_tables import (
    EngineRow,
    family_sections,
    parse_engine_tables,
    section_displacement,
)

log = logging.getLogger(__name__)


def _row_matches(
    row: EngineRow, start: int | None, end: int | None, cc: int | None, fuel: str | None
) -> bool:
    """The physical-key predicate: a missing key on either side never
    excludes; a present pair must agree. Displacement is required on OUR
    side - a configuration without one cannot be physically matched at all
    (mostly EVs and thin filings, per the census)."""
    if cc is None or row.displacement_cc is None:
        return False
    if abs(row.displacement_cc - cc) > max(20, round(cc * 0.03)):
        return False
    if row.years is not None and start is not None:
        row_start, row_end = row.years
        upper = end if end is not None else start
        if row_start > upper or (row_end is not None and row_end < start):
            return False
    return row.fuel is None or fuel is None or row.fuel == fuel


PASS_NAME = "wikipedia"

COVERAGE: tuple[str, ...] = ("name", "chassis_codes", "start_year", "end_year")

# What a generation-attached article's lead infobox speaks about; name stays
# out - a wd-attached generation keeps its own name.
LEAD_COVERAGE: tuple[str, ...] = ("start_year", "end_year", "chassis_codes")


@dataclass
class WikipediaStats:
    processed: int = 0
    generations_timed: int = 0
    no_facts_found: int = 0
    articles_minted_from: int = 0
    generations_created: int = 0
    lead_era_minted: int = 0
    generations_refreshed: int = 0
    links_asserted: int = 0
    sections_reconciled: int = 0
    no_sections: int = 0
    redirected: int = 0
    unrouted: int = 0
    flagged_articles: int = 0
    engines_minted: int = 0
    transmissions_minted: int = 0
    variants_minted: int = 0
    powertrain_links: int = 0
    powertrain_retired: int = 0
    powertrain_ambiguous: int = 0
    assertions_inserted: int = 0
    assertions_superseded: int = 0
    flags_opened: int = 0
    flags_dismissed: int = 0

    def summary(self) -> str:
        return (
            f"processed={self.processed} timed={self.generations_timed} "
            f"no_facts={self.no_facts_found} minted_from={self.articles_minted_from} "
            f"created={self.generations_created} (lead={self.lead_era_minted}) "
            f"refreshed={self.generations_refreshed} "
            f"links={self.links_asserted} reconciled={self.sections_reconciled} "
            f"no_sections={self.no_sections} redirected={self.redirected} "
            f"unrouted={self.unrouted} flagged={self.flagged_articles} | "
            f"powertrain: minted={self.engines_minted}+{self.transmissions_minted} "
            f"variants={self.variants_minted} "
            f"links={self.powertrain_links} (retired={self.powertrain_retired}) "
            f"ambiguous={self.powertrain_ambiguous} | "
            f"assertions={self.assertions_inserted} "
            f"(superseded={self.assertions_superseded}) "
            f"flags={self.flags_opened} (dismissed={self.flags_dismissed})"
        )


class _WikipediaPass:
    def __init__(self, session: Session):
        self.session = session
        self.stats = WikipediaStats()
        self.source = get_source(session, SOURCE_NAME)
        self.wikidata_id = session.scalar(select(Source.id).where(Source.name == "Wikidata"))
        self.decisions = DecisionLog(session, self.source.id, PASS_NAME)

        self.current = current_records(session, self.source.id)
        self._load_routing()
        self._load_generation_attachments()
        self._load_strip_prefixes()
        self._load_generations()
        self._load_links()
        self._load_section_keys()
        self._load_section_mains()
        self._load_sitelink_titles()
        self._load_open_flags()
        self._load_powertrain()

    # --- loaders --------------------------------------------------------------

    def _load_routing(self) -> None:
        """QID -> model. Curated routings resolve by slug pair and OVERRIDE
        the mechanical attachment (an entry exists precisely because the
        mechanical rungs could not place the QID)."""
        self.model_by_qid: dict[str, int] = {
            qid: model_id
            for qid, model_id in self.session.execute(
                select(ExternalId.external_id, ExternalId.model_id).where(
                    ExternalId.source_id == self.wikidata_id,
                    ExternalId.model_id.isnot(None),
                )
            )
        }
        self.models: dict[int, Model] = {m.id: m for m in self.session.scalars(select(Model))}
        self.model_pairs: dict[int, str] = {
            model_id: f"{company_slug}/{model_slug}"
            for model_id, model_slug, company_slug in self.session.execute(
                select(Model.id, Model.slug, Company.slug).join(
                    Company, Model.company_id == Company.id
                )
            )
        }
        # The routing resolves through the model's own source id, never its
        # address: re-addressing a page must not unroute a curated judgment.
        model_by_external_id = {
            external_id: model_id
            for external_id, model_id in self.session.execute(
                select(ExternalId.external_id, ExternalId.model_id).where(
                    ExternalId.model_id.isnot(None)
                )
            )
        }
        for qid, key in policy.SECTION_ARTICLE_MODELS.items():
            model_id = model_by_external_id.get(key)
            if model_id is None:
                # A database that has not materialized the routed model yet
                # (fresh clone, test DB): the article stays unrouted and
                # waits, same as any other missing prerequisite.
                log.warning("SECTION_ARTICLE_MODELS[%s] -> %r: no such model yet", qid, key)
                continue
            self.model_by_qid[qid] = model_id

    def _load_generation_attachments(self) -> None:
        """QID -> generation, via the Wikidata attachment: these articles
        date one generation from their lead infobox instead of routing to a
        model."""
        self.generation_by_qid: dict[str, int] = {
            qid: generation_id
            for qid, generation_id in self.session.execute(
                select(ExternalId.external_id, ExternalId.generation_id).where(
                    ExternalId.source_id == self.wikidata_id,
                    ExternalId.generation_id.isnot(None),
                )
            )
        }

    def _load_strip_prefixes(self) -> None:
        """ADR 0013 §1 name forms per company - its own name plus its vPIC
        make name(s), longest first - for the stripped display rule
        (ADR 0019 §4). The same query the wd-models pass runs."""
        norms: dict[int, set[str]] = {
            cid: {normalize_name(name)}
            for cid, name in self.session.execute(select(Company.id, Company.name))
        }
        for company_id, make_name in self.session.execute(
            text(
                """SELECT DISTINCT ei.company_id, rr.payload->>'make_name'
                   FROM external_ids ei
                   JOIN sources s ON s.id = ei.source_id AND s.name = 'NHTSA vPIC'
                   JOIN raw_scrape.raw_records rr
                     ON rr.source_id = s.id AND rr.external_id = ei.external_id
                   WHERE ei.external_id LIKE 'make:%' AND ei.company_id IS NOT NULL"""
            )
        ):
            if make_name:
                norms.setdefault(company_id, set()).add(normalize_name(make_name))
        self.company_prefixes: dict[int, tuple[str, ...]] = {
            cid: tuple(sorted(ns, key=len, reverse=True)) for cid, ns in norms.items()
        }

    def _load_generations(self) -> None:
        self.generations: dict[int, Generation] = {
            g.id: g for g in self.session.scalars(select(Generation))
        }
        self.generation_specs: dict[int, GenerationSpecs] = {
            r.generation_id: r for r in self.session.scalars(select(GenerationSpecs))
        }
        self.model_specs: dict[int, ModelSpecs] = {
            r.model_id: r for r in self.session.scalars(select(ModelSpecs))
        }
        self.generation_by_company_slug: dict[tuple[int, str], int] = {
            (g.company_id, g.slug): g.id for g in self.generations.values()
        }

    def _load_links(self) -> None:
        """Live links per model (any source - the candidate gate placement
        sees), and this source's own live pairs for idempotent assertion.

        Generations ruled wrong-grain (ADR 0018 §1) are excluded from the
        competitor sets: a section must never reconcile onto a row ruled
        not-a-generation, and such a row must not block a real section from
        minting - even while its links await the demotion script."""
        demoted: set[int] = set(
            self.session.scalars(
                select(ExternalId.generation_id).where(
                    ExternalId.source_id == self.wikidata_id,
                    ExternalId.generation_id.isnot(None),
                    ExternalId.external_id.in_(policy.NOT_A_GENERATION),
                )
            )
        )
        self.links_by_model: dict[int, set[int]] = {}
        self.own_links: set[tuple[int, int]] = set()
        for generation_id, model_id, source_id in self.session.execute(
            select(
                GenerationModelLink.generation_id,
                GenerationModelLink.model_id,
                GenerationModelLink.source_id,
            ).where(GenerationModelLink.superseded_by.is_(None))
        ):
            if generation_id not in demoted:
                self.links_by_model.setdefault(model_id, set()).add(generation_id)
            if source_id == self.source.id:
                self.own_links.add((generation_id, model_id))

    def _load_section_keys(self) -> None:
        """`section:<QID>#<ordinal>` -> generation, this pass's identity."""
        self.section_generations: dict[str, int] = {
            external_id: generation_id
            for external_id, generation_id in self.session.execute(
                select(ExternalId.external_id, ExternalId.generation_id).where(
                    ExternalId.source_id == self.source.id,
                    ExternalId.generation_id.isnot(None),
                    ExternalId.external_id.like("section:%"),
                )
            )
        }

    def _load_section_mains(self) -> None:
        """Current `section-main:<QID>#<ordinal>` records by (qid, ordinal) -
        the fetched {{Main}} targets (ADR 0018 §3) - plus, for the adoption
        note, every sweep QID's sitelink title: whether a target corresponds
        to a Wikidata entity is feedstock for the future adoption pass, and
        recording it costs one query here."""
        self.section_mains: dict[tuple[str, int], RawRecord] = {}
        for record in self.current:
            if not record.external_id.startswith("section-main:"):
                continue
            qid, _, ordinal = record.external_id.removeprefix("section-main:").partition("#")
            self.section_mains[(qid, int(ordinal))] = record
        self.sweep_qid_by_title: dict[str, str] = {}
        if not self.section_mains:
            return
        from urllib.parse import unquote

        for sweep_qid, url in self.session.execute(
            select(
                RawRecord.external_id,
                RawRecord.payload.op("->")("article").op("->>")("value"),
            ).where(
                RawRecord.source_id == self.wikidata_id,
                RawRecord.payload.op("->>")("sweep") == "models",
            )
        ):
            if url and "/wiki/" in url:
                title = unquote(url.rsplit("/wiki/", 1)[-1])
                self.sweep_qid_by_title[self._norm_title(title)] = sweep_qid

    def _load_sitelink_titles(self) -> None:
        """Normalized enwiki title -> generation, for `{{Main}}`
        reconciliation: an attached generation QID's sitelink names the
        per-generation article a section defers to."""
        self.generation_by_title: dict[str, int] = {}
        rows = self.session.execute(
            select(RawRecord.payload, ExternalId.generation_id)
            .join(
                ExternalId,
                (ExternalId.external_id == RawRecord.external_id)
                & (ExternalId.source_id == RawRecord.source_id),
            )
            .where(
                RawRecord.source_id == self.wikidata_id,
                RawRecord.payload.op("->>")("sweep") == "models",
                ExternalId.generation_id.isnot(None),
            )
        ).all()
        from urllib.parse import unquote

        for payload, generation_id in rows:
            url = (payload.get("article") or {}).get("value") or ""
            if "/wiki/" in url:
                title = unquote(url.rsplit("/wiki/", 1)[-1])
                self.generation_by_title[self._norm_title(title)] = generation_id

    @staticmethod
    def _norm_title(title: str) -> str:
        return title.replace("_", " ").strip().casefold()

    def _load_open_flags(self) -> None:
        self.open_article_flags: dict[str, ReconciliationFlag] = {}
        self.open_value_flags: dict[tuple[int, str], ReconciliationFlag] = {}
        for flag in self.session.scalars(
            select(ReconciliationFlag).where(
                ReconciliationFlag.status == "open",
                ReconciliationFlag.source_id == self.source.id,
                ReconciliationFlag.kind.in_(["section_generation_review", "implausible_value"]),
            )
        ):
            if flag.kind == "section_generation_review":
                qid = (flag.detail or {}).get("qid")
                if qid:
                    self.open_article_flags[qid] = flag
            elif flag.generation_id is not None:
                self.open_value_flags[(flag.generation_id, flag.field_name)] = flag

    def _load_powertrain(self) -> None:
        """The engine-tables stage's working set (ADR 0020 amendment): every
        configuration's physical keys, the minted family entities by their
        article key, and this source's live links and power/torque claims."""
        petrol_ids: set[int] = set()
        diesel_ids: set[int] = set()
        for fuel_id, name in self.session.execute(select(FuelType.id, FuelType.name)):
            if "gasoline" in name.lower() or "petrol" in name.lower():
                petrol_ids.add(fuel_id)
            elif "diesel" in name.lower():
                diesel_ids.add(fuel_id)
        self.configs_by_model: dict[int, list[tuple]] = {}
        for cid, model_id, start, end, cc, fuel_id in self.session.execute(
            select(
                Configuration.id,
                CataloguePeriod.model_id,
                CataloguePeriod.start_year,
                CataloguePeriod.end_year,
                Configuration.engine_displacement_cc,
                Configuration.fuel_type_id,
            ).join(CataloguePeriod, CataloguePeriod.id == Configuration.catalogue_period_id)
        ):
            fuel = (
                "petrol" if fuel_id in petrol_ids else "diesel" if fuel_id in diesel_ids else None
            )
            self.configs_by_model.setdefault(model_id, []).append((cid, start, end, cc, fuel))

        # `<kind>-article:<key>` is a family, `...#<code>` one of its
        # variants - one scan fills both.
        self.engines_by_key: dict[str, Engine] = {}
        self.transmissions_by_key: dict[str, Transmission] = {}
        self.engine_variants: dict[tuple[str, str], int] = {}
        for external_id, engine_id, transmission_id in self.session.execute(
            select(ExternalId.external_id, ExternalId.engine_id, ExternalId.transmission_id).where(
                ExternalId.source_id == self.source.id,
                ExternalId.external_id.like("%-article:%"),
            )
        ):
            kind, _, rest = external_id.partition(":")
            key, _, code = rest.partition("#")
            if kind == "engine-article" and engine_id is not None:
                if code:
                    self.engine_variants[(key, code)] = engine_id
                else:
                    self.engines_by_key[key] = self.session.get(Engine, engine_id)
            elif kind == "transmission-article" and transmission_id is not None and not code:
                self.transmissions_by_key[key] = self.session.get(Transmission, transmission_id)

        self.live_engine_links: dict[int, dict[int, ConfigurationEngine]] = {}
        for row in self.session.scalars(
            select(ConfigurationEngine).where(
                ConfigurationEngine.source_id == self.source.id,
                ConfigurationEngine.superseded_by.is_(None),
            )
        ):
            self.live_engine_links.setdefault(row.configuration_id, {})[row.engine_id] = row
        self.live_transmission_links: dict[int, dict[int, ConfigurationTransmission]] = {}
        for row in self.session.scalars(
            select(ConfigurationTransmission).where(
                ConfigurationTransmission.source_id == self.source.id,
                ConfigurationTransmission.superseded_by.is_(None),
            )
        ):
            self.live_transmission_links.setdefault(row.configuration_id, {})[
                row.transmission_id
            ] = row
        # Configurations whose power/torque we asserted before: the heal set,
        # so a claim the tables stop making tombstones back to NULL.
        self.powertrain_asserted: set[int] = set(
            self.session.scalars(
                select(FieldProvenance.configuration_id).where(
                    FieldProvenance.source_id == self.source.id,
                    FieldProvenance.configuration_id.isnot(None),
                    FieldProvenance.field_name.in_(["power_hp", "torque_nm"]),
                    FieldProvenance.superseded_by.is_(None),
                )
            )
        )
        self.company_id_by_slug: dict[str, int] = {
            slug: cid
            for cid, slug in self.session.execute(select(Company.id, Company.slug))
            if slug
        }
        self.powertrain_unmatched: dict[str, int] = {}
        # Claims accumulate per configuration across ALL the articles that
        # anchor it (a model's page and its generations' pages overlap);
        # one sync at end of run judges the union - per-article syncing
        # would let the last article fight the first.
        self.pt_rows: dict[int, list[tuple]] = {}  # cid -> [(EngineRow, record, company_id)]
        self.pt_seen: dict[int, RawRecord] = {}  # cid -> an article that spoke for its model
        self.pt_titles: dict[str, str] = {}
        self.variant_demand: dict[str, set[str]] = {}  # family key -> codes the tables cite
        self.family_records: list[RawRecord] = []

    # --- flags ----------------------------------------------------------------

    def _flag_article(
        self, qid: str, model_id: int, record: RawRecord, reason: str, detail: dict
    ) -> None:
        full = {"qid": qid, "reason": reason, **detail}
        flag = self.open_article_flags.get(qid)
        if flag is not None:
            if flag.detail != full:
                flag.detail = full
            return
        flag = ReconciliationFlag(
            kind="section_generation_review",
            model_id=model_id,
            detail=full,
            source_id=self.source.id,
            raw_record_id=record.id,
        )
        self.session.add(flag)
        self.open_article_flags[qid] = flag
        self.stats.flags_opened += 1

    def _dismiss_article_flag(self, qid: str, resolution: str) -> None:
        flag = self.open_article_flags.pop(qid, None)
        if flag is not None:
            flag.status = "dismissed"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": resolution}
            self.stats.flags_dismissed += 1

    # --- the work -------------------------------------------------------------

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
        self.links_by_model.setdefault(model_id, set()).add(generation_id)
        self.stats.links_asserted += 1

    def _value_flag(
        self, generation: Generation, reason: str, raw: str, context: str, record: RawRecord
    ) -> None:
        key = (generation.id, "production")
        if key in self.open_value_flags:
            return
        flag = ReconciliationFlag(
            kind="implausible_value",
            generation_id=generation.id,
            field_name="production",
            detail={"reason": reason, "raw": raw[:500], "heading": context},
            source_id=self.source.id,
            raw_record_id=record.id,
        )
        self.session.add(flag)
        self.open_value_flags[key] = flag
        self.stats.flags_opened += 1

    def _section_facts(
        self,
        generation: Generation,
        section: GenerationSection,
        display: str,
        record: RawRecord,
        section_main: RawRecord | None = None,
    ) -> None:
        """The section's own infobox is first; a fetched `{{Main}}` target
        supplies what the section itself lacks (ADR 0018 §3), and every
        target-sourced assertion carries the `section-main:` record as its
        provenance. The two assert_field_facts calls split COVERAGE
        disjointly per run, so a field the target stops supplying falls back
        into the article record's coverage and tombstones normally."""
        facts: dict[str, tuple[str, object]] = {"name": (display, display)}
        main_facts: dict[str, tuple[str, object]] = {}
        if section.codes:
            facts["chassis_codes"] = ("|".join(section.codes), list(section.codes))
        self._generation_specs(generation, section.body, record)
        span = None
        raw = infobox_field(section.body, "production")
        if raw is not None:
            span, reason = parse_span(raw)
            if span is not None:
                observed = f"{span.start}–{span.end or 'present'}"
                facts["start_year"] = (observed, span.start)
                facts["end_year"] = (observed, span.end)
            elif reason is not None:
                self._value_flag(generation, reason, raw, section.heading, record)

        if section_main is not None and section_main_asserts(section_main.payload):
            target_title = section_main.payload["title"]
            target_wikitext = section_main.payload.get("wikitext", "")
            if span is None and raw is None:
                target_raw = infobox_field(target_wikitext, "production")
                if target_raw is not None:
                    target_span, target_reason = parse_span(target_raw)
                    if target_span is not None:
                        observed = f"{target_span.start}–{target_span.end or 'present'}"
                        main_facts["start_year"] = (observed, target_span.start)
                        main_facts["end_year"] = (observed, target_span.end)
                    elif target_reason is not None:
                        self._value_flag(
                            generation, target_reason, target_raw, target_title, section_main
                        )
            if "chassis_codes" not in facts and title_code_tokens(target_title):
                codes, _ambiguous = extract_chassis_codes(target_title, (), None)
                if codes:
                    main_facts["chassis_codes"] = ("|".join(codes), codes)

        for fact_record, fact_map, coverage in (
            (record, facts, tuple(f for f in COVERAGE if f not in main_facts)),
            (section_main, main_facts, tuple(f for f in COVERAGE if f in main_facts)),
        ):
            if not coverage:
                continue
            inserted, superseded = assert_field_facts(
                self.session,
                arc_col="generation_id",
                entity=generation,
                coverage=coverage,
                facts=fact_map,
                source_id=self.source.id,
                record=fact_record,
            )
            self.stats.assertions_inserted += inserted
            self.stats.assertions_superseded += superseded

    def _nameplate(self, model: Model) -> str:
        # Stripped nameplate (ADR 0019 §4, the ADR 0013 §1 rule). Stripping
        # must leave a nameplate, not a bare number: "Mazda3" and
        # "Polestar 2" strip to "3" and "2", which name nothing.
        name = model.name
        for prefix in self.company_prefixes.get(model.company_id, ()):
            stripped = strip_prefix(name, prefix, normalize_name)
            if stripped != name and any(ch.isalpha() for ch in stripped):
                return stripped
        return name

    def _display_name(self, model: Model, section: GenerationSection) -> str:
        name = self._nameplate(model)
        if section.codes:
            return f"{name} ({'/'.join(section.codes)})"
        word = ORDINAL_WORDS[section.ordinal - 1]
        return f"{name} ({word} generation)"

    def _reconcile_section(
        self, section: GenerationSection, competitors: set[int]
    ) -> int | str | None:
        """An existing generation this section describes, or the sentinel
        'distinct' when codes prove it is none of them, or None when the
        question cannot be answered mechanically."""
        for target in section.main_targets:
            generation_id = self.generation_by_title.get(self._norm_title(target))
            if generation_id in competitors:
                return generation_id
        if section.codes:
            code_hits = {
                generation_id
                for generation_id in competitors
                if set(self.generations[generation_id].chassis_codes or ()) & set(section.codes)
            }
            if len(code_hits) == 1:
                return code_hits.pop()
            if len(code_hits) > 1:
                return None
            if all(self.generations[g].chassis_codes for g in competitors):
                # Codes on both sides and disjoint: the AMG GT pair - the
                # 4-Door's X290/C590 beside the sports car's C190/C192.
                return "distinct"
        return None

    def _tombstone_stale_sections(self, qid: str, valid_ordinals: set[int], record) -> None:
        """An article revision that dropped a section leaves its generation
        keyed but unsupported: its facts tombstone (identity and link stand;
        an undated generation makes its model's placements wait, which is
        the honest state)."""
        prefix = f"section:{qid}#"
        for key, generation_id in self.section_generations.items():
            if not key.startswith(prefix):
                continue
            if int(key.removeprefix(prefix)) in valid_ordinals:
                continue
            inserted, superseded = assert_field_facts(
                self.session,
                arc_col="generation_id",
                entity=self.generations[generation_id],
                coverage=COVERAGE,
                facts={},
                source_id=self.source.id,
                record=record,
            )
            self.stats.assertions_inserted += inserted
            self.stats.assertions_superseded += superseded

    def _lead_facts(self, generation: Generation, record: RawRecord) -> None:
        """A generation-attached article dates its generation from the lead
        infobox: production span, chassis codes from the title parenthetical.
        A span that does not reduce to exactly one range flags
        `implausible_value` and asserts nothing; a field that starts parsing
        again dismisses the question it used to raise."""
        top = parse_article(
            record.payload["title"], record.payload.get("wikitext", "")
        ).top_wikitext
        self._generation_specs(generation, top, record)
        parsed = parse_infobox(record.payload["title"], top)
        facts: dict[str, tuple[str, object]] = {}
        if parsed.production is not None:
            observed = f"{parsed.production.start}–{parsed.production.end or 'present'}"
            facts["start_year"] = (observed, parsed.production.start)
            facts["end_year"] = (observed, parsed.production.end)
        codes, _ambiguous = extract_chassis_codes(parsed.title, (), None)
        if title_code_tokens(parsed.title) and codes:
            facts["chassis_codes"] = ("|".join(codes), codes)

        failed = {field_name for field_name, _, _ in parsed.failures}
        for field_name, span in (
            ("production", parsed.production),
            ("model years", parsed.model_years),
        ):
            key = (generation.id, field_name)
            flag = self.open_value_flags.get(key)
            if span is not None and field_name not in failed and flag is not None:
                flag.status = "dismissed"
                flag.resolved_at = func.now()
                flag.detail = {
                    **(flag.detail or {}),
                    "resolution": "parses_under_labeled_defer_amendment",
                }
                del self.open_value_flags[key]
                self.stats.flags_dismissed += 1
        for field_name, reason, raw in parsed.failures:
            key = (generation.id, field_name)
            if key in self.open_value_flags:
                continue
            flag = ReconciliationFlag(
                kind="implausible_value",
                generation_id=generation.id,
                field_name=field_name,
                detail={
                    "reason": reason,
                    "raw": raw,
                    "title": parsed.title,
                    "qid": record.payload["qid"],
                },
                source_id=self.source.id,
                raw_record_id=record.id,
            )
            self.session.add(flag)
            self.open_value_flags[key] = flag
            self.stats.flags_opened += 1

        inserted, superseded = assert_field_facts(
            self.session,
            arc_col="generation_id",
            entity=generation,
            coverage=LEAD_COVERAGE,
            facts=facts,
            source_id=self.source.id,
            record=record,
        )
        self.stats.assertions_inserted += inserted
        self.stats.assertions_superseded += superseded
        if facts:
            self.stats.generations_timed += 1
            self.decisions.record(
                record, "facts_asserted", method="infobox_parse", detail={"fields": sorted(facts)}
            )
        else:
            self.stats.no_facts_found += 1
            self.decisions.record(record, "no_facts_found")

    def _assert_specs(self, *, arc_col, entity, row_cache, row_cls, wikitext, record) -> None:
        """Physical specs onto the defaults table at this entity's grain.
        Empty wikitext still runs the tombstone sweep for an existing row -
        a page that stopped speaking heals its old claims."""
        facts: dict[str, tuple[str, object]] = dict(parse_specs(wikitext))
        raw_body = infobox_field(wikitext, "body_style") or infobox_field(wikitext, "body style")
        if raw_body:
            counts = door_counts(raw_body)
            if len(counts) == 1:
                facts["doors"] = (" ".join(raw_body.split())[:120], next(iter(counts)))
        row = row_cache.get(entity.id)
        if row is None:
            if not facts:
                return
            row = row_cls(**{arc_col: entity.id})
            self.session.add(row)
            self.session.flush()
            row_cache[entity.id] = row
        inserted, superseded = assert_field_facts(
            self.session,
            arc_col=arc_col,
            entity=entity,
            coverage=SPEC_COVERAGE,
            facts=facts,
            source_id=self.source.id,
            record=record,
            project_onto=row,
        )
        self.stats.assertions_inserted += inserted
        self.stats.assertions_superseded += superseded

    def _generation_specs(self, generation: Generation, wikitext: str, record: RawRecord) -> None:
        self._assert_specs(
            arc_col="generation_id",
            entity=generation,
            row_cache=self.generation_specs,
            row_cls=GenerationSpecs,
            wikitext=wikitext,
            record=record,
        )

    def _model_specs(self, model: Model, wikitext: str, record: RawRecord) -> None:
        self._assert_specs(
            arc_col="model_id",
            entity=model,
            row_cache=self.model_specs,
            row_cls=ModelSpecs,
            wikitext=wikitext,
            record=record,
        )

    def _powertrain_entity(self, kind: str, key: str, title: str, company_id: int):
        """The minting ladder (ADR 0020 amendment, Decision 2), per link
        target: recorded registry judgments first, then the mechanical
        maker-prefix rung, else nothing - generic technology and list pages
        wait in raw. Returns the entity row or None."""
        cache = self.engines_by_key if kind == "engine" else self.transmissions_by_key
        entity = cache.get(key)
        if entity is not None:
            return entity
        if key in policy.NOT_A_POWERTRAIN:
            return None
        registry = (
            policy.ENGINE_FAMILY_ARTICLES
            if kind == "engine"
            else policy.TRANSMISSION_FAMILY_ARTICLES
        )
        if key in registry:
            maker_slug = registry[key]
            maker_id = self.company_id_by_slug.get(maker_slug) if maker_slug else None
            if maker_slug and maker_id is None:
                log.warning("%s registry names unknown company %r for %r", kind, maker_slug, key)
        else:
            normalized = normalize_name(key)
            if not any(
                normalized.startswith(prefix)
                for prefix in self.company_prefixes.get(company_id, ())
            ):
                self.powertrain_unmatched[key] = self.powertrain_unmatched.get(key, 0) + 1
                return None
            maker_id = company_id
        slug = slugify(key)
        cls = Engine if kind == "engine" else Transmission
        if self.session.scalar(select(cls.id).where(cls.slug == slug)) is not None:
            log.warning("powertrain slug %r already taken; %r waits", slug, key)
            return None
        entity = cls(manufacturer_company_id=maker_id, slug=slug, name=title)
        self.session.add(entity)
        self.session.flush()
        self.session.add(
            ExternalId(
                **{("engine_id" if kind == "engine" else "transmission_id"): entity.id},
                source_id=self.source.id,
                external_id=f"{kind}-article:{key}",
            )
        )
        cache[key] = entity
        if kind == "engine":
            self.stats.engines_minted += 1
        else:
            self.stats.transmissions_minted += 1
        return entity

    def _engine_tables(self, model_ids: list[int], company_id: int, record: RawRecord) -> None:
        """Collect the article's engine-table claims against its models'
        configurations (ADR 0020 amendment, Decision 1). Physical keys only:
        catalogue years overlap, displacement within 3%, petrol/diesel not
        contradicted. Judgment happens in `_powertrain_sync`, over every
        article's claims together."""
        rows = parse_engine_tables(record.payload.get("wikitext", ""))
        for row in rows:
            for key, title in row.titles:
                self.pt_titles.setdefault(key, title)
        for row in rows:
            for key, code in row.engines:
                if code:
                    self.variant_demand.setdefault(key, set()).add(code)
        for model_id in model_ids:
            for cid, start, end, cc, fuel in self.configs_by_model.get(model_id, []):
                self.pt_seen[cid] = record
                for row in rows:
                    if _row_matches(row, start, end, cc, fuel):
                        self.pt_rows.setdefault(cid, []).append((row, record, company_id))

    def _family_variants(self, record: RawRecord) -> None:
        """Mint the demanded variant codes from a family page's per-code
        sections (ADR 0020 Decision 3): a code anchors to a heading or an
        `{{anchor}}` id; displacement rides along where the section states
        it. Codes whose anchors land nowhere keep the family-grain link -
        still true, just coarser."""
        kind, _, key = record.external_id.removeprefix("family:").partition(":")
        if kind != "engine-article":
            return
        family = self.engines_by_key.get(key)
        wanted = self.variant_demand.get(key, set())
        if family is None or not wanted:
            return
        sections = family_sections(record.payload.get("wikitext", ""))
        for code in sorted(wanted):
            if (key, code) in self.engine_variants:
                continue
            body = sections.get(" ".join(code.split()).casefold())
            if body is None:
                continue
            slug = slugify(f"{key} {code}")
            if self.session.scalar(select(Engine.id).where(Engine.slug == slug)) is not None:
                log.warning("variant slug %r already taken; %r#%r waits", slug, key, code)
                continue
            variant = Engine(
                manufacturer_company_id=family.manufacturer_company_id,
                slug=slug,
                name=f"{family.name or key} {code}",
                family_code=code,
                displacement_cc=section_displacement(body),
            )
            self.session.add(variant)
            self.session.flush()
            self.session.add(
                ExternalId(
                    engine_id=variant.id,
                    source_id=self.source.id,
                    external_id=f"engine-article:{key}#{code}",
                )
            )
            self.engine_variants[(key, code)] = variant.id
            self.stats.variants_minted += 1

    def _powertrain_sync(self) -> None:
        """Judge the accumulated claims once per run: exactly one engine
        identity standing across every matching row -> a link; several ->
        the decision log's open queue, re-attempted every run. Power and
        torque land only when every matching row agrees on one value -
        tunes we cannot tell apart assert nothing. Configurations whose
        articles stopped claiming heal back to nothing."""
        stale = (
            (
                set(self.live_engine_links)
                | set(self.live_transmission_links)
                | self.powertrain_asserted
            )
            & set(self.pt_seen)
        ) - set(self.pt_rows)
        for cid in sorted(set(self.pt_rows) | stale):
            claims = self.pt_rows.get(cid, [])
            record = claims[0][1] if claims else self.pt_seen[cid]
            company_id = claims[0][2] if claims else 0
            matching = [row for row, _r, _c in claims]

            engine_keys = sorted({key for row in matching for key, _code in row.engines})
            desired_engines: set[int] = set()
            if len(engine_keys) == 1:
                key = engine_keys[0]
                entity = self._powertrain_entity(
                    "engine", key, self.pt_titles.get(key, key), company_id
                )
                if entity is not None:
                    codes = {
                        code
                        for row in matching
                        for k, code in row.engines
                        if k == key and code is not None
                    }
                    variant_id = (
                        self.engine_variants.get((key, codes.pop())) if len(codes) == 1 else None
                    )
                    desired_engines.add(variant_id or entity.id)
                    self.decisions.record_key(
                        f"configuration:{cid}",
                        "engine_linked",
                        raw_record_id=record.id,
                        method="table_physical_keys",
                        detail={"engine": key, "rows": len(matching)},
                    )
            elif len(engine_keys) > 1:
                self.stats.powertrain_ambiguous += 1
                self.decisions.record_key(
                    f"configuration:{cid}",
                    "engine_ambiguous",
                    raw_record_id=record.id,
                    method="table_physical_keys",
                    detail={"engines": engine_keys},
                )
            desired_transmissions: set[int] = set()
            for key in sorted({key for row in matching for key, _code in row.transmissions}):
                entity = self._powertrain_entity(
                    "transmission", key, self.pt_titles.get(key, key), company_id
                )
                if entity is not None:
                    desired_transmissions.add(entity.id)

            self._sync_links(
                cid,
                desired_engines,
                self.live_engine_links,
                ConfigurationEngine,
                "engine_id",
                record,
            )
            self._sync_links(
                cid,
                desired_transmissions,
                self.live_transmission_links,
                ConfigurationTransmission,
                "transmission_id",
                record,
            )

            facts: dict[str, tuple[str, object]] = {}
            powers = {(row.power_hp, row.power_observed) for row in matching if row.power_hp}
            if len({p for p, _ in powers}) == 1:
                value, observed = next(iter(powers))
                facts["power_hp"] = (observed or str(value), value)
            torques = {(row.torque_nm, row.torque_observed) for row in matching if row.torque_nm}
            if len({t for t, _ in torques}) == 1:
                value, observed = next(iter(torques))
                facts["torque_nm"] = (observed or str(value), value)
            if facts or cid in self.powertrain_asserted:
                configuration = self.session.get(Configuration, cid)
                inserted, superseded = assert_field_facts(
                    self.session,
                    arc_col="configuration_id",
                    entity=configuration,
                    coverage=("power_hp", "torque_nm"),
                    facts=facts,
                    source_id=self.source.id,
                    record=record,
                )
                self.stats.assertions_inserted += inserted
                self.stats.assertions_superseded += superseded
                if facts:
                    self.powertrain_asserted.add(cid)

    def _sync_links(self, cid: int, desired: set[int], live_map, cls, id_col: str, record) -> None:
        existing = live_map.setdefault(cid, {})
        for entity_id in desired - existing.keys():
            link = cls(
                configuration_id=cid,
                **{id_col: entity_id},
                source_id=self.source.id,
                raw_record_id=record.id,
                scraped_at=record.last_seen_at,
            )
            self.session.add(link)
            existing[entity_id] = link
            self.stats.powertrain_links += 1
        for entity_id in list(existing.keys() - desired):
            # Retirement, not correction: no successor claim exists.
            row = existing.pop(entity_id)
            self.session.flush()
            row.superseded_by = row.id
            self.stats.powertrain_retired += 1

    def _lead_era(self, model: Model, model_id: int, qid: str, parsed, record: RawRecord) -> None:
        """An article with no generation sections describes one era. For a
        model with no linked generations, that era IS a generation - the
        lead production span dates it, keyed `section:<QID>#0` (the lead is
        MediaWiki's section 0). A model that already has linked generations
        gets nothing: a whole-nameplate span must not land on any one of
        them (§2's hazard, at mint scope)."""
        lead = parse_infobox(record.payload["title"], parsed.top_wikitext)
        keyed = self.section_generations.get(f"section:{qid}#0")
        if keyed is None and looks_multi_era(record.payload.get("wikitext", "")):
            # The article shows era structure the heading grammar could not
            # read; one era is exactly what it does NOT describe. Widening
            # the grammar is the real fix - this is the review vein for it.
            self.stats.no_sections += 1
            self._dismiss_article_flag(qid, "article_no_longer_has_sections")
            self.decisions.record(
                record,
                "lead_era_multi_era",
                detail={"title": parsed.title},
            )
            self._model_specs(self.models[model_id], "", record)
            return
        if keyed is None and self.links_by_model.get(model_id):
            # A multi-era nameplate: its lead describes no single era, so it
            # asserts no model defaults either (the current-generation dims
            # a lead often shows must not smear across eras).
            self.stats.no_sections += 1
            self._dismiss_article_flag(qid, "article_no_longer_has_sections")
            self.decisions.record(record, "no_sections")
            self._model_specs(self.models[model_id], "", record)
            return
        self._model_specs(self.models[model_id], parsed.top_wikitext, record)
        if keyed is None:
            if lead.production is None:
                self.stats.no_sections += 1
                self._dismiss_article_flag(qid, "article_no_longer_has_sections")
                if lead.failures:
                    # No generation exists to anchor a value flag; the
                    # decision log is the review vein.
                    self.decisions.record(
                        record,
                        "lead_era_unparseable",
                        detail={
                            "failures": [
                                {"field": f, "reason": r, "raw": raw[:200]}
                                for f, r, raw in lead.failures
                            ]
                        },
                    )
                else:
                    self.decisions.record(record, "no_sections")
                return
            name = self._nameplate(model)
            slug = slugify(name)
            reason = nonconforming_slug(slug)
            if reason is not None:
                self._flag_article(
                    qid,
                    model_id,
                    record,
                    "generation_slug_nonconforming",
                    {"title": parsed.title, "slug": slug, "reason": reason},
                )
                self.stats.flagged_articles += 1
                self.decisions.record(
                    record,
                    "flagged_sections",
                    detail={"reason": "nonconforming_slug", "slug": slug},
                )
                return
            occupant = self.generation_by_company_slug.get((model.company_id, slug))
            if occupant is not None:
                self._flag_article(
                    qid,
                    model_id,
                    record,
                    "generation_slug_collision",
                    {"title": parsed.title, "slug": slug, "existing_generation_id": occupant},
                )
                self.stats.flagged_articles += 1
                self.decisions.record(
                    record, "flagged_sections", detail={"reason": "slug_collision", "slug": slug}
                )
                return
            generation = Generation(company_id=model.company_id, slug=slug, name=name)
            self.session.add(generation)
            self.session.flush()
            self.generations[generation.id] = generation
            self.generation_by_company_slug[(model.company_id, slug)] = generation.id
            key = f"section:{qid}#0"
            self.session.add(
                ExternalId(generation_id=generation.id, source_id=self.source.id, external_id=key)
            )
            self.session.flush()
            self.section_generations[key] = generation.id
            self.stats.generations_created += 1
            self.stats.lead_era_minted += 1
            self.stats.articles_minted_from += 1
        else:
            generation = self.generations[keyed]
            self.stats.generations_refreshed += 1
            for field_name, reason, raw in lead.failures:
                if field_name == "production":
                    self._value_flag(generation, reason, raw, parsed.title, record)

        self._assert_link(generation.id, model_id, record)
        name = self._nameplate(model)
        facts: dict[str, tuple[str, object]] = {"name": (name, name)}
        if lead.production is not None:
            observed = f"{lead.production.start}–{lead.production.end or 'present'}"
            facts["start_year"] = (observed, lead.production.start)
            facts["end_year"] = (observed, lead.production.end)
        codes, _ambiguous = extract_chassis_codes(parsed.title, (), None)
        if title_code_tokens(parsed.title) and codes:
            facts["chassis_codes"] = ("|".join(codes), codes)
        inserted, superseded = assert_field_facts(
            self.session,
            arc_col="generation_id",
            entity=generation,
            coverage=COVERAGE,
            facts=facts,
            source_id=self.source.id,
            record=record,
        )
        self.stats.assertions_inserted += inserted
        self.stats.assertions_superseded += superseded
        self._dismiss_article_flag(qid, "lead_era_resolved")
        self.decisions.record(
            record,
            "lead_era_processed",
            method="lead_infobox_parse",
            detail={
                "model": self.model_pairs[model_id],
                "generation": generation.slug,
                "minted": keyed is None,
            },
        )

    def _process_article(self, record: RawRecord) -> None:
        qid = record.payload["qid"]
        # Redirect detection first (ADR 0019 §3 companion fix): an article
        # that both lost its routing and got redirected must still tombstone
        # its stale facts - the old order skipped the safety with the work.
        if not same_subject(record.payload.get("requested_title", ""), record.payload["title"]):
            generation_id = self.generation_by_qid.get(qid)
            if generation_id is not None:
                # Empty facts still run the tombstone: a span asserted before
                # the redirect was recognised heals back to NULL.
                inserted, superseded = assert_field_facts(
                    self.session,
                    arc_col="generation_id",
                    entity=self.generations[generation_id],
                    coverage=LEAD_COVERAGE,
                    facts={},
                    source_id=self.source.id,
                    record=record,
                )
                self.stats.assertions_inserted += inserted
                self.stats.assertions_superseded += superseded
            if generation_id is not None and generation_id in self.generation_specs:
                self._generation_specs(self.generations[generation_id], "", record)
            redirected_model = self.model_by_qid.get(qid)
            if redirected_model is not None and redirected_model in self.model_specs:
                self._model_specs(self.models[redirected_model], "", record)
            self._tombstone_stale_sections(qid, set(), record)
            self.stats.redirected += 1
            self.decisions.record(
                record,
                "waits_redirected_article",
                detail={
                    "requested": record.payload.get("requested_title"),
                    "resolved": record.payload["title"],
                },
            )
            return

        generation_id = self.generation_by_qid.get(qid)
        if generation_id is not None:
            generation = self.generations[generation_id]
            linked_models = [m for m, gens in self.links_by_model.items() if generation_id in gens]
            if linked_models:
                self._engine_tables(linked_models, generation.company_id, record)
            self._lead_facts(generation, record)
            return

        model_id = self.model_by_qid.get(qid)
        if model_id is None:
            self.stats.unrouted += 1
            self.decisions.record(record, "waits_unrouted_article")
            return
        model = self.models[model_id]
        self._engine_tables([model_id], model.company_id, record)

        parsed = parse_article(record.payload["title"], record.payload.get("wikitext", ""))
        if not parsed.sections:
            self._lead_era(model, model_id, qid, parsed, record)
            return
        self._model_specs(model, "", record)

        ordinals = [s.ordinal for s in parsed.sections]
        if sorted(ordinals) != list(range(1, len(ordinals) + 1)):
            self._flag_article(
                qid,
                model_id,
                record,
                "duplicate_or_noncontiguous_ordinals",
                {"title": parsed.title, "headings": [s.heading for s in parsed.sections]},
            )
            self.stats.flagged_articles += 1
            self.decisions.record(
                record, "flagged_sections", detail={"reason": "ordinals", "ordinals": ordinals}
            )
            return

        # Sections already keyed to this article refresh; everything else
        # must reconcile against the model's other generations or prove
        # distinct before anything mints.
        keyed: dict[int, int] = {}  # ordinal -> generation_id
        for section in parsed.sections:
            generation_id = self.section_generations.get(f"section:{qid}#{section.ordinal}")
            if generation_id is not None:
                keyed[section.ordinal] = generation_id
        competitors = {
            g for g in self.links_by_model.get(model_id, set()) if g not in keyed.values()
        }

        to_mint: list[GenerationSection] = []
        reconciled: dict[int, int] = {}  # ordinal -> existing generation_id
        unresolved: list[str] = []
        for section in parsed.sections:
            if section.ordinal in keyed:
                continue
            if not competitors:
                to_mint.append(section)
                continue
            answer = self._reconcile_section(section, competitors)
            if answer == "distinct":
                to_mint.append(section)
            elif answer is None:
                unresolved.append(section.heading)
            else:
                reconciled[section.ordinal] = answer

        if unresolved:
            self._flag_article(
                qid,
                model_id,
                record,
                "sections_unreconciled",
                {
                    "title": parsed.title,
                    "unreconciled": unresolved,
                    # A generation with no address is still a competitor;
                    # name it by what it is rather than where it lives.
                    "existing_generations": sorted(
                        self.generations[g].slug or f"#{g}" for g in competitors
                    ),
                },
            )
            self.stats.flagged_articles += 1
            self.decisions.record(
                record,
                "flagged_sections",
                detail={"reason": "unreconciled", "headings": unresolved},
            )
            return

        # Slug collisions checked for the WHOLE article before anything
        # mints - all-or-nothing, and never auto-suffix an identity.
        planned: dict[int, tuple[GenerationSection, str, str]] = {}
        for section in to_mint:
            display = self._display_name(model, section)
            slug = slugify(display)
            reason = nonconforming_slug(slug)
            if reason is not None:
                # The drift guard (ADR 0019 §4), all-or-nothing as ever.
                self._flag_article(
                    qid,
                    model_id,
                    record,
                    "generation_slug_nonconforming",
                    {"title": parsed.title, "slug": slug, "reason": reason},
                )
                self.stats.flagged_articles += 1
                self.decisions.record(
                    record,
                    "flagged_sections",
                    detail={"reason": "nonconforming_slug", "slug": slug},
                )
                return
            occupant = self.generation_by_company_slug.get((model.company_id, slug))
            if occupant is not None or any(s == slug for _, _, s in planned.values()):
                self._flag_article(
                    qid,
                    model_id,
                    record,
                    "generation_slug_collision",
                    {
                        "title": parsed.title,
                        "slug": slug,
                        "heading": section.heading,
                        "existing_generation_id": occupant,
                    },
                )
                self.stats.flagged_articles += 1
                self.decisions.record(
                    record, "flagged_sections", detail={"reason": "slug_collision", "slug": slug}
                )
                return
            planned[section.ordinal] = (section, display, slug)

        # Safe to write.
        minted: list[str] = []
        for section in parsed.sections:
            if section.ordinal in keyed:
                generation = self.generations[keyed[section.ordinal]]
                self._section_facts(
                    generation,
                    section,
                    self._display_name(model, section),
                    record,
                    section_main=self.section_mains.get((qid, section.ordinal)),
                )
                self._assert_link(generation.id, model_id, record)
                self.stats.generations_refreshed += 1
            elif section.ordinal in reconciled:
                self._assert_link(reconciled[section.ordinal], model_id, record)
                self.stats.sections_reconciled += 1
            else:
                section_, display, slug = planned[section.ordinal]
                generation = Generation(company_id=model.company_id, slug=slug, name=display)
                self.session.add(generation)
                self.session.flush()
                self.generations[generation.id] = generation
                self.generation_by_company_slug[(model.company_id, slug)] = generation.id
                key = f"section:{qid}#{section_.ordinal}"
                self.session.add(
                    ExternalId(
                        generation_id=generation.id,
                        source_id=self.source.id,
                        external_id=key,
                    )
                )
                self.session.flush()
                self.section_generations[key] = generation.id
                self._assert_link(generation.id, model_id, record)
                self._section_facts(
                    generation,
                    section_,
                    display,
                    record,
                    section_main=self.section_mains.get((qid, section_.ordinal)),
                )
                self.stats.generations_created += 1
                minted.append(slug)

        self._tombstone_stale_sections(qid, set(ordinals), record)
        self._dismiss_article_flag(qid, "sections_resolved")
        if minted:
            self.stats.articles_minted_from += 1
        detail = {
            "model": self.model_pairs[model_id],
            "minted": minted,
            "reconciled": len(reconciled),
            "refreshed": len(keyed),
        }
        # Per fetched {{Main}} target: what it resolved to, whether the grain
        # guards let it assert, and the sweep-QID correspondence - the future
        # Wikidata-adoption pass's key (ADR 0017 §4), recorded here because
        # it costs a dict lookup. Identity is not touched.
        section_main_detail = {}
        for section in parsed.sections:
            section_main = self.section_mains.get((qid, section.ordinal))
            if section_main is None:
                continue
            title = section_main.payload["title"]
            section_main_detail[str(section.ordinal)] = {
                "target": title,
                "asserts": section_main_asserts(section_main.payload),
                "sweep_qid": self.sweep_qid_by_title.get(self._norm_title(title)),
            }
        if section_main_detail:
            detail["section_main"] = section_main_detail
        self.decisions.record(record, "sections_processed", method="section_parse", detail=detail)

    def run(self) -> WikipediaStats:
        # One pass replaced wikipedia_infobox + wikipedia_sections; their
        # decision rows would otherwise linger as a retired queue forever.
        self.session.execute(
            delete(MatchDecision).where(
                MatchDecision.pass_name.in_(["wikipedia_infobox", "wikipedia_sections"])
            )
        )
        for record in self.current:
            if record.external_id.startswith("section-main:"):
                # Consumed as evidence inside the article's processing; marked
                # here so staleness stays queryable. `infobox:` records are
                # archival - the article carries section 0.
                mark_reconciled(self.session, record)
                continue
            if record.external_id.startswith("family:"):
                self.family_records.append(record)
                mark_reconciled(self.session, record)
                continue
            if not record.external_id.startswith("article:"):
                continue
            self.stats.processed += 1
            self._process_article(record)
            mark_reconciled(self.session, record)
        for family_record in self.family_records:
            self._family_variants(family_record)
        self._powertrain_sync()
        if self.powertrain_unmatched:
            top = sorted(self.powertrain_unmatched.items(), key=lambda kv: -kv[1])[:15]
            log.info(
                "powertrain targets outside every rung (registry review vein): %s",
                ", ".join(f"{k} x{n}" for k, n in top),
            )
        self.decisions.flush()
        self.session.commit()
        return self.stats


def run_wikipedia_pass(session: Session) -> WikipediaStats:
    stats = _WikipediaPass(session).run()
    log.info("wikipedia pass done: %s", stats.summary())
    return stats


if __name__ == "__main__":
    from carmanac.runner import run

    run(run_wikipedia_pass)
