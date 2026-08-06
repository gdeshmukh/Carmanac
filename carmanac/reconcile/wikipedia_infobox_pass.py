"""The Wikipedia infobox pass (ADR 0017 §2): generation time and codes.

Consumes the landed `infobox:<QID>` records and asserts onto the generation
the QID is already attached to:

- `start_year` / `end_year` from the infobox `production` span - the global
  truth the columns have always meant (open end = still in production).
- `chassis_codes` from the article title's parenthetical, through the same
  strict extractor the label pass uses - Wikipedia titles its generation
  pages by internal code (`BMW 3 Series (E30)`) in a convention that holds
  across marques, so the title is an assertion, not decoration.

The `model_years` span is deliberately NOT stored (no column for a
US-specific reading); the placement pass parses it from raw at decision
time. Identity is never touched: a record whose QID is attached to nothing
waits - the line-case articles are archival until line membership
materializes their generations.

Tier 2 writes facts directly - queues are for identity ambiguity, not source
rank (ADR 0017 §1). Precedence has teeth on the other side:
the wd-models pass skips projecting fields this pass asserts (its
label-derived values stay in `field_provenance`, outranked on the column).
A span that does not reduce to exactly one range flags `implausible_value`
and asserts nothing - flag, never guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from carmanac.db.models import ExternalId, Generation, ReconciliationFlag, Source
from carmanac.ingest.landing import get_source
from carmanac.ingest.wikipedia import SOURCE_NAME
from carmanac.reconcile.bookkeeping import DecisionLog, mark_reconciled
from carmanac.reconcile.engine import assert_field_facts, current_records
from carmanac.reconcile.sources.wikidata_models import extract_chassis_codes
from carmanac.reconcile.sources.wikipedia_infobox import parse_infobox, title_code_tokens

log = logging.getLogger(__name__)

PASS_NAME = "wikipedia_infobox"

COVERAGE: tuple[str, ...] = ("start_year", "end_year", "chassis_codes")


@dataclass
class WikipediaInfoboxStats:
    processed: int = 0
    generations_timed: int = 0
    waits_unattached: int = 0
    redirected: int = 0
    no_facts_found: int = 0
    assertions_inserted: int = 0
    assertions_superseded: int = 0
    flags_opened: int = 0

    def summary(self) -> str:
        return (
            f"processed={self.processed} timed={self.generations_timed} "
            f"waits_unattached={self.waits_unattached} redirected={self.redirected} "
            f"no_facts={self.no_facts_found} | "
            f"assertions={self.assertions_inserted} "
            f"(superseded={self.assertions_superseded}) flags={self.flags_opened}"
        )


def _same_subject(requested: str, resolved: str) -> bool:
    """Underscore/space and case wobble is a rename; anything more is a
    REDIRECT that may have changed the article's grain - the 'Honda Civic
    Hybrid' sitelink resolves to the whole-nameplate 'Honda Civic' page,
    whose 1972-present span must never land on one generation."""
    normalize = lambda t: t.replace("_", " ").strip().casefold()  # noqa: E731
    return normalize(requested) == normalize(resolved)


def run_wikipedia_infobox_pass(session: Session) -> WikipediaInfoboxStats:
    stats = WikipediaInfoboxStats()
    source = get_source(session, SOURCE_NAME)
    wikidata_id = session.scalar(select(Source.id).where(Source.name == "Wikidata"))
    decisions = DecisionLog(session, source.id, PASS_NAME)

    # QID -> generation, via the Wikidata attachment (identity is inherited;
    # this pass never matches by name).
    generation_by_qid: dict[str, int] = {
        qid: generation_id
        for qid, generation_id in session.execute(
            select(ExternalId.external_id, ExternalId.generation_id).where(
                ExternalId.source_id == wikidata_id,
                ExternalId.generation_id.isnot(None),
            )
        )
    }

    # Open parse flags per (generation, field), so re-runs do not re-ask.
    open_flags: set[tuple[int, str]] = {
        (flag.generation_id, flag.field_name)
        for flag in session.scalars(
            select(ReconciliationFlag).where(
                ReconciliationFlag.kind == "implausible_value",
                ReconciliationFlag.status == "open",
                ReconciliationFlag.source_id == source.id,
                ReconciliationFlag.generation_id.isnot(None),
            )
        )
    }

    for record in current_records(session, source.id):
        stats.processed += 1
        qid = record.payload["qid"]
        generation_id = generation_by_qid.get(qid)
        if generation_id is None:
            stats.waits_unattached += 1
            decisions.record(record, "waits_unattached_qid")
            mark_reconciled(session, record)
            continue
        generation = session.get(Generation, generation_id)

        if not _same_subject(record.payload.get("requested_title", ""), record.payload["title"]):
            # Empty facts still run the tombstone: a span this record asserted
            # before the redirect was recognised heals back to NULL.
            inserted, superseded = assert_field_facts(
                session,
                arc_col="generation_id",
                entity=generation,
                coverage=COVERAGE,
                facts={},
                source_id=source.id,
                record=record,
            )
            stats.assertions_inserted += inserted
            stats.assertions_superseded += superseded
            stats.redirected += 1
            decisions.record(
                record,
                "waits_redirected_article",
                detail={
                    "requested": record.payload.get("requested_title"),
                    "resolved": record.payload["title"],
                },
            )
            mark_reconciled(session, record)
            continue

        parsed = parse_infobox(record.payload["title"], record.payload.get("wikitext", ""))
        facts: dict[str, tuple[str, object]] = {}
        if parsed.production is not None:
            observed = f"{parsed.production.start}–{parsed.production.end or 'present'}"
            facts["start_year"] = (observed, parsed.production.start)
            facts["end_year"] = (observed, parsed.production.end)

        code_source = title_code_tokens(parsed.title)
        codes, _ambiguous = extract_chassis_codes(parsed.title, (), None)
        if code_source and codes:
            facts["chassis_codes"] = ("|".join(codes), codes)

        for field_name, reason, raw in parsed.failures:
            key = (generation.id, field_name)
            if key in open_flags:
                continue
            session.add(
                ReconciliationFlag(
                    kind="implausible_value",
                    generation_id=generation.id,
                    field_name=field_name,
                    detail={"reason": reason, "raw": raw, "title": parsed.title, "qid": qid},
                    source_id=source.id,
                    raw_record_id=record.id,
                )
            )
            open_flags.add(key)
            stats.flags_opened += 1

        inserted, superseded = assert_field_facts(
            session,
            arc_col="generation_id",
            entity=generation,
            coverage=COVERAGE,
            facts=facts,
            source_id=source.id,
            record=record,
        )
        stats.assertions_inserted += inserted
        stats.assertions_superseded += superseded

        if facts:
            stats.generations_timed += 1
            decisions.record(
                record,
                "facts_asserted",
                method="infobox_parse",
                detail={"fields": sorted(facts)},
            )
        else:
            stats.no_facts_found += 1
            decisions.record(record, "no_facts_found")
        mark_reconciled(session, record)

    decisions.flush()
    session.commit()
    log.info("wikipedia infobox pass done: %s", stats.summary())
    return stats
