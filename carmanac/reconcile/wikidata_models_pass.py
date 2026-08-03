"""The Wikidata models-sweep pass (ADR 0012): match and enrich, never create.

Level is decided per make, against the as-filed models. For every current
models-sweep record (bare QID, `sweep: models` marker), the ladder is:

    1. existing external id            -> refresh (model or generation)
    2. curated registry                -> recorded human judgments
    3. exact-normalized name/alias     -> model correspondence (incl. the
       match under the P176 company       make-prefix-stripped form; unique
                                          hits only, never fuzzy)
    4. line evidence (P179 members)    -> a `model_lines` row, never a model
    5. generation evidence (P179 to a  -> a `generations` row under it
       matched model)
    6. otherwise                       -> flag with candidates only when a
                                          held company + near-misses exist;
                                          else wait in raw, unflagged

Four rules hold across the ladder, each one a place where guessing was
available and rejected:

- **Match and enrich only.** A model-shaped entity under a held company with
  no as-filed match waits - no row, no flag. One open make question must not
  fan out into thousands of model-shaped copies (ADR 0010 §1).
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
from dataclasses import dataclass, field

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from carmanac.db.models import (
    Company,
    ExternalId,
    FieldProvenance,
    Generation,
    MatchDecision,
    Model,
    ModelLine,
    ModelLineMember,
    RawRecord,
    ReconciledRecord,
    ReconciliationFlag,
)
from carmanac.ingest.landing import get_source
from carmanac.ingest.wikidata.models import SWEEP_MARKER
from carmanac.reconcile import policy
from carmanac.reconcile.engine import _current_records, _supersede, slugify
from carmanac.reconcile.matching import normalize_name
from carmanac.reconcile.sources import wikidata_models
from carmanac.reconcile.sources.wikidata_models import (
    ModelEntity,
    extract_chassis_codes,
    strip_prefix,
)

log = logging.getLogger(__name__)

PASS_NAME = "wikidata_models"

# What this pass asserts per entity kind, and therefore what it may tombstone.
MODEL_COVERAGE: tuple[str, ...] = ("summary",)
GENERATION_COVERAGE: tuple[str, ...] = (
    "name",
    "summary",
    "chassis_codes",
    "start_year",
    "end_year",
)

_DECISION_CHUNK = 500


@dataclass
class WikidataModelsStats:
    """What one sweep pass did. Every counter is per-record or per-row."""

    processed: int = 0
    models_refreshed: int = 0
    models_matched: int = 0
    generations_created: int = 0
    generations_refreshed: int = 0
    lines_created: int = 0
    lines_matched: int = 0
    memberships_inserted: int = 0
    line_generations_waiting: int = 0
    market_name_flagged: int = 0
    waits_no_held_maker: int = 0
    waits_unmatched: int = 0
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
            f"refreshed={self.generations_refreshed} "
            f"line_case_waiting={self.line_generations_waiting} | "
            f"market_name_flags={self.market_name_flagged} "
            f"waits: no_held_maker={self.waits_no_held_maker} "
            f"unmatched={self.waits_unmatched} company_entity={self.company_entities} | "
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
    decided: bool = False


class _WikidataModelsPass:
    """One run of the sweep pass. Holds the per-run caches; `run()` is the
    entry point. Deliberately not reusable across runs."""

    def __init__(self, session: Session):
        self.session = session
        self.stats = WikidataModelsStats()
        self.source = get_source(session, wikidata_models.SOURCE_NAME)
        self.decisions: dict[str, dict] = {}

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

        # Held companies and their models: the rung-3 name index is per
        # company, keyed by normalized model name.
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
        # Held-company norms, longest first, for the cross-badge guard's
        # "which brand does this label wear" question (ADR 0013 §3).
        self._brand_norms: list[tuple[str, int]] = sorted(
            ((norm, cid) for cid, norm in self.company_norm.items() if norm),
            key=lambda t: len(t[0]),
            reverse=True,
        )
        self.company_by_slug: dict[str, int] = {c.slug: c.id for c in self.companies.values()}
        self.models: dict[int, Model] = {m.id: m for m in session.scalars(select(Model))}
        self.models_by_name: dict[int, dict[str, list[int]]] = {}
        self.model_by_company_slug: dict[tuple[int, str], int] = {}
        for m in self.models.values():
            self.models_by_name.setdefault(m.company_id, {}).setdefault(
                normalize_name(m.name), []
            ).append(m.id)
            self.model_by_company_slug[(m.company_id, m.slug)] = m.id

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

        # Lines resolve by natural key - they hold no external ids (§4).
        self.line_by_key: dict[tuple[int, str], int] = {
            (line.company_id, line.slug): line.id for line in session.scalars(select(ModelLine))
        }
        self.line_by_qid: dict[str, int] = {}  # this run's line resolutions

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

        # Generation slugs per model, for collision detection before INSERT.
        self.generation_by_model_slug: dict[tuple[int, str], int] = {
            (g.model_id, g.slug): g.id for g in session.scalars(select(Generation))
        }

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

        # The subjects: current record per QID within the models sweep, parsed
        # once, in ascending-QID order (the engine's determinism rule).
        self.subjects: dict[str, _Subject] = {}
        for record in _current_records(session, self.source.id, sweep=SWEEP_MARKER):
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
        subject.decided = True
        self.decisions[subject.entity.qid] = {
            "source_id": self.source.id,
            "pass_name": PASS_NAME,
            "external_id": subject.entity.qid,
            "raw_record_id": subject.record.id,
            "rung": rung,
            "method": method,
            "outcome": outcome,
            "detail": detail,
            "reconciler_version": policy.RECONCILER_VERSION,
        }

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

    # --- facts ---------------------------------------------------------------

    def _assert_facts(
        self,
        arc_col: str,
        target,
        coverage: tuple[str, ...],
        facts: dict[str, tuple[str, object]],
        record: RawRecord,
    ) -> None:
        """Upsert this record's assertions for one entity into
        `field_provenance` (the engine's supersede semantics), tombstone
        coverage fields the source went quiet on, and project winners onto
        the entity columns. Single-source projection, like the models pass:
        §6's full ladder becomes exercisable when a second source asserts at
        this level."""
        live: dict[str, FieldProvenance] = {
            row.field_name: row
            for row in self.session.scalars(
                select(FieldProvenance).where(
                    getattr(FieldProvenance, arc_col) == target.id,
                    FieldProvenance.source_id == self.source.id,
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
                    _supersede(
                        self.session,
                        row,
                        {
                            arc_col: target.id,
                            "field_name": field_name,
                            "observed_value": None,
                            "source_id": self.source.id,
                            "raw_record_id": record.id,
                            "scraped_at": record.last_seen_at,
                        },
                    )
                    setattr(target, field_name, None)
                continue
            observed, projected = fact
            values = {
                arc_col: target.id,
                "field_name": field_name,
                "observed_value": observed,
                "source_id": self.source.id,
                "raw_record_id": record.id,
                "scraped_at": record.last_seen_at,
            }
            if row is None:
                self.session.add(FieldProvenance(**values))
                self.stats.assertions_inserted += 1
            elif row.observed_value != observed:
                _supersede(self.session, row, values)
                self.stats.assertions_inserted += 1
                self.stats.assertions_superseded += 1
            setattr(target, field_name, projected)

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

        self._assert_facts("generation_id", generation, GENERATION_COVERAGE, facts, subject.record)

    # --- rungs 1-3: match ----------------------------------------------------

    def _attach_model(self, model_id: int, subject: _Subject) -> None:
        self.session.add(
            ExternalId(model_id=model_id, source_id=self.source.id, external_id=subject.entity.qid)
        )
        self.session.flush()
        self.model_by_qid[subject.entity.qid] = model_id
        self.qid_by_model[model_id] = subject.entity.qid

    def _slug_pair(self, model_id: int) -> str:
        model = self.models[model_id]
        return f"{self.companies[model.company_id].slug}/{model.slug}"

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
        for company_id in subject.held_companies:
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
                        if (entity.qid, self._slug_pair(model_id)) in (
                            policy.WIKIDATA_MODEL_NEGATIVES
                        ):
                            continue
                        if model_id not in hits or rank < hits[model_id][1]:
                            hits[model_id] = (method, rank)
        return hits

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
                company_slug, _, model_slug = curated.partition("/")
                company_id = self.company_by_slug.get(company_slug)
                model_id = (
                    self.model_by_company_slug.get((company_id, model_slug))
                    if company_id is not None
                    else None
                )
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
            if not subject.held_companies:
                self.stats.waits_no_held_maker += 1
                self._decide(
                    subject,
                    "2",
                    None,
                    "waits_no_held_maker",
                    {"makers": list(entity.makers)} if entity.makers else None,
                )
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
        label-twin generations: description and sitelink title."""
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
        self._decide(subject, "3", method, "matched", {"model": self._slug_pair(model_id)})

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
        label-twin cluster flag (picking the nameplate from identical labels
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
        ]
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
            name = self._strip(entity.label, company_id)
            slug = slugify(name, qid)
            key = (company_id, slug)
            line_id = self.line_by_key.get(key)
            if line_id is None:
                line = ModelLine(company_id=company_id, slug=slug, name=name)
                self.session.add(line)
                self.session.flush()
                self.line_by_key[key] = line.id
                line_id = line.id
                self.stats.lines_created += 1
                self._decide(subject, "4", "p179_referenced", "line_created", {"line": slug})
            else:
                # Natural-key reuse: Wikidata's duplicate series entities
                # ("BMW 3 Series" x4) converge on one grouping row - lines
                # hold no external ids, so this is the §4 identity rule, not
                # a merge.
                self.stats.lines_matched += 1
                self._decide(subject, "4", "p179_referenced", "line_matched", {"line": slug})
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

    def _trigram_candidates(self, name: str, company_id: int) -> list[dict]:
        rows = self.session.execute(
            text(
                """SELECT name, slug, round(similarity(name, :n)::numeric, 2) AS sim
                   FROM models WHERE company_id = :c AND similarity(name, :n) > 0.3
                   ORDER BY sim DESC, slug LIMIT 5"""
            ),
            {"n": name, "c": company_id},
        ).all()
        return [{"name": r.name, "slug": r.slug, "similarity": float(r.sim)} for r in rows]

    def _refresh_generation(self, subject: _Subject) -> None:
        generation = self.session.get(Generation, self.generation_by_qid[subject.entity.qid])
        model = self.models[generation.model_id]
        display = (
            self._strip(subject.entity.label, model.company_id)
            if subject.entity.label
            else generation.name
        )
        self._generation_facts(generation, subject, display)
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
        slug = slugify(display, entity.qid)
        occupant = self.generation_by_model_slug.get((model_id, slug))
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
        generation = Generation(model_id=model_id, slug=slug, name=display)
        self.session.add(generation)
        self.session.flush()
        self.generation_by_model_slug[(model_id, slug)] = generation.id
        self.session.add(
            ExternalId(
                generation_id=generation.id,
                source_id=self.source.id,
                external_id=entity.qid,
            )
        )
        self.session.flush()
        self.generation_by_qid[entity.qid] = generation.id
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

    def _structure_phase(self) -> None:
        for qid in sorted(self.subjects, key=lambda q: int(q[1:])):
            subject = self.subjects[qid]
            if qid in self.generation_by_qid and not subject.decided:
                self._refresh_generation(subject)
                continue
            if subject.decided:
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

            # Rung 6: no structural evidence. Flag with candidates only when
            # a held company AND near-misses exist; otherwise wait, unflagged
            # (§3: the tabled expansion's warehouse).
            candidates: list[dict] = []
            if subject.held_companies and entity.label:
                for company_id in subject.held_companies:
                    stripped = self._strip(entity.label, company_id)
                    candidates = self._trigram_candidates(stripped, company_id)
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

    # --- the pass ------------------------------------------------------------

    def _write_decisions(self) -> None:
        rows = [self.decisions[qid] for qid in sorted(self.decisions, key=lambda q: int(q[1:]))]
        for start in range(0, len(rows), _DECISION_CHUNK):
            chunk = rows[start : start + _DECISION_CHUNK]
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
        self._write_decisions()
        for subject in self.subjects.values():
            self._mark(subject.record)
        return self.stats


def run_wikidata_models_pass(session: Session) -> WikidataModelsStats:
    """Run the full Wikidata models-sweep pass (ADR 0012). Commits on success."""
    stats = _WikidataModelsPass(session).run()
    session.commit()
    log.info("wikidata models pass done: %s", stats.summary())
    return stats
