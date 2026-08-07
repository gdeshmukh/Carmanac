"""Fetch the `{{Main}}` targets of undated section-born generations (ADR 0018 §3).

A minted section that carries a `{{Main}}` pointer defers its content to a
per-generation article - the shape where the nameplate page holds no section
infobox and the dates live one page over. The target's section-0 wikitext
lands as `section-main:<QID>#<ordinal>` beside the `article:`/`infobox:`
records; kinds stay readable from the namespaced id. Identity is inherited:
the pointer is structural parsing inside an article already reached through
the model's QID.

Fetch eligibility is deliberately narrow (ADR 0018 §3): minted section-born
generations only, section targets reducing to exactly one distinct title
with no `#` fragment (a fragment points into another article's body). The
grain guards - redirect and the per-generation title convention - apply at
read time in the passes, not here: the landed record is archival either way.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from carmanac.db.models import ExternalId, Generation, RawRecord
from carmanac.ingest.http import PoliteClient
from carmanac.ingest.landing import content_hash, get_source, upsert_raw_records
from carmanac.ingest.wikipedia.infoboxes import API_URL, SOURCE_NAME
from carmanac.reconcile.sources.wikipedia_sections import parse_article

log = logging.getLogger(__name__)

_COMMIT_EVERY = 10


@dataclass(frozen=True)
class SectionMainTarget:
    key: str  # section:<QID>#<ordinal>
    qid: str
    ordinal: int
    title: str  # the {{Main}} target page


@dataclass(frozen=True)
class SectionMainLandResult:
    fetched: int
    inserted: int
    skipped_missing: int


def section_main_targets(session: Session) -> list[SectionMainTarget]:
    """The fetchable targets: minted section-born generations that are
    undated (or already have a section-main record - refresh runs keep it
    current), whose section's `{{Main}}` pointers reduce to exactly one
    distinct fragment-free title."""
    source = get_source(session, SOURCE_NAME)
    sections = session.execute(
        select(ExternalId.external_id, Generation.start_year)
        .join(Generation, Generation.id == ExternalId.generation_id)
        .where(
            ExternalId.source_id == source.id,
            ExternalId.external_id.like("section:%"),
        )
    ).all()
    refreshable = set(
        session.scalars(
            select(RawRecord.external_id).where(
                RawRecord.source_id == source.id,
                RawRecord.external_id.like("section-main:%"),
            )
        )
    )
    wanted: dict[str, dict[int, str]] = {}  # qid -> ordinal -> section key
    for key, start_year in sections:
        if start_year is not None and key.replace("section:", "section-main:") not in refreshable:
            continue
        qid, _, ordinal = key.removeprefix("section:").partition("#")
        wanted.setdefault(qid, {})[int(ordinal)] = key

    targets: list[SectionMainTarget] = []
    if not wanted:
        return targets
    for record in session.scalars(
        select(RawRecord).where(
            RawRecord.source_id == source.id,
            RawRecord.external_id.in_([f"article:{qid}" for qid in sorted(wanted)]),
        )
    ):
        qid = record.payload["qid"]
        article = parse_article(record.payload["title"], record.payload.get("wikitext", ""))
        by_ordinal = {s.ordinal: s for s in article.sections}
        for ordinal, key in sorted(wanted[qid].items()):
            section = by_ordinal.get(ordinal)
            if section is None or not section.main_targets:
                continue
            distinct = {t.replace("_", " ").strip() for t in section.main_targets}
            if len(distinct) != 1:
                log.info("skip %s: several distinct Main targets %s", key, sorted(distinct))
                continue
            (title,) = distinct
            if "#" in title:
                log.info("skip %s: fragment Main target %r", key, title)
                continue
            targets.append(SectionMainTarget(key=key, qid=qid, ordinal=ordinal, title=title))
    targets.sort(key=lambda t: (t.qid, t.ordinal))
    return targets


def land_section_mains(session: Session, *, already_landed: bool = True) -> SectionMainLandResult:
    """Fetch and land one `section-main:<QID>#<ordinal>` record per target.

    `already_landed` skips keys with a landed record - the resumability
    switch. A refresh run passes False; unchanged wikitext lands as a
    hash-rejected no-op.
    """
    source = get_source(session, SOURCE_NAME)
    targets = section_main_targets(session)
    if already_landed:
        landed = set(
            session.scalars(
                select(RawRecord.external_id).where(
                    RawRecord.source_id == source.id,
                    RawRecord.external_id.like("section-main:%"),
                )
            )
        )
        targets = [t for t in targets if f"section-main:{t.qid}#{t.ordinal}" not in landed]

    fetched = inserted = skipped = 0
    pending: list[dict] = []
    with PoliteClient() as client:
        for i, target in enumerate(targets, start=1):
            response = client.request(
                "GET",
                API_URL,
                params={
                    "action": "parse",
                    "page": target.title,
                    "prop": "wikitext|revid",
                    "section": "0",
                    "redirects": "1",
                    "format": "json",
                    "formatversion": "2",
                },
            )
            body = response.json()
            if "error" in body:
                log.warning(
                    "no article for %s (%s): %s",
                    target.key,
                    target.title,
                    body["error"].get("code"),
                )
                skipped += 1
                continue
            parse = body["parse"]
            payload = {
                "qid": target.qid,
                "ordinal": target.ordinal,
                "title": parse.get("title", target.title),
                "requested_title": target.title,
                "revid": parse.get("revid"),
                "wikitext": parse.get("wikitext", ""),
            }
            pending.append(
                {
                    "source_id": source.id,
                    "external_id": f"section-main:{target.qid}#{target.ordinal}",
                    "url": f"https://en.wikipedia.org/wiki/{target.title.replace(' ', '_')}",
                    "payload": payload,
                    "content_hash": content_hash(payload),
                }
            )
            fetched += 1
            if len(pending) >= _COMMIT_EVERY:
                inserted += upsert_raw_records(session, pending)
                session.commit()
                pending = []
                log.info("landed %d/%d section-main targets", i, len(targets))

    if pending:
        inserted += upsert_raw_records(session, pending)
        session.commit()
    return SectionMainLandResult(fetched=fetched, inserted=inserted, skipped_missing=skipped)
