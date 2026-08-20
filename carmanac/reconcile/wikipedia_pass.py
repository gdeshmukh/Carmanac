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
    Company,
    ExternalId,
    Generation,
    GenerationModelLink,
    MatchDecision,
    Model,
    RawRecord,
    ReconciliationFlag,
    Source,
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
    infobox_field,
    parse_infobox,
    parse_span,
    same_subject,
    title_code_tokens,
)
from carmanac.reconcile.sources.wikipedia_sections import (
    ORDINAL_WORDS,
    GenerationSection,
    parse_article,
    section_main_asserts,
)

log = logging.getLogger(__name__)

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

    def _lead_era(self, model: Model, model_id: int, qid: str, parsed, record: RawRecord) -> None:
        """An article with no generation sections describes one era. For a
        model with no linked generations, that era IS a generation - the
        lead production span dates it, keyed `section:<QID>#0` (the lead is
        MediaWiki's section 0). A model that already has linked generations
        gets nothing: a whole-nameplate span must not land on any one of
        them (§2's hazard, at mint scope)."""
        lead = parse_infobox(record.payload["title"], parsed.top_wikitext)
        keyed = self.section_generations.get(f"section:{qid}#0")
        if keyed is None:
            if self.links_by_model.get(model_id):
                self.stats.no_sections += 1
                self._dismiss_article_flag(qid, "article_no_longer_has_sections")
                self.decisions.record(record, "no_sections")
                return
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
            self._lead_facts(self.generations[generation_id], record)
            return

        model_id = self.model_by_qid.get(qid)
        if model_id is None:
            self.stats.unrouted += 1
            self.decisions.record(record, "waits_unrouted_article")
            return
        model = self.models[model_id]

        parsed = parse_article(record.payload["title"], record.payload.get("wikitext", ""))
        if not parsed.sections:
            self._lead_era(model, model_id, qid, parsed, record)
            return

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
            if not record.external_id.startswith("article:"):
                continue
            self.stats.processed += 1
            self._process_article(record)
            mark_reconciled(self.session, record)
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
