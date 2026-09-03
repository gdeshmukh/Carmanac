"""The Wikidata models-sweep pass (ADR 0012): match and enrich; creation
only under the mint registry (§7).

Level is decided per make, against the as-filed models. For every current
models-sweep record (bare QID, `sweep: models` marker), the ladder is:

    1. existing external id            -> refresh (model or generation)
    2. curated registry                -> recorded human judgments
    3. exact-normalized name/alias     -> model correspondence (incl. the
       match under the P176 company       make-prefix-stripped form; unique
                                          hits only, never fuzzy)
    4. line evidence (P179 members)    -> a `model_lines` row, never a model
    5. generation evidence (P179 to a  -> a `generations` row under the
       matched model)                     model's company, linked to the
                                          model via `generation_model_links`
                                          (ADR 0016: company-anchored)
    6. otherwise                       -> flag with candidates only when a
                                          held company + near-misses exist;
                                          else wait in raw, unflagged

Four rules hold across the ladder, each one a place where guessing was
available and rejected:

- **Match and enrich only** - except under the mint registry (§7). A
  model-shaped entity under a held company with no as-filed match waits - no
  row, no flag. One open make question must not fan out into thousands of
  model-shaped copies (ADR 0010 §1). The exception is deliberate and narrow:
  vPIC and EPA are US registries, so a marque that never sold there can never
  earn a model row from them - for companies a human has listed in
  `WIKIDATA_MINT_COMPANIES`, an entity that fell through every match and
  structure rung mints a nameplate row instead of waiting, under per-entity
  conditions (sole maker, no membership evidence, no foreign-brand label, no
  excluded word) with contested slugs flagged as a group, never suffixed.
- **The P176 maker gate runs before any name or structure rung**, including
  for entities with generation-shaped structure. Their evidence keeps in raw
  until the maker converts.
- **Chain edges (P155/P156) order, they never create.** That edge is
  level-ambiguous: Ford Model T *follows* Model S (two nameplates) while BMW
  E21 *follows* the 02 Series (a chain crossing nameplate boundaries), and
  both look identical to a generation chained to its sibling. Only P179
  membership in a matched model creates a generation.
- **Labels outrank aliases** (ADR 0013). A label says what an entity IS;
  aliases say what it is ALSO called, and Wikidata files rebadges there.

`models.name` stays vPIC's - Wikidata labels prefix the make, and "Toyota
4Runner" must never rename `4Runner`. Generations are Wikidata's own
contribution: name, summary, chassis codes, and span years where asserted
(span-less generations are legal - identity now, time later).

Every attempted record upserts a `match_decisions` row, so the labeled set
accumulates from the first run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from carmanac.db.models import (
    Company,
    ExternalId,
    FieldProvenance,
    Generation,
    GenerationModelLink,
    MatchDecision,
    Model,
    ModelLine,
    ModelLineMember,
    RawRecord,
    ReconciliationFlag,
    Source,
)
from carmanac.ingest.landing import get_source
from carmanac.ingest.wikidata.models import SWEEP_MARKER
from carmanac.ingest.wikipedia import SOURCE_NAME as WIKIPEDIA_SOURCE_NAME
from carmanac.reconcile import policy
from carmanac.reconcile.addressing import nonconforming_slug, slugify
from carmanac.reconcile.bookkeeping import (
    DecisionLog,
    mark_reconciled,
    trigram_candidates,
)
from carmanac.reconcile.engine import (
    assert_field_facts,
    current_records,
)
from carmanac.reconcile.matching import normalize_name
from carmanac.reconcile.sources import wikidata_models
from carmanac.reconcile.sources.wikidata_models import (
    ModelEntity,
    extract_chassis_codes,
    strip_prefix,
)
from carmanac.reconcile.sources.wikipedia_infobox import parse_infobox, same_subject
from carmanac.reconcile.sources.wikipedia_sections import parse_article

log = logging.getLogger(__name__)

PASS_NAME = "wikidata_models"

_BARE_QID = re.compile(r"Q\d+")
# The era-sibling shapes: a trailing roman numeral ("Dokker II") or a trailing
# parenthetical ("A110 (2017)") on an otherwise-shared nameplate. Wikidata
# files a nameplate and its generations as sibling model entities in exactly
# this dress, and level is never decided by label - so the whole family is
# one naming ruling, like exact label duplicates.
_TRAILING_ROMAN = re.compile(r"\s+(?:X{0,1}(?:IX|IV|V?I{0,3}))$")
_TRAILING_PAREN = re.compile(r"\s*\([^)]*\)$")
_MINT_EXCLUDE = re.compile(
    r"\b(?:" + "|".join(re.escape(w) for w in policy.WIKIDATA_MINT_EXCLUDE) + r")\b",
    re.IGNORECASE,
)


def line_brand_wearers(label: str, companies_by_norm: dict[str, list[int]]) -> list[int]:
    """Companies whose exact name the label wears as a leading token prefix.

    Tokens are whitespace-bound: hyphens bind, so "Mercedes-Benz C-Class"
    wears Mercedes-Benz and never the pre-war marque "Mercedes". The full
    label is excluded - a line named exactly a company name wears no brand.
    Every company of a matching name counts, slugged or not: two companies
    named "Mercury" is an ambiguity to surface, never a coin toss.
    """
    tokens = label.split()
    prefixes = {normalize_name(" ".join(tokens[:k])) for k in range(1, len(tokens))}
    prefixes.discard("")
    return sorted(cid for norm in prefixes for cid in companies_by_norm.get(norm, []))


def brand_destination(
    maker_id: int | None, wearers: list[int], model_holding: set[int]
) -> tuple[int | None, str | None]:
    """Which company an entity files under: (company, None), or (None, flag
    reason) when the answer would be a guess. Callers prefix the reason with
    their own rung ("line_", "model_").

    An entity whose name states its maker stays with it, whatever the maker
    holds - TVR's lines are TVR's before any US filing exists. A maker that
    holds models also keeps its foreign-badged entities (Lexus under Toyota
    is the maker's own assertion, and namesake companies - Delta, Skyline -
    would poison any vote there). A model-less maker is the stranded case:
    Wikidata's maker property names the holding company while the models sit
    under the carmaker, so the name decides - the unique model-holding
    company the name wears takes it. More than one wearer, or a model-less
    one, is a question for review, never a guess.

    Both rungs share this because the same entity can reach either: a
    nameplate is a line the day something carries P179 to it. Two rules
    would let one Wikidata edit move a car between brands.
    """
    if maker_id in wearers or not wearers or maker_id in model_holding:
        return maker_id, None
    if len(wearers) > 1:
        return None, "brand_ambiguous"
    if wearers[0] not in model_holding:
        return None, "brand_model_less"
    return wearers[0], None


def _filing_number(external_id: str) -> int:
    """`model:999` sorts below `model:1000`. Lexicographic order would not,
    and the two dual-filing models are exactly where that shows."""
    return int(external_id.split(":", 1)[1])


# What this pass asserts per entity kind, and therefore what it may tombstone.
MODEL_COVERAGE: tuple[str, ...] = ("summary",)
# A minted model (§7) additionally takes its NAME from the label: the one
# case where Wikidata names a model, because it is the filing source there.
# Matched models keep vPIC's name - the refresh path never covers "name".
MINTED_MODEL_COVERAGE: tuple[str, ...] = ("name", *MODEL_COVERAGE)
GENERATION_COVERAGE: tuple[str, ...] = (
    "name",
    "summary",
    "chassis_codes",
    "start_year",
    "end_year",
)


@dataclass
class WikidataModelsStats:
    """What one sweep pass did. Every counter is per-record or per-row."""

    processed: int = 0
    models_refreshed: int = 0
    models_matched: int = 0
    generations_created: int = 0
    generations_adopted: int = 0
    generations_refreshed: int = 0
    generation_links_asserted: int = 0
    generation_links_adopted: int = 0
    lines_created: int = 0
    lines_matched: int = 0
    memberships_inserted: int = 0
    line_generations_waiting: int = 0
    market_name_flagged: int = 0
    waits_no_held_maker: int = 0
    brand_voted: int = 0
    waits_unmatched: int = 0
    models_minted: int = 0
    mint_contested: int = 0
    duplicates_resolved: int = 0
    company_entities: int = 0
    assertions_inserted: int = 0
    assertions_superseded: int = 0
    flags_opened: int = 0
    flags_dismissed: int = 0

    def summary(self) -> str:
        return (
            f"processed={self.processed} | models: refreshed={self.models_refreshed} "
            f"matched={self.models_matched} | lines: created={self.lines_created} "
            f"matched={self.lines_matched} memberships={self.memberships_inserted} | "
            f"generations: created={self.generations_created} "
            f"adopted={self.generations_adopted} "
            f"refreshed={self.generations_refreshed} "
            f"links={self.generation_links_asserted} "
            f"(adopted={self.generation_links_adopted}) "
            f"line_case_waiting={self.line_generations_waiting} | "
            f"market_name_flags={self.market_name_flagged} "
            f"brand_voted={self.brand_voted} "
            f"waits: no_held_maker={self.waits_no_held_maker} "
            f"unmatched={self.waits_unmatched} company_entity={self.company_entities} | "
            f"minted={self.models_minted} (contested={self.mint_contested}, "
            f"duplicates_resolved={self.duplicates_resolved}) | "
            f"assertions={self.assertions_inserted} "
            f"(superseded={self.assertions_superseded}) "
            f"flags={self.flags_opened} (dismissed={self.flags_dismissed})"
        )


@dataclass
class _Subject:
    """Per-entity working state carried across the pass's phases."""

    record: RawRecord
    entity: ModelEntity
    held_companies: list[int] = field(default_factory=list)
    # Where the MATCH rungs look. The maker's companies, except where the
    # §2.2 vote redirected them to the brand the label wears (ADR 0022 §7);
    # the mint gate deliberately keeps reading `held_companies`.
    match_companies: list[int] = field(default_factory=list)
    decided: bool = False


class _WikidataModelsPass:
    """One run of the sweep pass. Holds the per-run caches; `run()` is the
    entry point. Deliberately not reusable across runs."""

    def __init__(self, session: Session):
        self.session = session
        self.stats = WikidataModelsStats()
        self.source = get_source(session, wikidata_models.SOURCE_NAME)
        self.decisions = DecisionLog(session, self.source.id, PASS_NAME)

        self._load_external_ids()
        self._load_companies()
        self._load_duplicate_bases()
        self._load_models()
        self._load_structure()
        self._load_wikipedia_precedence()
        self._load_open_flags()
        self._load_subjects()

    # --- per-run caches ------------------------------------------------------

    def _load_external_ids(self) -> None:
        """Which of our rows each swept QID already corresponds to."""
        session = self.session
        # QID -> entity id, partitioned by arc. One QID corresponds to at most
        # one row of one kind (ADR 0011 §4); the maps say which.
        self.company_by_qid: dict[str, int] = {}
        self.model_by_qid: dict[str, int] = {}
        self.generation_by_qid: dict[str, int] = {}
        for external_id, company_id, model_id, generation_id in session.execute(
            select(
                ExternalId.external_id,
                ExternalId.company_id,
                ExternalId.model_id,
                ExternalId.generation_id,
            ).where(ExternalId.source_id == self.source.id)
        ):
            if company_id is not None:
                self.company_by_qid[external_id] = company_id
            elif model_id is not None:
                self.model_by_qid[external_id] = model_id
            elif generation_id is not None:
                self.generation_by_qid[external_id] = generation_id

    def _load_duplicate_bases(self) -> None:
        """Base addresses under duplicate adjudication (ADR 0012 §7): each ruled
        target's (company, slug), with its registered members. A QID outside
        the registry never takes one of these addresses through an automatic
        rung - not by mint and not by label match - it flags for a ruling
        instead ("members left out of the registry keep contesting")."""
        by_slug = {c.slug: cid for cid, c in self.companies.items() if c.slug}
        self.duplicate_ruled_bases: dict[tuple[int, str], list[str]] = {}
        for qid, target in sorted(policy.WIKIDATA_DUPLICATE_NAMEPLATES.items()):
            company_slug, _, slug = target.partition(":")[2].partition("/")
            company_id = by_slug.get(company_slug)
            if company_id is not None:
                self.duplicate_ruled_bases.setdefault((company_id, slug), []).append(qid)

    def _load_companies(self) -> None:
        """Held companies, plus every name form they wear - what rung 3
        strips prefixes against and the cross-badge guard reads brands from."""
        session = self.session
        self.companies: dict[int, Company] = {c.id: c for c in session.scalars(select(Company))}
        self.company_norm: dict[int, str] = {
            cid: normalize_name(c.name) for cid, c in self.companies.items()
        }
        # Strip prefixes are the company's RECORDED names, plural (ADR 0013
        # §1): its own name plus its vPIC make name(s) - "Audi AG" is the
        # company but the badge on the car says "Audi", and vPIC holds that
        # as data ("AUDI"). Longest-first so "Mercedes-Benz" beats "Mercedes".
        self.company_prefixes: dict[int, tuple[str, ...]] = {}
        make_names: dict[int, set[str]] = {}
        for company_id, make_name in session.execute(
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
                make_names.setdefault(company_id, set()).add(normalize_name(make_name))
        for cid in self.companies:
            norms = {self.company_norm[cid]} | make_names.get(cid, set())
            self.company_prefixes[cid] = tuple(sorted(norms, key=len, reverse=True))
        # Exact-name lookup for the line destination rule; every company of a
        # name counts, so namesakes surface as ambiguity there.
        self.companies_by_norm: dict[str, list[int]] = {}
        for cid, norm in self.company_norm.items():
            if norm:
                self.companies_by_norm.setdefault(norm, []).append(cid)
        # Held-company norms, longest first, for the cross-badge guard's
        # "which brand does this label wear" question (ADR 0013 §3).
        self._brand_norms: list[tuple[str, int]] = sorted(
            ((norm, cid) for cid, norm in self.company_norm.items() if norm),
            key=lambda t: len(t[0]),
            reverse=True,
        )

    def _load_models(self) -> None:
        """As-filed models, indexed for rung 3, plus each model's anchor QID."""
        session = self.session
        self.models: dict[int, Model] = {m.id: m for m in session.scalars(select(Model))}
        self.models_by_name: dict[int, dict[str, list[int]]] = {}
        for m in self.models.values():
            self.models_by_name.setdefault(m.company_id, {}).setdefault(
                normalize_name(m.name), []
            ).append(m.id)

        # What the curated registries key on: the model's own FILING id
        # (vPIC's `model:<id>`, which all 1,735 live rows carry). A QID is a
        # match someone made later, not the model's own identifier - keying
        # on whichever id sorted first would have moved the key the day an
        # entity attached, which is the same silent-unmaking a slug key had.
        # Lookups still accept any id, so a registry entry written against a
        # QID keeps resolving; only the canonical key is narrowed.
        self.model_source_key: dict[int, str] = {}
        self.model_by_source_key: dict[str, int] = {}
        for external_id, model_id in session.execute(
            select(ExternalId.external_id, ExternalId.model_id).where(
                ExternalId.model_id.isnot(None)
            )
        ):
            self.model_by_source_key[external_id] = model_id
            if external_id.startswith("model:"):
                current = self.model_source_key.get(model_id)
                if current is None or _filing_number(external_id) < _filing_number(current):
                    self.model_source_key[model_id] = external_id

        # Reverse of model_by_qid: the model's ONE QID (lowest wins if legacy
        # data ever holds several - the deterministic assert-anchor, same
        # principle as the engine's merge canonicals). Rung-3 claims collect
        # here before resolution, keyed by model.
        self.qid_by_model: dict[int, str] = {}
        for qid, model_id in sorted(
            self.model_by_qid.items(), key=lambda kv: int(kv[0][1:]), reverse=True
        ):
            self.qid_by_model[model_id] = qid
        # model_id -> [(qid, method, rank)]; rank 0 = label form, 1 = alias.
        self.claims: dict[int, list[tuple[str, str, int]]] = {}

        # Live parent eras as (child, parent) pairs, and what each brand vote
        # decided. Corroboration only (ADR 0022 §7): recorded on the decision
        # so the labeled set can be measured, never consulted to gate.
        self.parent_pairs: set[tuple[int, int]] = set(
            session.execute(
                text(
                    """SELECT company_id, parent_company_id FROM company_relationships
                       WHERE superseded_by IS NULL"""
                )
            ).all()
        )
        self.vote_detail: dict[str, dict] = {}

        # The mint gate (§7): registry company QIDs resolved to company ids
        # through the same map every maker resolves through, so an alias QID
        # of a listed company gates identically. Slug occupancy is checked
        # against every model we hold, not just the sweep's - the natural key
        # `uq_models_company_id_slug` would reject the INSERT anyway, and a
        # collision is a review question, never an IntegrityError.
        self.mint_companies: set[int] = {
            self.company_by_qid[q]
            for q in policy.WIKIDATA_MINT_COMPANIES
            if q in self.company_by_qid
        }
        self.model_by_company_slug: dict[tuple[int, str], int] = {
            (m.company_id, m.slug): m.id for m in self.models.values() if m.slug
        }
        # (subject, company_id, name, slug) collected across rung 6, minted
        # together so label duplicates are seen as a group before any row exists.
        self.mint_candidates: list[tuple[_Subject, int, str, str]] = []

        # Prior decisions, so a refresh preserves HOW a match was made
        # (ADR 0013 §4) instead of overwriting the method with 'external_id'.
        self.prior_method: dict[str, str] = {
            qid: method
            for qid, method in session.execute(
                select(MatchDecision.external_id, MatchDecision.method).where(
                    MatchDecision.source_id == self.source.id,
                    MatchDecision.pass_name == PASS_NAME,
                    MatchDecision.method.isnot(None),
                    MatchDecision.method != "external_id",
                )
            )
        }

    def _load_structure(self) -> None:
        """Lines, memberships and generation slugs - the structure rungs 4-5
        resolve against."""
        session = self.session
        # Lines resolve by natural key - they hold no external ids (§4). The
        # key is the normalized NAME, never the slug: a slug is an address,
        # and re-addressing a line must not make the next run mint a second
        # one under the freed string.
        self.line_by_key: dict[tuple[int, str], int] = {
            (line.company_id, normalize_name(line.name)): line.id
            for line in session.scalars(select(ModelLine))
        }
        self.line_by_qid: dict[str, int] = {}  # this run's line resolutions
        self.line_holds: dict[tuple[str | None, str], str] = {
            (company_slug, normalize_name(name)): why
            for (company_slug, name), why in policy.WIKIDATA_LINE_HOLDS.items()
        }

        # Live memberships this source already asserts, for idempotent re-runs.
        self.live_memberships: set[tuple[int, int]] = {
            (line_id, model_id)
            for line_id, model_id in session.execute(
                select(ModelLineMember.model_line_id, ModelLineMember.model_id).where(
                    ModelLineMember.source_id == self.source.id,
                    ModelLineMember.superseded_by.is_(None),
                )
            )
        }

        # Generation slugs per company (ADR 0016 anchoring), for collision
        # detection before INSERT.
        self.generation_by_company_slug: dict[tuple[int, str], int] = {
            (g.company_id, g.slug): g.id for g in session.scalars(select(Generation))
        }

        # Live generation-model links: this source's own (for idempotent
        # re-runs) and the sourceless migration seeds (to adopt - a sourced
        # re-assertion supersedes the anonymous row rather than sitting
        # beside it forever).
        self.live_generation_links: set[tuple[int, int]] = set()
        self.anonymous_links: dict[tuple[int, int], int] = {}
        for link_id, generation_id, model_id, source_id in session.execute(
            select(
                GenerationModelLink.id,
                GenerationModelLink.generation_id,
                GenerationModelLink.model_id,
                GenerationModelLink.source_id,
            ).where(GenerationModelLink.superseded_by.is_(None))
        ):
            if source_id == self.source.id:
                self.live_generation_links.add((generation_id, model_id))
            elif source_id is None:
                self.anonymous_links[(generation_id, model_id)] = link_id

        # Ruled generation grain, resolved to rows once: qid -> (company_id,
        # model_id | None, display name). An anchor naming a company or model
        # not landed stays unresolved and flags in the structure phase.
        company_by_slug = {c.slug: cid for cid, c in self.companies.items() if c.slug}
        self.generation_grain: dict[str, tuple[int, int | None, str]] = {}
        for qid, (anchor, name) in policy.WIKIDATA_GENERATION_GRAIN.items():
            company_slug, _, model_slug = anchor.partition("/")
            company_id = company_by_slug.get(company_slug)
            if company_id is None:
                continue
            model_id = None
            if model_slug:
                model_id = self.model_by_company_slug.get((company_id, model_slug))
                if model_id is None:
                    continue
            self.generation_grain[qid] = (company_id, model_id, name)

    def _load_wikipedia_precedence(self) -> None:
        """Generation fields the infobox pass asserts live (ADR 0017 §2):
        infobox assertions outrank label-derived ones, so this pass keeps its
        assertions current but leaves those columns alone."""
        self.wikipedia_generation_fields: dict[int, frozenset[str]] = {}
        wikipedia_id = self.session.scalar(
            select(Source.id).where(Source.name == WIKIPEDIA_SOURCE_NAME)
        )
        if wikipedia_id is None:
            return
        rows: dict[int, set[str]] = {}
        for generation_id, field_name in self.session.execute(
            select(FieldProvenance.generation_id, FieldProvenance.field_name).where(
                FieldProvenance.generation_id.isnot(None),
                FieldProvenance.source_id == wikipedia_id,
                FieldProvenance.superseded_by.is_(None),
                FieldProvenance.observed_value.isnot(None),
            )
        ):
            rows.setdefault(generation_id, set()).add(field_name)
        self.wikipedia_generation_fields = {k: frozenset(v) for k, v in rows.items()}

    def _load_open_flags(self) -> None:
        """Questions already open, so a re-run does not ask them twice."""
        session = self.session
        # Open flags: record-scoped match_review on this sweep's records
        # (keyed by QID - a changed payload lands a new raw row and the open
        # question must not be asked twice), and entity-scoped multi_value
        # keys for the generations this pass writes.
        self.open_match_flags: dict[str, list[ReconciliationFlag]] = {}
        for flag, external_id in session.execute(
            select(ReconciliationFlag, RawRecord.external_id)
            .join(RawRecord, ReconciliationFlag.raw_record_id == RawRecord.id)
            .where(
                ReconciliationFlag.kind == "match_review",
                ReconciliationFlag.status == "open",
                RawRecord.source_id == self.source.id,
                RawRecord.payload.op("->>")("sweep") == SWEEP_MARKER,
            )
        ):
            self.open_match_flags.setdefault(external_id, []).append(flag)
        self.open_entity_flags: set[tuple] = {
            ("generation", flag.generation_id, flag.kind, flag.field_name)
            for flag in session.scalars(
                select(ReconciliationFlag).where(
                    ReconciliationFlag.status == "open",
                    ReconciliationFlag.generation_id.isnot(None),
                )
            )
        }

    def _load_subjects(self) -> None:
        """Current record per QID within the models sweep, parsed once."""
        session = self.session
        self.subjects: dict[str, _Subject] = {}
        for record in current_records(session, self.source.id, sweep=SWEEP_MARKER):
            entity = wikidata_models.map_record(record.payload)
            if entity is not None:
                self.subjects[entity.qid] = _Subject(record=record, entity=entity)

        # Every QID some swept entity claims P179 membership in: the line
        # evidence (§2.4).
        self.p179_referenced: set[str] = {
            target for s in self.subjects.values() for target in s.entity.series_of
        }

    # --- bookkeeping ---------------------------------------------------------

    def _decide(
        self,
        subject: _Subject,
        rung: str | None,
        method: str | None,
        outcome: str,
        detail: dict | None = None,
    ) -> None:
        """Log the decision and mark the subject settled, so the later phases
        skip it. This pass revisits entities across phases, unlike the others."""
        subject.decided = True
        self.decisions.record(subject.record, outcome, rung=rung, method=method, detail=detail)

    def _flag(self, subject: _Subject, reason: str, detail: dict) -> None:
        full_detail = {"reason": reason, "qid": subject.entity.qid, **detail}
        existing = self.open_match_flags.get(subject.entity.qid)
        if existing is not None:
            # One open question per record - but the question can CHANGE
            # between runs (a cluster claimant becomes a market-name suspect
            # once the model's nameplate attaches; a cross-badge verdict
            # flips when the brand list changes). An open flag is the
            # current question, so its whole detail refreshes whenever the
            # current computation differs; only closes are immutable history.
            for flag in existing:
                if (flag.detail or {}) != full_detail:
                    flag.detail = full_detail
            return
        flag = ReconciliationFlag(
            kind="match_review",
            raw_record_id=subject.record.id,
            source_id=self.source.id,
            detail=full_detail,
        )
        self.session.add(flag)
        self.open_match_flags[subject.entity.qid] = [flag]
        self.stats.flags_opened += 1

    def _dismiss_flags(self, qid: str, resolution: str) -> None:
        """Every flag close records WHY (the review's resolution discipline):
        a dismissed flag with no reason is a labeled-set example lost."""
        for flag in self.open_match_flags.pop(qid, []):
            flag.status = "dismissed"
            flag.resolved_at = func.now()
            flag.detail = {**(flag.detail or {}), "resolution": resolution}
            self.stats.flags_dismissed += 1

    # --- facts ---------------------------------------------------------------

    def _assert_facts(
        self,
        arc_col: str,
        target,
        coverage: tuple[str, ...],
        facts: dict[str, tuple[str, object]],
        record: RawRecord,
        skip_projection: frozenset[str] = frozenset(),
    ) -> None:
        """The engine's shared upsert-tombstone-project (single-source
        projection, like the models pass: §6's full ladder becomes
        exercisable when a second source asserts at this level)."""
        inserted, superseded = assert_field_facts(
            self.session,
            arc_col=arc_col,
            entity=target,
            coverage=coverage,
            facts=facts,
            source_id=self.source.id,
            record=record,
            skip_projection=skip_projection,
        )
        self.stats.assertions_inserted += inserted
        self.stats.assertions_superseded += superseded

    def _enrich_model(self, model_id: int, subject: _Subject) -> None:
        model = self.models[model_id]
        facts: dict[str, tuple[str, object]] = {}
        if subject.entity.description:
            facts["summary"] = (subject.entity.description, subject.entity.description)
        self._assert_facts("model_id", model, MODEL_COVERAGE, facts, subject.record)

    # --- generation facts ----------------------------------------------------

    def _entity_generation_flag(
        self, generation_id: int, field_name: str, detail: dict, record: RawRecord
    ) -> None:
        key = ("generation", generation_id, "multi_value", field_name)
        if key in self.open_entity_flags:
            return
        self.session.add(
            ReconciliationFlag(
                kind="multi_value",
                generation_id=generation_id,
                field_name=field_name,
                detail=detail,
                source_id=self.source.id,
                raw_record_id=record.id,
            )
        )
        self.open_entity_flags.add(key)
        self.stats.flags_opened += 1

    def _generation_facts(
        self, generation: Generation, subject: _Subject, display_name: str
    ) -> None:
        """Assert what the entity says about its generation: name, summary,
        chassis codes (ambiguous extractions flag rather than guess), span
        years only when dates are asserted - span-less generations are legal
        (identity now, time later)."""
        entity = subject.entity
        facts: dict[str, tuple[str, object]] = {"name": (display_name, display_name)}
        if entity.description:
            facts["summary"] = (entity.description, entity.description)

        codes, ambiguous = extract_chassis_codes(entity.label, entity.aliases, display_name)
        if codes:
            facts["chassis_codes"] = ("|".join(codes), codes)
        if ambiguous:
            self._entity_generation_flag(
                generation.id,
                "chassis_codes",
                {"ambiguous_tokens": ambiguous, "accepted": codes, "qid": entity.qid},
                subject.record,
            )

        if entity.start_years:
            facts["start_year"] = (
                "|".join(map(str, entity.start_years)),
                min(entity.start_years),
            )
            if len(entity.start_years) > 1:
                self._entity_generation_flag(
                    generation.id,
                    "start_year",
                    {"claims": list(entity.start_years), "qid": entity.qid},
                    subject.record,
                )
        if entity.end_years:
            facts["end_year"] = ("|".join(map(str, entity.end_years)), max(entity.end_years))
            if len(entity.end_years) > 1:
                self._entity_generation_flag(
                    generation.id,
                    "end_year",
                    {"claims": list(entity.end_years), "qid": entity.qid},
                    subject.record,
                )

        self._assert_facts(
            "generation_id",
            generation,
            GENERATION_COVERAGE,
            facts,
            subject.record,
            skip_projection=self.wikipedia_generation_fields.get(generation.id, frozenset()),
        )

    # --- rungs 1-3: match ----------------------------------------------------

    def _attach_model(self, model_id: int, subject: _Subject) -> None:
        self.session.add(
            ExternalId(model_id=model_id, source_id=self.source.id, external_id=subject.entity.qid)
        )
        self.session.flush()
        self.model_by_qid[subject.entity.qid] = model_id
        self.qid_by_model[model_id] = subject.entity.qid

    def _slug_pair(self, model_id: int) -> str:
        """Readable model reference for decision detail and flag candidates -
        display only. Registry lookups use `_model_key`."""
        model = self.models[model_id]
        company = self.companies[model.company_id]
        return f"{company.slug or company.name}/{model.slug or model.name}"

    def _model_key(self, model_id: int) -> str | None:
        """The model's own source id, which is what curated judgments key on.
        For a vPIC-born model that is its filing id; for a minted model (§7)
        the QID IS the filing. None for a model no source has identified - it
        cannot be the subject of a registry entry, so it can never be negated
        or curated either."""
        return self.model_source_key.get(model_id) or self.qid_by_model.get(model_id)

    def _name_hits(self, subject: _Subject) -> dict[int, str]:
        """Rung 3: exact-normalized hits across label/aliases and their
        make-prefix-stripped forms, negatives excluded. {model_id: (method,
        rank)} - rank 0 for label forms, 1 for alias forms (ADR 0013 §2:
        a label says what the entity IS; aliases list what it is also
        called, including its rebadges). Label forms are tried first, and a
        label hit upgrades an earlier alias hit on the same model."""
        entity = subject.entity
        hits: dict[int, tuple[str, int]] = {}
        names = [(entity.label, "label", 0)] + [(a, "alias", 1) for a in entity.aliases]
        for company_id in subject.match_companies:
            index = self.models_by_name.get(company_id, {})
            for name, kind, rank in names:
                if not name:
                    continue
                stripped = self._strip(name, company_id)
                for candidate, method in (
                    (name, f"exact_{kind}"),
                    (stripped, f"prefix_stripped_{kind}"),
                ):
                    for model_id in index.get(normalize_name(candidate), []):
                        if (entity.qid, self._model_key(model_id)) in (
                            policy.WIKIDATA_MODEL_NEGATIVES
                        ):
                            continue
                        if model_id not in hits or rank < hits[model_id][1]:
                            hits[model_id] = (method, rank)
        return hits

    def _vote_brand(self, subject: _Subject) -> bool:
        """Point the match rungs at the brand the label wears when no maker
        holds models (ADR 0022 §7). False stops the entity here.

        The vote is the line rungs' (`brand_destination`), so one entity
        cannot file under two companies depending on which rung reaches it.
        A parent era between the destination and the stated maker is written
        into the decision as corroboration and never gates: Wikidata calls
        Karmann the maker of the Chrysler Crossfire, no such era exists, and
        the Crossfire is still a Chrysler.

        The vote only ever ADDS a destination. An unclear one - a brand token
        two companies wear, a brand holding no models - changes nothing: the
        entity carries on exactly as it did before the vote existed, finding
        nothing at rung 3 and reaching the line and structure rungs, which
        ask that same question where it is answerable. Only an entity with no
        held maker at all still waits here.
        """
        entity = subject.entity
        makers = subject.held_companies
        wearers = line_brand_wearers(entity.label, self.companies_by_norm) if entity.label else []
        maker_id = makers[0] if len(makers) == 1 else None
        destination, _reason = brand_destination(maker_id, wearers, set(self.models_by_name))
        if destination is not None and destination in self.models_by_name:
            subject.match_companies = [destination]
            self.stats.brand_voted += 1
            self.vote_detail[entity.qid] = {
                "brand": self.companies[destination].slug or self.companies[destination].name,
                "makers": [self.companies[m].name for m in makers],
                "parent_link": any((destination, m) in self.parent_pairs for m in makers),
            }
            return True
        if makers:
            return True
        self.stats.waits_no_held_maker += 1
        self._decide(
            subject,
            "2",
            None,
            "waits_no_held_maker",
            {"makers": list(entity.makers)} if entity.makers else None,
        )
        return False

    def _strip(self, name: str, company_id: int) -> str:
        """Prefix-strip against every recorded name the company wears
        (ADR 0013 §1): its own name and its vPIC make name(s), longest
        first - 'Audi A3' strips under 'Audi AG' because vPIC says AUDI."""
        for prefix in self.company_prefixes.get(company_id, ()):
            stripped = strip_prefix(name, prefix, normalize_name)
            if stripped != name:
                return stripped
        return name

    def _label_brand(self, entity: ModelEntity) -> int | None:
        """The held company whose name the LABEL wears as a prefix, longest
        match - 'Subaru Trailseeker' wears Subaru. None when no held name
        prefixes the label (or the label IS a company name outright). The
        prefix must end on a word boundary of the label: normalization
        strips spacing, so a raw startswith would read 'Ranger' out of
        'Range Rover (1st generation)' - a brand the label does not wear."""
        if not entity.label:
            return None
        tokens: list[str] = []
        current: list[str] = []
        for ch in entity.label.casefold():
            if ch.isalnum():
                current.append(ch)
            elif current:
                tokens.append("".join(current))
                current = []
        if current:
            tokens.append("".join(current))
        # Cumulative whole-token prefixes, excluding the full label: a label
        # that IS a company name outright wears no brand.
        prefixes = {"".join(tokens[:k]) for k in range(1, len(tokens))}
        for norm, company_id in self._brand_norms:
            if norm in prefixes:
                return company_id
        return None

    def _is_cross_badge(self, entity: ModelEntity, model_id: int) -> bool:
        """ADR 0013 §3: the entity's label wears a different held brand than
        the model's company. Same-family prefixes (BMW / BMW M) don't count
        as foreign, in either direction."""
        brand = self._label_brand(entity)
        if brand is None:
            return False
        model_company = self.models[model_id].company_id
        if brand == model_company:
            return False
        b, m = self.company_norm[brand], self.company_norm[model_company]
        return not (b.startswith(m) or m.startswith(b))

    def _match_phase(self) -> None:
        for qid in sorted(self.subjects, key=lambda q: int(q[1:])):
            subject = self.subjects[qid]
            entity = subject.entity
            self.stats.processed += 1

            # Rung 1: the QID already corresponds to one of our rows. Only
            # the model's anchor QID asserts facts - a second mapped QID
            # re-asserting summary would contend for the same live slot and
            # supersede in a cycle on every run (the engine's §5 churn, one
            # level down). Legacy fan-in refreshes identity only.
            if qid in self.model_by_qid:
                model_id = self.model_by_qid[qid]
                self.stats.models_refreshed += 1
                if self.qid_by_model.get(model_id) == qid:
                    self._enrich_model(model_id, subject)
                    outcome = "model_refreshed"
                else:
                    outcome = "model_refreshed_secondary"
                self._dismiss_flags(qid, "resolves_to_existing_model")
                self._decide(subject, "1", self._refresh_method(subject), outcome)
                continue
            if qid in self.generation_by_qid:
                # Refreshed in the generation phase, where display names and
                # collision state are in hand.
                continue
            if qid in self.company_by_qid:
                # The entity IS a company we hold (1:1 rule: its QID cannot
                # also map to a model). Nothing to do at model level.
                self.stats.company_entities += 1
                self._decide(subject, "1", "external_id", "company_entity")
                continue

            # Rung 2: curated registry - recorded human judgments.
            curated = policy.WIKIDATA_MODEL_MATCHES.get(qid)
            if curated is not None:
                model_id = self.model_by_source_key.get(curated)
                if model_id is None:
                    self._flag(subject, "registry_unresolvable", {"registry": curated})
                    self._decide(subject, "2", "curated", "flagged_registry_unresolvable")
                else:
                    self._attach_model(model_id, subject)
                    self._enrich_model(model_id, subject)
                    self._dismiss_flags(qid, f"curated_match:{curated}")
                    self.stats.models_matched += 1
                    self._decide(subject, "2", "curated", "matched", {"model": curated})
                continue

            # The §2.2 gate: resolve P176 through the external-id map.
            subject.held_companies = sorted(
                {self.company_by_qid[m] for m in entity.makers if m in self.company_by_qid}
            )
            subject.match_companies = subject.held_companies
            # No maker holding models to match under: Wikidata names the group
            # (General Motors for the Corvette) while the filings sit under the
            # badge. The badge vote decides (ADR 0022 §7).
            if not any(
                c in self.models_by_name for c in subject.held_companies
            ) and not self._vote_brand(subject):
                continue

            # A registered duplicate's address comes from its ruling (§7), never
            # from its label - rung 3 does not claim for it.
            if qid in policy.WIKIDATA_DUPLICATE_NAMEPLATES:
                continue

            # Same rule one registry over: a grain-ruled entity is a
            # generation, and rung 3 must not claim it as a model.
            if qid in policy.WIKIDATA_GENERATION_GRAIN:
                continue

            # Rung 3: exact-normalized name/alias match, never fuzzy. A unique
            # hit is only a CLAIM: generation entities carry the bare nameplate
            # label, so one model can be claimed by several entities at once.
            # Correspondence is decided per MODEL in _resolve_claims, once
            # every claim is known.
            #
            # An entity claims only at its BEST evidence rank (ADR 0013 §2), so
            # a label hit plus an alias hit on another model is one claim on
            # the label's model - not an ambiguity. The alias is just the
            # entity listing its other names.
            hits = self._name_hits(subject)
            best_rank = min((rank for _, rank in hits.values()), default=None)
            best = {m: meth for m, (meth, rank) in hits.items() if rank == best_rank}
            if len(best) == 1:
                ((model_id, method),) = best.items()
                self.claims.setdefault(model_id, []).append((qid, method, best_rank))
            elif len(best) > 1:
                self._flag(
                    subject,
                    "ambiguous_model_match",
                    {
                        "label": entity.label,
                        "candidates": sorted(self._slug_pair(m) for m in best),
                    },
                )
                self._decide(
                    subject,
                    "3",
                    None,
                    "flagged_ambiguous",
                    {"candidates": sorted(self._slug_pair(m) for m in best)},
                )
            # 0 hits: falls through to the structure phases.

    def _claim_detail(self, qid: str) -> dict:
        """What a reviewer needs to tell a nameplate entity from its
        label-duplicate generations: description and sitelink title."""
        entity = self.subjects[qid].entity
        return {
            "qid": qid,
            "description": entity.description,
            "article": (entity.article or "").rsplit("/", 1)[-1] or None,
        }

    def _refresh_method(self, subject: _Subject) -> str:
        """The method to record on a rung-1 refresh (ADR 0013 §4): keep the
        method that MADE the match. Backfill by recomputing the hit when the
        log only holds 'external_id' (the pre-0013 overwrite) - resolving the
        maker gate here, since rung 1 runs before the §2.2 resolution."""
        prior = self.prior_method.get(subject.entity.qid)
        if prior:
            return prior
        model_id = self.model_by_qid.get(subject.entity.qid)
        if model_id is not None:
            if not subject.held_companies:
                subject.held_companies = sorted(
                    {
                        self.company_by_qid[m]
                        for m in subject.entity.makers
                        if m in self.company_by_qid
                    }
                )
            hit = self._name_hits(subject).get(model_id)
            if hit is not None:
                return hit[0]
        return "external_id"

    def _attach_match(self, model_id: int, subject: _Subject, method: str) -> None:
        self._attach_model(model_id, subject)
        self._enrich_model(model_id, subject)
        self._dismiss_flags(subject.entity.qid, f"matched:{self._slug_pair(model_id)}")
        self.stats.models_matched += 1
        detail = {"model": self._slug_pair(model_id)}
        vote = self.vote_detail.get(subject.entity.qid)
        if vote is not None:
            detail["brand_vote"] = vote
        self._decide(subject, "3", method, "matched", detail)

    def _flag_market_name(
        self, subject: _Subject, model_id: int, method: str, co_claimants: list[str]
    ) -> None:
        """An alias-form claim never clusters and never auto-attaches when
        contested or cross-badge (ADR 0013 §2-§3): the alias is Wikidata's
        record of what else this car is called - its market names and its
        rebadges - so the correspondence is a review question, not a match."""
        cross = self._is_cross_badge(subject.entity, model_id)
        brand = self._label_brand(subject.entity)
        detail = {
            "model": self._slug_pair(model_id),
            "via": method,
            "label": subject.entity.label,
            "cross_badge": cross,
            **({"label_brand": self.companies[brand].slug} if brand is not None else {}),
            **({"co_claimants": co_claimants} if co_claimants else {}),
        }
        self._flag(subject, "market_name_or_rebadge", detail)
        self.stats.market_name_flagged += 1
        self._decide(subject, "3", method, "flagged_market_name_or_rebadge", detail)

    def _resolve_claims(self) -> None:
        """Decide model correspondence per model, over all rung-3 claims,
        with label evidence outranking alias evidence (ADR 0013 §2).

        Label claimants: exactly one -> it attaches 1:1; several -> the
        label-duplicate cluster flag (picking the nameplate from identical labels
        would be a guess - the curated registry resolves it). Alias claimants
        never cluster: uncontested same-brand ones attach (the alias IS the
        as-filed market name - the Echo/LeCar species); contested or
        cross-badge ones flag as `market_name_or_rebadge`.

        One refinement keeps real structure out of the cluster: a claimant
        whose P179 points at ANOTHER claimant of the same model is that
        claimant's generation (Wikidata labels generation entities with the
        bare nameplate), so it defers to the structure phase - where the
        surviving claimant, once attached, makes it a direct-case generation.
        """
        # Attaches and cluster flags first; market-name flags after, so an
        # entity that label-attaches to its own model while alias-hitting
        # another is treated the same regardless of model iteration order.
        market_tasks: list[tuple[str, int, str, list[str]]] = []
        for model_id in sorted(
            self.claims, key=lambda m: min(int(q[1:]) for q, _, _ in self.claims[m])
        ):
            claimants = self.claims[model_id]
            model = self.models[model_id]
            ruled = self.duplicate_ruled_bases.get((model.company_id, model.slug))
            if ruled:
                # The address is under duplicate adjudication: no claimant attaches
                # by name, each asks for a ruling.
                for qid, method, _rank in sorted(claimants):
                    detail = {
                        "model": self._slug_pair(model_id),
                        "label": self.subjects[qid].entity.label,
                        "duplicates": sorted(ruled),
                    }
                    self._flag(self.subjects[qid], "duplicate_ruled_base", detail)
                    self._decide(
                        self.subjects[qid], "3", method, "flagged_duplicate_ruled_base", detail
                    )
                continue
            cluster = {q for q, _, _ in claimants}
            active = [
                (q, method, rank)
                for q, method, rank in claimants
                if not set(self.subjects[q].entity.series_of) & (cluster - {q})
            ]
            active.sort(key=lambda c: int(c[0][1:]))
            if not active:
                continue  # every claimant deferred to the structure phase
            label_active = [(q, m) for q, m, rank in active if rank == 0]
            alias_active = [(q, m) for q, m, rank in active if rank == 1]

            existing_qid = self.qid_by_model.get(model_id)
            if existing_qid is not None:
                # The model already has its QID. A returning LABEL claimant
                # is a source-side duplicate of it (curated-merge question);
                # a returning ALIAS claimant is the same market-name/rebadge
                # question it was before the model matched - the outcome must
                # not flip between runs.
                if label_active:
                    detail = {
                        "model": self._slug_pair(model_id),
                        "claimants": [self._claim_detail(q) for q, _ in label_active],
                        "existing_qid": existing_qid,
                    }
                    self._flag(self.subjects[label_active[0][0]], "second_qid_for_model", detail)
                    for q, method in label_active:
                        self._decide(self.subjects[q], "3", method, "flagged_second_qid", detail)
                co = sorted(q for q, _ in label_active + alias_active)
                for qid, method in alias_active:
                    market_tasks.append((qid, model_id, method, [c for c in co if c != qid]))
                continue

            if len(label_active) == 1:
                qid, method = label_active[0]
                self._attach_match(model_id, self.subjects[qid], method)
            elif len(label_active) > 1:
                detail = {
                    "model": self._slug_pair(model_id),
                    "claimants": [self._claim_detail(q) for q, _ in label_active],
                }
                self._flag(self.subjects[label_active[0][0]], "shared_model_match", detail)
                for qid, method in label_active:
                    self._decide(self.subjects[qid], "3", method, "flagged_shared_match", detail)
            elif len(alias_active) == 1 and not self._is_cross_badge(
                self.subjects[alias_active[0][0]].entity, model_id
            ):
                # Uncontested same-brand alias: Wikidata recording the
                # alternate name of exactly this filing (Echo, LeCar, Sunny).
                qid, method = alias_active[0]
                self._attach_match(model_id, self.subjects[qid], method)
                continue

            co = sorted(q for q, _ in label_active + alias_active)
            for qid, method in alias_active:
                market_tasks.append((qid, model_id, method, [c for c in co if c != qid]))

        for qid, model_id, method, co_claimants in market_tasks:
            if not self.subjects[qid].decided:
                self._flag_market_name(self.subjects[qid], model_id, method, co_claimants)

    # --- rung 4: lines -------------------------------------------------------

    def _line_phase(self) -> None:
        line_qids = [
            qid
            for qid in sorted(self.p179_referenced, key=lambda q: int(q[1:]))
            if qid in self.subjects
            and not self.subjects[qid].decided
            and qid not in self.model_by_qid
            and qid not in self.generation_by_qid
            and qid not in policy.WIKIDATA_GENERATION_GRAIN
        ]
        model_holding = set(self.models_by_name)
        for qid in line_qids:
            subject = self.subjects[qid]
            entity = subject.entity
            if entity.label is None:
                self._decide(subject, "4", None, "line_waits_no_label")
                continue
            if len(subject.held_companies) != 1:
                # No held maker (already a §2.2 wait for the counters) or an
                # ambiguous multi-maker series: the members' evidence keeps.
                outcome = (
                    "waits_no_held_maker"
                    if not subject.held_companies
                    else "line_waits_ambiguous_company"
                )
                self._decide(subject, "4", None, outcome)
                continue
            company_id = subject.held_companies[0]
            detail: dict = {}
            hold = self.line_holds.get(
                (
                    self.companies[company_id].slug,
                    normalize_name(self._strip(entity.label, company_id)),
                )
            )
            if hold is not None:
                detail["held"] = hold
            else:
                wearers = line_brand_wearers(entity.label, self.companies_by_norm)
                destination, reason = brand_destination(company_id, wearers, model_holding)
                flag_reason = f"line_{reason}" if reason else None
                if flag_reason is None and destination != company_id:
                    maker_key = (
                        company_id,
                        normalize_name(self._strip(entity.label, company_id)),
                    )
                    if maker_key in self.line_by_key:
                        # Derivation never abandons an existing maker-side row:
                        # a vote that turns clean later (a namesake merges, the
                        # brand gains its first model) would otherwise mint a
                        # duplicate at the destination and orphan this one.
                        # Relocation is the decision script's reviewed act; the
                        # pending move rides the flag until it runs.
                        flag_reason = "line_awaits_relocation"
                if flag_reason is None:
                    company_id = destination
                    if any(
                        (f.detail or {}).get("reason", "").startswith("line_")
                        for f in self.open_match_flags.get(qid, [])
                    ):
                        self._dismiss_flags(qid, "line_brand_resolved")
                else:
                    # The row keeps filing under its maker - staying put is
                    # not a guess - while the open question rides the flag.
                    self._flag(
                        subject,
                        flag_reason,
                        {
                            "label": entity.label,
                            "candidates": [
                                {
                                    "company": self.companies[cid].name,
                                    "slug": self.companies[cid].slug,
                                }
                                for cid in wearers
                            ],
                        },
                    )
                    detail["flagged"] = flag_reason
            name = self._strip(entity.label, company_id)
            slug = slugify(name)
            key = (company_id, normalize_name(name))
            line_id = self.line_by_key.get(key)
            if line_id is None:
                if not slug or slug in policy.RESERVED_ROUTE_SEGMENTS:
                    self._decide(subject, "4", None, "line_waits_unslugable", {"name": name})
                    continue
                line = ModelLine(company_id=company_id, slug=slug, name=name)
                self.session.add(line)
                self.session.flush()
                self.line_by_key[key] = line.id
                line_id = line.id
                self.stats.lines_created += 1
                self._decide(
                    subject, "4", "p179_referenced", "line_created", {"line": slug, **detail}
                )
            else:
                # Natural-key reuse: Wikidata's duplicate series entities
                # ("BMW 3 Series" x4) converge on one grouping row - lines
                # hold no external ids, so this is the §4 identity rule, not
                # a merge.
                self.stats.lines_matched += 1
                self._decide(
                    subject, "4", "p179_referenced", "line_matched", {"line": slug, **detail}
                )
            self.line_by_qid[qid] = line_id

    # --- memberships (§4) ----------------------------------------------------

    def _membership_phase(self) -> None:
        for qid in sorted(self.subjects, key=lambda q: int(q[1:])):
            subject = self.subjects[qid]
            model_id = self.model_by_qid.get(qid)
            if model_id is None:
                continue
            for target in subject.entity.series_of:
                line_id = self.line_by_qid.get(target)
                if line_id is None or (line_id, model_id) in self.live_memberships:
                    continue
                self.session.add(
                    ModelLineMember(
                        model_line_id=line_id,
                        model_id=model_id,
                        source_id=self.source.id,
                        raw_record_id=subject.record.id,
                        scraped_at=subject.record.last_seen_at,
                    )
                )
                self.live_memberships.add((line_id, model_id))
                self.stats.memberships_inserted += 1

    # --- rung 5-6: generations and the rest ----------------------------------

    def _assert_generation_link(self, generation_id: int, model_id: int, subject: _Subject) -> None:
        """One live sourced link per (generation, model). A sourced assertion
        supersedes the pair's anonymous migration seed - the seed said only
        "the old FK pointed here"; this row says who asserts it and from
        which record."""
        if (generation_id, model_id) in self.live_generation_links:
            return
        link = GenerationModelLink(
            generation_id=generation_id,
            model_id=model_id,
            source_id=self.source.id,
            raw_record_id=subject.record.id,
            scraped_at=subject.record.last_seen_at,
        )
        self.session.add(link)
        anonymous_id = self.anonymous_links.pop((generation_id, model_id), None)
        if anonymous_id is not None:
            self.session.flush()
            self.session.get(GenerationModelLink, anonymous_id).superseded_by = link.id
            self.stats.generation_links_adopted += 1
        self.live_generation_links.add((generation_id, model_id))
        self.stats.generation_links_asserted += 1

    def _refresh_generation(self, subject: _Subject) -> None:
        qid = subject.entity.qid
        generation = self.session.get(Generation, self.generation_by_qid[qid])
        grain = policy.WIKIDATA_GENERATION_GRAIN.get(qid)
        if grain is not None:
            # The ruled display, not the label: "Chevrolet Corvette C7"
            # stripped would rename the row the ruling called "C7". Read
            # from the registry itself, not the resolved anchors - an
            # anchor that stops resolving must not rename the row either.
            display = grain[1]
        elif subject.entity.label and qid not in policy.WIKIDATA_DUPLICATE_NAMEPLATES:
            display = self._strip(subject.entity.label, generation.company_id)
        else:
            display = generation.name
        self._generation_facts(generation, subject, display)
        for target in subject.entity.series_of:
            model_id = self.model_by_qid.get(target)
            if model_id is not None:
                self._assert_generation_link(generation.id, model_id, subject)
        self.stats.generations_refreshed += 1
        self._dismiss_flags(subject.entity.qid, "resolves_to_existing_generation")
        self._decide(
            subject,
            "1",
            self.prior_method.get(subject.entity.qid, "external_id"),
            "generation_refreshed",
        )

    def _create_generation(self, model_id: int, subject: _Subject) -> None:
        entity = subject.entity
        model = self.models[model_id]
        display = self._strip(entity.label, model.company_id)
        slug = slugify(display)
        reason = nonconforming_slug(slug)
        if reason is not None:
            # The drift guard (ADR 0019 §4): a label shaped like a section
            # heading or a source page title flags instead of minting.
            self._flag(
                subject,
                "generation_slug_nonconforming",
                {"label": entity.label, "slug": slug, "reason": reason},
            )
            self._decide(
                subject,
                "5",
                "p179_member_of_matched_model",
                "flagged_nonconforming_slug",
                {"model": self._slug_pair(model_id), "slug": slug, "reason": reason},
            )
            return
        occupant = self.generation_by_company_slug.get((model.company_id, slug))
        if occupant is not None:
            # Two source entities, one slug, one model: usually Wikidata's
            # duplicate-entity disease. Flag - the models-pass rule: never
            # auto-suffix an identity (ADR 0010 §2.3, one level down).
            self._flag(
                subject,
                "generation_slug_collision",
                {
                    "label": entity.label,
                    "slug": slug,
                    "model": self._slug_pair(model_id),
                    "existing_generation_id": occupant,
                },
            )
            self._decide(
                subject,
                "5",
                "p179_member_of_matched_model",
                "flagged_generation_collision",
                {"model": self._slug_pair(model_id), "slug": slug},
            )
            return
        generation = Generation(company_id=model.company_id, slug=slug, name=display)
        self.session.add(generation)
        self.session.flush()
        self.generation_by_company_slug[(model.company_id, slug)] = generation.id
        self.session.add(
            ExternalId(
                generation_id=generation.id,
                source_id=self.source.id,
                external_id=entity.qid,
            )
        )
        self.session.flush()
        self.generation_by_qid[entity.qid] = generation.id
        self._assert_generation_link(generation.id, model_id, subject)
        self._generation_facts(generation, subject, display)
        self.stats.generations_created += 1
        self._dismiss_flags(entity.qid, f"generation_created:{self._slug_pair(model_id)}/{slug}")
        self._decide(
            subject,
            "5",
            "p179_member_of_matched_model",
            "generation_created",
            {"model": self._slug_pair(model_id), "generation": slug},
        )

    def _grain_generation(self, subject: _Subject) -> None:
        """A registry-ruled generation Wikidata files as a series. Mint it
        under the ruled anchor - or adopt the generation already standing at
        its slug - and attach the external id, which is what keeps the line
        track from ever deriving this entity again."""
        entity = subject.entity
        resolved = self.generation_grain.get(entity.qid)
        if resolved is None:
            self._flag(
                subject,
                "generation_grain_unresolved",
                {
                    "label": entity.label,
                    "anchor": policy.WIKIDATA_GENERATION_GRAIN[entity.qid][0],
                },
            )
            self._decide(subject, "5", "generation_grain_registry", "flagged_unresolved_anchor")
            return
        company_id, model_id, display = resolved
        slug = slugify(display)
        occupant = self.generation_by_company_slug.get((company_id, slug))
        if occupant is not None:
            generation = self.session.get(Generation, occupant)
            self.stats.generations_adopted += 1
        else:
            generation = Generation(company_id=company_id, slug=slug, name=display)
            self.session.add(generation)
            self.session.flush()
            self.generation_by_company_slug[(company_id, slug)] = generation.id
            self.stats.generations_created += 1
        self.session.add(
            ExternalId(
                generation_id=generation.id,
                source_id=self.source.id,
                external_id=entity.qid,
            )
        )
        self.session.flush()
        self.generation_by_qid[entity.qid] = generation.id
        if model_id is not None:
            self._assert_generation_link(generation.id, model_id, subject)
        self._generation_facts(generation, subject, display)
        outcome = "generation_adopted" if occupant is not None else "generation_created"
        detail = {"generation": f"{self.companies[company_id].slug}/{slug}"}
        if model_id is not None:
            detail["model"] = self._slug_pair(model_id)
        self._dismiss_flags(entity.qid, f"{outcome}:{detail['generation']}")
        self._decide(subject, "5", "generation_grain_registry", outcome, detail)

    def _structure_phase(self) -> None:
        for qid in sorted(self.subjects, key=lambda q: int(q[1:])):
            subject = self.subjects[qid]
            if qid in policy.NOT_A_GENERATION and not subject.decided:
                # Ruled wrong-grain (ADR 0018 §1): no link assertion, no
                # refresh, no creation - the registry gate that keeps the
                # demotion script's retirement from resurrecting on re-run.
                # Existing assertions stay live; the record stays in raw.
                self._decide(
                    subject,
                    "5",
                    "not_a_generation_registry",
                    "held_not_a_generation",
                    {"verdict": policy.NOT_A_GENERATION[qid]},
                )
                continue
            if qid in self.generation_by_qid and not subject.decided:
                self._refresh_generation(subject)
                continue
            if subject.decided:
                continue
            if qid in policy.WIKIDATA_GENERATION_GRAIN:
                self._grain_generation(subject)
                continue
            entity = subject.entity

            model_targets = sorted(
                {self.model_by_qid[t] for t in entity.series_of if t in self.model_by_qid}
            )
            line_targets = [t for t in entity.series_of if t in self.line_by_qid]

            if len(model_targets) == 1 and entity.label:
                self._create_generation(model_targets[0], subject)
                continue
            if len(model_targets) > 1:
                self._flag(
                    subject,
                    "ambiguous_generation_parent",
                    {
                        "label": entity.label,
                        "candidates": sorted(self._slug_pair(m) for m in model_targets),
                    },
                )
                self._decide(
                    subject,
                    "5",
                    None,
                    "flagged_ambiguous_generation",
                    {"candidates": sorted(self._slug_pair(m) for m in model_targets)},
                )
                continue
            if line_targets:
                # The BMW shape: a generation entity of a LINE waits in raw -
                # instantiating E46 under each member needs vPIC's year lists
                # to overlap against, which is the year-pass ADR's job (§5).
                self.stats.line_generations_waiting += 1
                self._decide(
                    subject,
                    "5",
                    "p179_member_of_line",
                    "line_generation_waits",
                    {"lines": line_targets, "chains": list(entity.follows + entity.followed_by)},
                )
                continue

            # Rung 6, mint side (§7): under a registry company, an entity
            # that fell through every match and structure rung is the fill,
            # not a near-miss - its trigram neighbours are its own siblings
            # minting beside it, so the candidates queue would only echo the
            # batch back. Collected, not minted: duplicates must be seen together.
            minted = self._mint_candidate(subject)
            if minted is not None:
                self.mint_candidates.append((subject, *minted))
                continue

            # Rung 6: no structural evidence. Flag with candidates only when
            # a held company AND near-misses exist; otherwise wait, unflagged
            # (§3: the tabled expansion's warehouse).
            candidates: list[dict] = []
            if subject.held_companies and entity.label:
                for company_id in subject.held_companies:
                    stripped = self._strip(entity.label, company_id)
                    candidates = trigram_candidates(
                        self.session, "models", stripped, company_id=company_id
                    )
                    if candidates:
                        break
            if candidates:
                self._flag(
                    subject,
                    "no_model_match",
                    {"label": entity.label, "candidates": candidates},
                )
                self._decide(
                    subject, "6", "trigram", "flagged_candidates", {"candidates": candidates}
                )
            else:
                chain_evidence = bool(
                    set(entity.follows + entity.followed_by)
                    & (set(self.model_by_qid) | set(self.generation_by_qid))
                )
                self.stats.waits_unmatched += 1
                self._decide(
                    subject,
                    "6",
                    None,
                    "waits_unmatched",
                    {"chained_to_held": True} if chain_evidence else None,
                )

    # --- rung 6, mint (§7) -----------------------------------------------------

    def _mint_candidate(self, subject: _Subject) -> tuple[int, str, str] | None:
        """(company_id, name, slug) when the entity may mint, else None.

        Every condition is an under-admission by design - a skipped entity
        just keeps waiting, and widening costs one registry review:

        - sole asserted maker, resolved to one registry company (a multi-
          maker entity is a JV or a rebadge, a judgment not a mint);
        - a real label, not the bare QID the label service fell back to;
        - no membership evidence: P179/P361 point at something we do not
          hold, and such an entity may be a generation of it (level is
          structural, never label - the founding ADR 0012 lesson);
        - no excluded word: concept/prototype/race cars are an unruled
          scope question and wait for it;
        - the label does not wear ANOTHER held marque ('Fiat 850' files
          under Abarth with a label that says whose car it is), same-family
          prefixes tolerated as in the cross-badge guard;
        - the stripped name is not the company name itself.
        """
        entity = subject.entity
        if len(subject.held_companies) != 1 or len(entity.makers) != 1:
            return None
        company_id = subject.held_companies[0]
        if company_id not in self.mint_companies:
            return None
        label = entity.label or ""
        if not label or _BARE_QID.fullmatch(label):
            return None
        if entity.series_of or entity.part_of:
            return None
        if _MINT_EXCLUDE.search(f"{label} {entity.description or ''}"):
            return None
        brand = self._label_brand(entity)
        if brand is not None and brand != company_id:
            b, m = self.company_norm[brand], self.company_norm[company_id]
            if not (b.startswith(m) or m.startswith(b)):
                return None
        name = self._mint_name(label, company_id)
        if normalize_name(name) == self.company_norm[company_id]:
            return None
        return company_id, name, slugify(name)

    def _mint_name(self, label: str, company_id: int) -> str:
        """The minted model's name: the label with the marque stripped.

        `_strip` handles the recorded-name cases ("Citroën 2CV" under
        "Citroën"). It cannot handle a company whose FILED name carries a
        legal tail the badge does not - "Škoda 100" never starts with
        "Škoda Auto" - so the fallback cuts the longest run of whole tokens
        the label and the company name share ("Škoda", "Aston Martin"),
        provided a remainder survives. Mint-only: rung 3 matching keeps
        `_strip` untouched, because widening what MATCHES is a different
        decision from widening what a new row is CALLED.
        """
        name = self._strip(label, company_id)
        if name != label:
            return name
        company_tokens = [normalize_name(t) for t in self.companies[company_id].name.split()]
        label_tokens = label.split()
        shared = 0
        for lt, ct in zip(label_tokens, company_tokens, strict=False):
            if normalize_name(lt) != ct:
                break
            shared += 1
        if 0 < shared < len(label_tokens):
            return " ".join(label_tokens[shared:])
        return name

    def _mint_phase(self) -> None:
        """Mint the collected candidates, contested slugs held as a group.

        Label duplicates are the trap the census predicted: four distinct "Škoda
        Rapid" entities are four different-era cars sharing a nameplate, and
        minting any one of them would enthrone an arbitrary era at the plain
        address. Unlike the vPIC collision rule (§2.3: lower filing keeps the
        slug), NO duplicate mints - which of them deserves the plain name, and
        what the others should be called, is one naming ruling per group."""

        def base_slug(name: str) -> str:
            base = _TRAILING_PAREN.sub("", name)
            base = _TRAILING_ROMAN.sub("", base)
            return slugify(base) or slugify(name)

        groups: dict[tuple[int, str], list[tuple[_Subject, int, str, str]]] = {}
        for cand in self.mint_candidates:
            qid = cand[0].entity.qid
            if (
                qid in self.model_by_qid
                or qid in self.generation_by_qid
                or qid in policy.WIKIDATA_DUPLICATE_NAMEPLATES
            ):
                continue  # ruled (or ruled-and-awaiting-evidence); no longer contested
            groups.setdefault((cand[1], base_slug(cand[2])), []).append(cand)

        for (company_id, base), cands in sorted(groups.items()):
            company = self.companies[company_id]
            # A group is every candidate sharing an era-stripped base:
            # exact duplicates ("C6"/"C6") and dressed siblings ("Dokker"/
            # "Dokker I"/"Dokker II", "A110"/"A110 (2017)") alike. A base
            # already worn by a held model contests a single candidate the
            # same way - "A110 (2017)" beside a live A110 is the same
            # question as beside a candidate one.
            base_occupant = self.model_by_company_slug.get((company_id, base))
            dressed_single = len(cands) == 1 and cands[0][3] != base
            ruled_members = self.duplicate_ruled_bases.get((company_id, base), [])
            if len(cands) > 1 or ruled_members or (base_occupant is not None and dressed_single):
                duplicates = sorted({c[0].entity.qid for c in cands} | set(ruled_members))
                if base_occupant is not None:
                    duplicates.append(self._slug_pair(base_occupant))
                for subject, _, _name, slug in cands:
                    self._flag(
                        subject,
                        "mint_label_duplicates",
                        {"label": subject.entity.label, "slug": slug, "duplicates": duplicates},
                    )
                    self._decide(
                        subject,
                        "6",
                        "registry_mint",
                        "flagged_mint_duplicates",
                        {"slug": slug, "duplicates": duplicates},
                    )
                self.stats.mint_contested += len(cands)
                continue

            ((subject, _, name, slug),) = cands
            entity = subject.entity
            reason = nonconforming_slug(slug)
            if reason is not None:
                self._flag(
                    subject,
                    "mint_slug_nonconforming",
                    {"label": entity.label, "slug": slug, "reason": reason},
                )
                self._decide(
                    subject,
                    "6",
                    "registry_mint",
                    "flagged_mint_nonconforming",
                    {"slug": slug, "reason": reason},
                )
                self.stats.mint_contested += 1
                continue
            occupant = self.model_by_company_slug.get((company_id, slug))
            if occupant is not None:
                # The slug is worn by a model the entity did NOT name-match:
                # either the same nameplate under a spelling rung 3 cannot
                # see, or a genuine duplicate. A human rules; a match lands in
                # WIKIDATA_MODEL_MATCHES, a duplicate gets its naming ruling.
                self._flag(
                    subject,
                    "mint_slug_occupied",
                    {"label": entity.label, "slug": slug, "model": self._slug_pair(occupant)},
                )
                self._decide(
                    subject,
                    "6",
                    "registry_mint",
                    "flagged_mint_occupied",
                    {"model": self._slug_pair(occupant)},
                )
                self.stats.mint_contested += 1
                continue

            model = Model(company_id=company_id, slug=slug, name=name)
            self.session.add(model)
            self.session.flush()
            self.models[model.id] = model
            self.model_by_company_slug[(company_id, slug)] = model.id
            self.models_by_name.setdefault(company_id, {}).setdefault(
                normalize_name(name), []
            ).append(model.id)
            self._attach_model(model.id, subject)
            facts: dict[str, tuple[str, object]] = {"name": (name, name)}
            if entity.description:
                facts["summary"] = (entity.description, entity.description)
            self._assert_facts("model_id", model, MINTED_MODEL_COVERAGE, facts, subject.record)
            self._dismiss_flags(entity.qid, f"model_minted:{company.slug}/{slug}")
            self.stats.models_minted += 1
            self._decide(
                subject,
                "6",
                "registry_mint",
                "model_minted",
                {"model": f"{company.slug}/{slug}"},
            )

    # --- the duplicate rulings (ADR 0012 §7) ---------------------------------------

    def _duplicate_span(self, wikipedia_id: int | None, qid: str):
        """Decision-time read of the era's own landed article: (production
        span, title parenthetical). Deciding resolvability and naming only -
        the span itself is asserted by the Wikipedia pass, under its own
        provenance."""
        if wikipedia_id is None:
            return None, None
        record = self.session.scalar(
            select(RawRecord)
            .where(
                RawRecord.source_id == wikipedia_id,
                RawRecord.external_id == f"article:{qid}",
            )
            .order_by(RawRecord.last_seen_at.desc(), RawRecord.id.desc())
        )
        if record is None:
            return None, None
        title = record.payload.get("title", "")
        if not same_subject(record.payload.get("requested_title", ""), title):
            return None, None
        top = parse_article(title, record.payload.get("wikitext", "")).top_wikitext
        paren = re.search(r"\(([^()]+)\)\s*$", title)
        return parse_infobox(title, top).production, paren.group(1) if paren else None

    def _duplicates_phase(self) -> None:
        """Apply recorded duplicate rulings: the base nameplate is one model row;
        registered era entities become generations under it. This rung sets
        identity and attachment - time arrives from each era's own article
        through the Wikipedia pass. Unregistered members keep contesting."""
        if not policy.WIKIDATA_DUPLICATE_NAMEPLATES:
            return
        wikipedia_id = self.session.scalar(
            select(Source.id).where(Source.name == WIKIPEDIA_SOURCE_NAME)
        )
        company_by_slug = {c.slug: cid for cid, c in self.companies.items() if c.slug}
        for qid, target in sorted(policy.WIKIDATA_DUPLICATE_NAMEPLATES.items()):
            subject = self.subjects.get(qid)
            if subject is None or qid in self.model_by_qid or qid in self.generation_by_qid:
                continue
            kind, _, pair = target.partition(":")
            company_slug, _, model_slug = pair.partition("/")
            company_id = company_by_slug.get(company_slug)
            if company_id is None:
                log.warning("WIKIDATA_DUPLICATE_NAMEPLATES[%s] -> %r: no such company", qid, target)
                continue
            entity = subject.entity
            model_id = self.model_by_company_slug.get((company_id, model_slug))
            # The ruled slug decides how much of the label is the nameplate:
            # "Renault Type I" keeps its numeral under `type-i`, "Express I"
            # sheds it under `express`.
            full_name = self._mint_name(entity.label or "", company_id)
            if model_id is not None:
                base_name = self.models[model_id].name
            elif slugify(full_name) == model_slug:
                base_name = full_name
            else:
                base_name = _TRAILING_ROMAN.sub("", _TRAILING_PAREN.sub("", full_name)).strip()

            if kind == "model":
                if model_id is None:
                    model = Model(company_id=company_id, slug=model_slug, name=base_name)
                    self.session.add(model)
                    self.session.flush()
                    self.models[model.id] = model
                    self.model_by_company_slug[(company_id, model_slug)] = model.id
                    model_id = model.id
                elif self.qid_by_model.get(model_id) is not None:
                    self._decide(
                        subject,
                        "7",
                        "duplicate_ruling",
                        "duplicate_model_conflict",
                        {"model": pair},
                    )
                    continue
                self._attach_model(model_id, subject)
                facts: dict[str, tuple[str, object]] = {"name": (base_name, base_name)}
                if entity.description:
                    facts["summary"] = (entity.description, entity.description)
                self._assert_facts(
                    "model_id", self.models[model_id], MINTED_MODEL_COVERAGE, facts, subject.record
                )
                self.stats.duplicates_resolved += 1
                self._dismiss_flags(qid, f"duplicate_model:{pair}")
                self._decide(
                    subject, "7", "duplicate_ruling", "duplicate_model_resolved", {"model": pair}
                )
                continue

            span, era_label = self._duplicate_span(wikipedia_id, qid)
            if span is None and not entity.start_years:
                self._decide(
                    subject, "7", "duplicate_ruling", "duplicate_era_awaits_span", {"model": pair}
                )
                continue
            if model_id is None:
                # An all-era group: no entity means the nameplate, so the
                # model row is created bare and carries no QID.
                model = Model(company_id=company_id, slug=model_slug, name=base_name)
                self.session.add(model)
                self.session.flush()
                self.models[model.id] = model
                self.model_by_company_slug[(company_id, model_slug)] = model.id
                model_id = model.id
            if era_label:
                display = f"{base_name} ({era_label})"
            elif span is not None:
                display = f"{base_name} ({span.start}–{span.end or 'present'})"
            else:
                display = f"{base_name} ({min(entity.start_years)})"
            slug = slugify(display)
            if nonconforming_slug(slug) is not None:
                # A span separates eras but may not wear an address
                # (ADR 0019 §4): the era lands unaddressed until a code or
                # ordinal arrives.
                slug = None
            elif self.generation_by_company_slug.get((company_id, slug)) is not None:
                self._decide(
                    subject,
                    "7",
                    "duplicate_ruling",
                    "duplicate_era_collision",
                    {"model": pair, "slug": slug},
                )
                continue
            generation = Generation(company_id=company_id, slug=slug, name=display)
            self.session.add(generation)
            self.session.flush()
            if slug is not None:
                self.generation_by_company_slug[(company_id, slug)] = generation.id
            self.session.add(
                ExternalId(generation_id=generation.id, source_id=self.source.id, external_id=qid)
            )
            self.session.flush()
            self.generation_by_qid[qid] = generation.id
            self._assert_generation_link(generation.id, model_id, subject)
            self._generation_facts(generation, subject, display)
            self.stats.duplicates_resolved += 1
            self._dismiss_flags(qid, f"duplicate_era:{pair}")
            self._decide(
                subject,
                "7",
                "duplicate_ruling",
                "duplicate_era_resolved",
                {"model": pair, "generation": slug or f"#{generation.id}"},
            )

    # --- the pass ------------------------------------------------------------

    def run(self) -> WikidataModelsStats:
        log.info(
            "wikidata models pass: %d current sweep records (reconciler v%s)",
            len(self.subjects),
            policy.RECONCILER_VERSION,
        )
        self._match_phase()
        self._resolve_claims()
        self._line_phase()
        self._membership_phase()
        self._structure_phase()
        self._duplicates_phase()
        self._mint_phase()
        self.decisions.flush()
        for subject in self.subjects.values():
            mark_reconciled(self.session, subject.record)
        return self.stats


def run_wikidata_models_pass(session: Session) -> WikidataModelsStats:
    """Run the full Wikidata models-sweep pass (ADR 0012). Commits on success."""
    stats = _WikidataModelsPass(session).run()
    session.commit()
    log.info("wikidata models pass done: %s", stats.summary())
    return stats


if __name__ == "__main__":
    from carmanac.runner import run

    run(run_wikidata_models_pass)
