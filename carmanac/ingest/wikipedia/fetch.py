"""Fetch English-Wikipedia wikitext into raw (ADR 0017/0018).

Two record kinds, told apart by the namespaced external id:

- `article:<QID>` — the full article a target QID's sitelink names. The
  per-generation sections live beyond section 0, so the whole page lands
  untransformed: a revision is not re-fetchable once the page moves on.
- `section-main:<QID>#<ordinal>` — section-0 of the `{{Main}}` target a
  minted, undated section-born generation defers to (ADR 0018 §3; the
  eligibility gate is unchanged).

Article targets are QIDs the passes already trust — identity is inherited
from Wikidata sitelinks, never inferred by name matching (ADR 0017 §1):
1:1-attached to one of our models or generations, routed by
`SECTION_ARTICLE_MODELS`, or members of an open mint twin group (their own
articles carry the era spans the twins ruling resolves by). `--wider` adds
the archive layers — P179 line-case entities and unattached entities under
companies that hold models — landed raw, asserted by no pass today.

`infobox:<QID>` section-0 records are archival: the full article
contains section 0, so this module lands none.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import unquote

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from carmanac.db.models import ExternalId, Generation, RawRecord
from carmanac.ingest.http import PoliteClient
from carmanac.ingest.landing import LandResult, content_hash, get_source, upsert_raw_records
from carmanac.reconcile import policy
from carmanac.reconcile.sources.wikipedia_sections import parse_article

log = logging.getLogger(__name__)

SOURCE_NAME = "Wikipedia (English)"
API_URL = "https://en.wikipedia.org/w/api.php"

# A long polite fetch must survive interruption; committed batches resume.
_COMMIT_EVERY = 25

# Contested twins ride their own open flags, so the target set follows the
# queue with no registry copy to go stale. Under :wider, a GROUP_CONCAT'd
# multi-maker value resolves to no company and stays out on purpose — the
# JV question is open, and the archive layer must not answer it by accident.
_TARGETS_SQL = """
    SELECT rr.external_id AS qid,
           rr.payload->'article'->>'value' AS article_url
    FROM raw_scrape.raw_records rr
    JOIN sources s ON s.id = rr.source_id AND s.name = 'Wikidata'
    WHERE rr.payload->>'sweep' = 'models'
      AND coalesce(rr.payload->'article'->>'value', '') <> ''
      AND (
        EXISTS (
            SELECT 1 FROM external_ids ei
            WHERE ei.external_id = rr.external_id
              AND ei.source_id = rr.source_id
              AND (ei.model_id IS NOT NULL OR ei.generation_id IS NOT NULL)
        )
        OR rr.external_id IN :curated
        OR EXISTS (
            SELECT 1 FROM reconciliation_flags rf
            WHERE rf.raw_record_id = rr.id
              AND rf.status = 'open'
              AND rf.detail->>'reason' IN ('mint_label_twins', 'mint_slug_occupied')
        )
        OR (:wider AND (
            coalesce(rr.payload->'seriesOf'->>'value', '') <> ''
            OR EXISTS (
                SELECT 1 FROM external_ids em
                JOIN models m ON m.company_id = em.company_id
                WHERE em.source_id = rr.source_id
                  AND em.external_id = replace(rr.payload->'makers'->>'value',
                                               'http://www.wikidata.org/entity/', '')
            )
        ))
      )
    ORDER BY rr.external_id
"""


def article_title(article_url: str) -> str:
    """`https://en.wikipedia.org/wiki/BMW_3_Series_(E30)` -> the page title."""
    return unquote(article_url.rsplit("/wiki/", 1)[-1])


def _fetch_pages(session: Session, source_id: int, requests: list[dict]) -> LandResult:
    """One `parse` API call per request dict (title, external_id, url, qid,
    optional params/extra), batch-committed. A missing page logs and skips:
    the sitelink said it exists, the wiki disagrees."""
    fetched = inserted = skipped = 0
    pending: list[dict] = []
    with PoliteClient() as client:
        for i, req in enumerate(requests, start=1):
            response = client.request(
                "GET",
                API_URL,
                params={
                    "action": "parse",
                    "page": req["title"],
                    "prop": "wikitext|revid",
                    "redirects": "1",
                    "format": "json",
                    "formatversion": "2",
                    **req.get("params", {}),
                },
            )
            body = response.json()
            if "error" in body:
                log.warning(
                    "no article for %s (%s): %s",
                    req["external_id"],
                    req["title"],
                    body["error"].get("code"),
                )
                skipped += 1
                continue
            parse = body["parse"]
            payload = {
                "qid": req["qid"],
                "title": parse.get("title", req["title"]),
                "requested_title": req["title"],
                "revid": parse.get("revid"),
                "wikitext": parse.get("wikitext", ""),
                **req.get("extra", {}),
            }
            pending.append(
                {
                    "source_id": source_id,
                    "external_id": req["external_id"],
                    "url": req["url"],
                    "payload": payload,
                    "content_hash": content_hash(payload),
                }
            )
            fetched += 1
            if len(pending) >= _COMMIT_EVERY:
                inserted += upsert_raw_records(session, pending)
                session.commit()
                pending = []
                log.info("landed %d/%d pages", i, len(requests))

    if pending:
        inserted += upsert_raw_records(session, pending)
        session.commit()
    return LandResult(fetched=fetched, inserted=inserted, skipped_missing=skipped)


def land_articles(
    session: Session, *, already_landed: bool = True, wider: bool = False
) -> LandResult:
    """Fetch and land one `article:<QID>` record per target page."""
    source = get_source(session, SOURCE_NAME)
    rows = session.execute(
        text(_TARGETS_SQL).bindparams(bindparam("curated", expanding=True)),
        {
            # IN () is a syntax error; the impossible sentinel keeps the query
            # valid when the registry is empty.
            "curated": sorted(policy.SECTION_ARTICLE_MODELS) or ["Q0"],
            "wider": wider,
        },
    ).all()
    requests = [
        {
            "qid": row.qid,
            "title": article_title(row.article_url),
            "external_id": f"article:{row.qid}",
            "url": row.article_url,
        }
        for row in rows
    ]

    if already_landed:
        landed = {
            row[0]
            for row in session.execute(
                text(
                    "SELECT external_id FROM raw_scrape.raw_records "
                    "WHERE source_id = :sid AND external_id LIKE 'article:%'"
                ),
                {"sid": source.id},
            )
        }
        requests = [r for r in requests if r["external_id"] not in landed]

    return _fetch_pages(session, source.id, requests)


@dataclass(frozen=True)
class SectionMainTarget:
    key: str  # section:<QID>#<ordinal>
    qid: str
    ordinal: int
    title: str  # the {{Main}} target page


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


def land_section_mains(session: Session, *, already_landed: bool = True) -> LandResult:
    """Fetch and land one `section-main:<QID>#<ordinal>` record per target."""
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

    requests = [
        {
            "qid": t.qid,
            "title": t.title,
            "external_id": f"section-main:{t.qid}#{t.ordinal}",
            "url": f"https://en.wikipedia.org/wiki/{t.title.replace(' ', '_')}",
            "extra": {"ordinal": t.ordinal},
            # Section 0 only: the target's own generations are not this
            # generation's evidence.
            "params": {"section": "0"},
        }
        for t in targets
    ]
    return _fetch_pages(session, source.id, requests)


def land_family_articles(session: Session, *, already_landed: bool = True) -> LandResult:
    """Fetch each minted powertrain family's own article (ADR 0020
    Decision 3): the entity's name is the page, the external id names the
    record - `family:<engine-article:key>` keeps the kind readable. The
    per-variant sections are where displacement lives at the grain a
    configuration wants."""
    source = get_source(session, SOURCE_NAME)
    rows = session.execute(
        text(
            """SELECT ei.external_id AS key, coalesce(e.name, t.name) AS title
               FROM external_ids ei
               LEFT JOIN engines e ON e.id = ei.engine_id
               LEFT JOIN transmissions t ON t.id = ei.transmission_id
               WHERE ei.source_id = :sid
                 AND (ei.external_id LIKE 'engine-article:%'
                      OR ei.external_id LIKE 'transmission-article:%')
                 AND ei.external_id NOT LIKE '%#%'"""
        ),
        {"sid": source.id},
    ).all()
    requests = [
        {
            "qid": row.key,
            "title": row.title,
            "external_id": f"family:{row.key}",
            "url": f"https://en.wikipedia.org/wiki/{row.title.replace(' ', '_')}",
        }
        for row in rows
        if row.title
    ]
    if already_landed:
        landed = set(
            session.scalars(
                select(RawRecord.external_id).where(
                    RawRecord.source_id == source.id,
                    RawRecord.external_id.like("family:%"),
                )
            )
        )
        requests = [r for r in requests if r["external_id"] not in landed]
    return _fetch_pages(session, source.id, requests)


def land_wikipedia(
    session: Session, *, already_landed: bool = True, wider: bool = False
) -> LandResult:
    """Articles, then the `{{Main}}` targets already-minted generations
    defer to, then the minted powertrain families' own pages; targets
    minted from a fresh batch surface on the next invocation (the
    fetch -> pass -> fetch cadence of the fill sequence)."""
    articles = land_articles(session, already_landed=already_landed, wider=wider)
    mains = land_section_mains(session, already_landed=already_landed)
    families = land_family_articles(session, already_landed=already_landed)
    return LandResult(
        fetched=articles.fetched + mains.fetched + families.fetched,
        inserted=articles.inserted + mains.inserted + families.inserted,
        skipped_missing=articles.skipped_missing + mains.skipped_missing + families.skipped_missing,
    )


if __name__ == "__main__":
    import sys

    from carmanac.runner import run

    run(
        land_wikipedia,
        already_landed="--refresh" not in sys.argv[1:],
        wider="--wider" in sys.argv[1:],
    )
